"""Import, select and process independent chapter ranges in an existing project."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

from smart_audiobook.application import _build_speaker_service
from smart_audiobook.book_manifest import book_statistics, load_chapter, select_range
from smart_audiobook.book_processor import AnalyzedChapter, BookAnalysis
from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.document_loaders import load_document
from smart_audiobook.models import ChapterStatus
from smart_audiobook.review_store import ReviewProject, ReviewStateError
from smart_audiobook.segmenter import segment_text
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.voice_assignment import assign_profile_voices
from smart_audiobook.web_security import sanitize_upload_name


def chapter_state(segments) -> ChapterStatus:
    return ChapterStatus.REVIEW_REQUIRED if any(s.review_needed or s.speaker == "Unknown" for s in segments) else ChapterStatus.READY_FOR_AUDIO


class BookWorkflow:
    """Use the review service's store, registry and providers without UI concerns."""

    def __init__(self, review):
        self.review = review
        self.store = review.store

    def import_book(self, source_path: Path, source_name: str | None = None) -> ReviewProject:
        safe_name = sanitize_upload_name(source_name or source_path.name)
        identity, directory = self.store.create_workspace()
        source = directory / "source" / safe_name
        try:
            shutil.copy2(source_path, source)
            document = load_document(source)
            try:
                voices = tuple(self.review.tts_provider.list_voices())
            except SpeechGenerationError:
                voices = ()
            registry = CharacterRegistry()
            project = ReviewProject(identity, safe_name, BookAnalysis(document, (), registry.characters, registry),
                voices, assign_profile_voices(registry, voices), imported=True)
            self.store.save(project)
            return project
        except Exception:
            # Only the just-created UUID workspace can be removed here.
            shutil.rmtree(directory)
            raise

    def select(self, identity: str, numbers: set[int], narrate: set[int] | None = None) -> ReviewProject:
        project = self.review.load(identity)
        available = {ch.number for ch in project.analysis.document.chapters}
        if not numbers.issubset(available) or (narrate is not None and not narrate.issubset(available)):
            raise ReviewStateError("Capítulo no válido.")
        chapters = tuple(replace(ch, selected_for_processing=ch.number in numbers,
            narrate=ch.narrate if narrate is None else ch.number in narrate) for ch in project.analysis.document.chapters)
        project.analysis = replace(project.analysis, document=replace(project.analysis.document, chapters=chapters))
        self.store.save(project)
        return project

    def select_expression(self, identity: str, expression: str) -> ReviewProject:
        project = self.review.load(identity)
        return self.select(identity, select_range(expression, {ch.number for ch in project.analysis.document.chapters}))

    def preview(self, identity: str, number: int):
        project = self.review.load(identity)
        chapter = next((ch for ch in project.analysis.document.chapters if ch.number == number), None)
        if chapter is None:
            raise ReviewStateError("Capítulo no válido.")
        try:
            return load_chapter(chapter)
        except (OSError, ValueError, KeyError) as error:
            raise ReviewStateError("No se pudo cargar el texto del capítulo.") from error

    def statistics(self, identity: str, detailed: bool = False):
        project = self.review.load(identity)
        from smart_audiobook.tts_config import TTSConfig
        config = TTSConfig.from_environment()
        return book_statistics(project.analysis.document, detailed=detailed,
            provider=config.provider, device=config.piper_device)

    def analyze(self, identity: str, numbers: set[int] | None = None, *,
                reanalyze: bool = False, use_llm: bool = True, speaker_service=None) -> ReviewProject:
        project = self.review.load(identity)
        available = {ch.number for ch in project.analysis.document.chapters}
        if numbers is None:
            numbers = {ch.number for ch in project.analysis.document.chapters if ch.selected_for_processing and ch.narrate}
        if not numbers or not numbers.issubset(available):
            raise ReviewStateError("Selecciona al menos un capítulo válido.")
        previous = {ch.number: ch for ch in project.analysis.chapters}
        registry = CharacterRegistry.from_list(project.analysis.registry.to_list())
        for profile in registry.profiles:
            profile.activity = []
        factory = getattr(self.review.application, "speaker_service", _build_speaker_service)
        service = speaker_service or factory(use_llm)
        saved_before = service.llm_calls_saved
        initial_saved = project.analysis.llm_calls_saved
        initial_diagnostics = dict(project.analysis.resolution_diagnostics)
        service.resolution_cache.update(project.analysis.resolution_cache)
        chapters = list(project.analysis.document.chapters)
        for index, source in enumerate(chapters):
            old = previous.get(source.number)
            if source.number not in numbers or (old and not reanalyze):
                if old:
                    for segment in old.segments:
                        registry.observe(segment)
                continue
            chapters[index] = replace(source, status=ChapterStatus.ANALYZING)
            project.analysis = replace(project.analysis, document=replace(project.analysis.document, chapters=tuple(chapters)))
            self.store.save(project)
            try:
                if old:
                    indexed = old.segments  # stable IDs and manual overrides survive
                else:
                    content = load_chapter(source)
                    segments = segment_text(content.text)
                    emphasis_by_text = {}
                    for block in content.blocks:
                        if block.emphasis:
                            for fragment in segment_text(block.text):
                                emphasis_by_text.setdefault(fragment.text, set()).update(block.emphasis)
                    indexed = tuple(replace(s, id=f"{source.id}_seg_{offset:06d}", chapter=source.number,
                        order=source.number * 1_000_000_000 + offset,
                        emphasis=tuple(sorted(emphasis_by_text.get(s.text, ()))))
                        for offset, s in enumerate(segments, start=1))
                analysis = service.identify(indexed, registry)
                previous[source.number] = AnalyzedChapter(source.number, source.title, analysis.segments)
                chapters[index] = replace(source, status=chapter_state(analysis.segments), consistency_warning=None)
                if old and [(s.speaker, s.new_character_candidate) for s in old.segments] != [(s.speaker, s.new_character_candidate) for s in analysis.segments]:
                    self._warn_later(chapters, source.number)
                project.output = None
            except Exception:
                chapters[index] = replace(source, status=ChapterStatus.FAILED)
                self._finish(project, chapters, previous, registry, service, saved_before, initial_saved)
                self._diagnostics(project, initial_diagnostics, service)
                self.store.save(project)
                raise
            self._finish(project, chapters, previous, registry, service, saved_before, initial_saved)
            self._diagnostics(project, initial_diagnostics, service)
            self.store.save(project)
        return project

    @staticmethod
    def _diagnostics(project, initial, service):
        project.analysis = replace(project.analysis, resolution_diagnostics={
            key: initial.get(key, 0) + value for key, value in service.diagnostics.items()})

    @staticmethod
    def _finish(project, chapters, previous, registry, service, saved_before, initial_saved):
        analyzed = tuple(previous[key] for key in sorted(previous))
        registry.rebuild_statistics(s for ch in analyzed for s in ch.segments)
        project.analysis = replace(project.analysis, document=replace(project.analysis.document, chapters=tuple(chapters)),
            chapters=analyzed, registry=registry, characters=registry.characters,
            resolution_cache=service.resolution_cache,
            llm_calls_saved=initial_saved + service.llm_calls_saved - saved_before)
        project.voice_assignments = assign_profile_voices(registry, project.voices, project.voice_assignments)
        if any(s.speaker == "Unknown" for ch in analyzed for s in ch.segments) and project.voices:
            project.voice_assignments.setdefault("Unknown", project.voices[-1].id)

    @staticmethod
    def _warn_later(chapters, number):
        for i, chapter in enumerate(chapters):
            if chapter.number > number and chapter.status != ChapterStatus.NOT_ANALYZED:
                chapters[i] = replace(chapter, consistency_warning=f"Revisar continuidad: cambió el conocimiento del capítulo {number}.")

    def clear_chapter_cache(self, identity: str, number: int) -> ReviewProject:
        """Drop this chapter's cache namespace only; other chapters remain resumable."""
        project = self.review.load(identity)
        chapter = next((ch for ch in project.analysis.document.chapters if ch.number == number), None)
        if chapter is None:
            raise ReviewStateError("Capítulo no válido.")
        root = (self.store.directory(identity) / "cache" / "chapters").resolve()
        target = (root / chapter.id).resolve()
        if not target.is_relative_to(root) or target == root:
            raise ReviewStateError("Ruta de caché no válida.")
        if target.is_dir():
            shutil.rmtree(target)
        state_path = self.store.directory(identity) / "generation_state.json"
        if state_path.is_file():
            from smart_audiobook.book_manifest import write_json
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state.get("chapters", {}).pop(str(number), None)
            write_json(state_path, state)
        project.analysis = replace(project.analysis, document=replace(project.analysis.document,
            chapters=tuple(replace(ch, status=ChapterStatus.READY_FOR_AUDIO) if ch.number == number else ch
                           for ch in project.analysis.document.chapters)))
        project.output = None
        self.store.save(project)
        return project
