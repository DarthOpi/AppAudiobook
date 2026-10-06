"""Application service for reviewing speakers and voices before TTS."""

import inspect
import json
import logging
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from smart_audiobook.application import AudiobookApplicationService, ProcessingResult
from smart_audiobook.audio_cache import AudioCache
from smart_audiobook.book_processor import AnalyzedChapter, BookAnalysis
from smart_audiobook.characters import normalize_character_name, CharacterRegistry
from smart_audiobook.models import ChapterStatus, NARRATOR, UNKNOWN_SPEAKER, TextSegment
from smart_audiobook.review_store import (
    ReviewProject,
    ReviewProjectStore,
    ReviewStateError,
)
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import (
    TTSProvider,
    provider_capabilities,
    provider_id,
    provider_model,
)
from smart_audiobook.voice_assignment import assign_voices, assign_profile_voices

PREVIEW_TEXT = "Hola. Esta es una prueba de mi voz para el audiolibro."
LOGGER = logging.getLogger(__name__)


class AnalysisApplication(Protocol):
    def analyze(self, source_path: Path, use_llm: bool = True) -> BookAnalysis:
        """Analyze one source document."""

    def generate(
        self,
        analysis: BookAnalysis,
        output_root: Path,
        voices_by_speaker: dict[str, str] | None = None,
        tts_provider: TTSProvider | None = None,
    ) -> ProcessingResult:
        """Generate audio from reviewed state."""


class ReviewService:
    """Coordinate persisted analysis edits, previews, and final generation."""

    def __init__(
        self,
        store: ReviewProjectStore,
        tts_provider: TTSProvider,
        application: AnalysisApplication | None = None,
    ) -> None:
        self.store = store
        self.tts_provider = tts_provider
        self.application = application or AudiobookApplicationService()

    @property
    def provider_status(self) -> dict[str, object]:
        return {
            "id": provider_id(self.tts_provider),
            "model": provider_model(self.tts_provider),
            "ready": True,
            "capabilities": provider_capabilities(self.tts_provider),
        }

    def start_analysis(self, source_path: Path, source_name: str) -> ReviewProject:
        processing_id, directory = self.store.create_workspace()
        stored_source = directory / "source" / source_name
        try:
            shutil.copy2(source_path, stored_source)
            analysis = self.application.analyze(stored_source)
            try:
                voices = tuple(self.tts_provider.list_voices())
            except SpeechGenerationError as error:
                LOGGER.warning("Voice catalog is unavailable: %s", error)
                voices = ()
            analysis.registry.rebuild_statistics(_all_segments(analysis))
            assignments = assign_profile_voices(analysis.registry, voices)
            if any(s.speaker == UNKNOWN_SPEAKER for s in _all_segments(analysis)) and voices:
                assignments[UNKNOWN_SPEAKER] = voices[-1].id
            project = ReviewProject(
                processing_id=processing_id,
                source_name=source_name,
                analysis=analysis,
                voices=voices,
                voice_assignments=assignments,
            )
            self.store.save(project)
            return project
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def load(self, processing_id: str) -> ReviewProject:
        return self.store.load(processing_id)

    def _editable_project(self, processing_id: str) -> ReviewProject:
        project = self.load(processing_id)
        if any(ch.status in {ChapterStatus.ANALYZING, ChapterStatus.GENERATING_AUDIO}
               for ch in project.analysis.document.chapters):
            raise ReviewStateError("Espera a que termine el procesamiento antes de editar el proyecto.")
        return project

    @property
    def books(self):
        from smart_audiobook.book_workflow import BookWorkflow
        return BookWorkflow(self)

    def update_speakers(
        self,
        processing_id: str,
        updates: dict[str, str],
    ) -> ReviewProject:
        project = self._editable_project(processing_id)
        known = list(project.characters)
        changed: dict[str, str] = {}
        for segment in project.dialogues:
            requested = updates.get(segment.id)
            if requested is None:
                continue
            canonical = _canonical_name(requested, known)
            profile = project.analysis.registry.find(canonical)
            if profile:
                profile.pending = False
                canonical = profile.canonical_name
            elif canonical != UNKNOWN_SPEAKER:
                canonical = project.analysis.registry.register(canonical, segment.chapter)
            if canonical not in known:
                known.append(canonical)
            changed[segment.id] = canonical
        project.analysis = _replace_segment_speakers(project.analysis, changed)
        project.analysis = _with_rebuilt_characters(project.analysis, known)
        _complete_voice_assignments(project)
        project.output = None
        self.store.save(project)
        return project

    def rename_character(
        self,
        processing_id: str,
        current_name: str,
        new_name: str,
    ) -> ReviewProject:
        project = self._editable_project(processing_id)
        if current_name in {NARRATOR, UNKNOWN_SPEAKER}:
            raise ReviewStateError("Narrator y Unknown no se pueden renombrar.")
        current = _find_character(project.characters, current_name)
        target = _canonical_name(new_name, list(project.characters), exclude=current)
        try:
            project.analysis.registry.rename(current, target)
        except ValueError as error:
            raise ReviewStateError(str(error)) from error
        project.analysis = _replace_character(project.analysis, current, target)
        project.analysis = _with_rebuilt_characters(project.analysis)
        _move_voice_assignment(project, current, target)
        project.output = None
        self.store.save(project)
        return project

    def merge_characters(
        self,
        processing_id: str,
        source_name: str,
        target_name: str,
        known_from: int = 1,
    ) -> ReviewProject:
        project = self._editable_project(processing_id)
        if source_name in {NARRATOR, UNKNOWN_SPEAKER}:
            raise ReviewStateError("Ese personaje no se puede fusionar.")
        source = _find_character(project.characters, source_name)
        target = _find_character(project.characters, target_name)
        if normalize_character_name(source) == normalize_character_name(target):
            raise ReviewStateError("Selecciona dos personajes diferentes.")
        try:
            project.analysis.registry.merge(source, target, known_from)
        except ValueError as error:
            raise ReviewStateError(str(error)) from error
        project.analysis = _replace_character(project.analysis, source, target)
        project.analysis = _with_rebuilt_characters(project.analysis)
        _move_voice_assignment(project, source, target)
        project.output = None
        self.store.save(project)
        return project

    def select_voice(
        self,
        processing_id: str,
        character: str,
        voice_id: str,
    ) -> ReviewProject:
        project = self._editable_project(processing_id)
        canonical = _find_character(project.characters, character)
        if voice_id not in {voice.id for voice in project.voices}:
            raise ReviewStateError("La voz seleccionada no está disponible.")
        project.voice_assignments[canonical] = voice_id
        profile = project.analysis.registry.find(canonical)
        if profile:
            profile.voice_id, profile.voice_manual = voice_id, True
            profile.voice_strategy = "dedicated"
        project.output = None
        self.store.save(project)
        return project

    def edit_alias(self, processing_id: str, character: str, alias: str,
                   known_from: int = 1, remove: bool = False) -> ReviewProject:
        project = self._editable_project(processing_id)
        try:
            if remove:
                project.analysis.registry.remove_alias(character, alias)
            else:
                project.analysis.registry.add_alias(character, alias, known_from)
        except ValueError as error:
            raise ReviewStateError(str(error)) from error
        if project.imported:
            project.analysis = replace(project.analysis, document=replace(project.analysis.document,
                chapters=tuple(replace(ch, consistency_warning=f"Revisar aliases: cambió el conocimiento desde el capítulo {known_from}.")
                    if ch.number >= known_from and ch.status != ChapterStatus.NOT_ANALYZED else ch
                    for ch in project.analysis.document.chapters)))
        self.store.save(project)
        return project

    def reanalyze(self, processing_id: str, selected_ids: set[str] | None = None,
                  low_confidence: bool = False, speaker_service=None) -> ReviewProject:
        from smart_audiobook.application import _build_speaker_service
        project = self._editable_project(processing_id)
        ids = selected_ids if selected_ids is not None else {
            s.id for s in project.dialogues if s.speaker == UNKNOWN_SPEAKER
            or (low_confidence and s.review_needed)
        }
        valid = {s.id for s in project.dialogues}
        if not ids.issubset(valid):
            raise ReviewStateError("Segmento no válido.")
        service = speaker_service or _build_speaker_service(True)
        saved_before = service.llm_calls_saved
        service.resolution_cache.update(project.analysis.resolution_cache)
        registry = CharacterRegistry.from_list(project.analysis.registry.to_list())
        for p in registry.profiles:
            p.activity = []  # reconstruct only past activity while walking the book
        chapters = tuple(replace(ch, segments=service.identify(ch.segments, registry, ids).segments)
                         for ch in project.analysis.chapters)
        registry.rebuild_statistics(s for ch in chapters for s in ch.segments)
        project.analysis = replace(project.analysis, chapters=chapters, registry=registry,
            characters=registry.characters, resolution_cache=service.resolution_cache,
            llm_calls_saved=project.analysis.llm_calls_saved + service.llm_calls_saved - saved_before)
        _complete_voice_assignments(project)
        project.output = None
        self.store.save(project)
        return project

    def enrich_character(self, processing_id: str, character: str, chapter: int,
                          resolver=None) -> ReviewProject:
        """Explicit, optional enrichment; suggestions never become aliases automatically."""
        from smart_audiobook.gemini_resolver import build_gemini_resolver_from_environment
        project = self._editable_project(processing_id)
        profile = project.analysis.registry.require(character)
        evidence = [s.text[:300] for s in project.segments if s.chapter <= chapter
                    and (s.speaker == profile.canonical_name or profile.canonical_name in
                         project.analysis.registry.mentions(s.text, s.chapter))][-8:]
        if len(evidence) < 3:
            raise ReviewStateError("Se necesitan al menos tres fragmentos de evidencia.")
        resolver = resolver or build_gemini_resolver_from_environment()
        if resolver is None or not hasattr(resolver, "enrich"):
            raise ReviewStateError("LLM no configurado para enriquecimiento.")
        data = resolver.enrich(profile.canonical_name, evidence, chapter)
        if not data:
            raise ReviewStateError("No se recibió un perfil válido.")
        profile.description = data["description"]
        profile.personality_traits = data["personality_traits"]
        profile.knowledge["description"] = chapter
        profile.knowledge["personality_traits"] = chapter
        self.store.save(project)
        return project

    def preview_voice(self, processing_id: str, voice_id: str) -> Path:
        project = self.load(processing_id)
        if voice_id not in {voice.id for voice in project.voices}:
            raise ReviewStateError("La voz seleccionada no está disponible.")
        cache = AudioCache(self.store.directory(processing_id) / "cache" / "audio")
        path, _hit = cache.synthesize(self.tts_provider, PREVIEW_TEXT, voice_id)
        return path

    def generate(self, processing_id: str, chapter_numbers: set[int] | None = None,
                 force_regenerate: bool = False) -> ReviewProject:
        project = self.load(processing_id)
        analysis = project.analysis
        if project.imported:
            selected = chapter_numbers if chapter_numbers is not None else {
                ch.number for ch in analysis.document.chapters if ch.selected_for_processing and ch.narrate}
            analyzed = {ch.number for ch in analysis.chapters}
            if not selected or not selected.issubset(analyzed):
                raise ReviewStateError("Analiza primero todos los capítulos seleccionados.")
            analysis = replace(analysis, chapters=tuple(ch for ch in analysis.chapters if ch.number in selected))
        else:
            selected = {ch.number for ch in analysis.chapters}
        available_voice_ids = {voice.id for voice in project.voices}
        if not available_voice_ids:
            raise SpeechGenerationError("No se encontraron voces disponibles.")
        used_speakers = {segment.speaker for ch in analysis.chapters for segment in ch.segments}
        missing = sorted(
            speaker
            for speaker in used_speakers
            if project.voice_assignments.get(speaker) not in available_voice_ids
        )
        if NARRATOR not in project.voice_assignments:
            raise SpeechGenerationError("Narrator debe tener una voz asignada.")
        if missing:
            raise SpeechGenerationError(
                "Falta una voz válida para: " + ", ".join(missing)
            )
        directory = self.store.directory(processing_id)
        cancel_marker = directory / "cancel.requested"
        cancel_marker.unlink(missing_ok=True)
        kwargs: dict[str, object] = {
            "voices_by_speaker": project.voice_assignments,
            "tts_provider": self.tts_provider,
        }
        signature = inspect.signature(self.application.generate)
        supports_options = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        if supports_options:
            kwargs.update(
                cache_directory=directory / "cache" / "audio",
                state_path=directory / "generation_state.json",
                resume=True,
                cancel_check=cancel_marker.exists,
                force_regenerate=force_regenerate,
            )
            if project.imported:
                from smart_audiobook.tts_config import TTSConfig
                config = TTSConfig.from_environment(provider_override=(provider_id(self.tts_provider)
                    if provider_id(self.tts_provider) in {"piper", "system"} else None))
                # Chapter WAVs stay independent; composition inserts chapter pauses once.
                kwargs["tts_config"] = replace(config, chapter_pause_ms=0)
                kwargs["chapter_cache_directories"] = {ch.number: directory / "cache" / "chapters" / ch.id
                    for ch in project.analysis.document.chapters}
                running = set()
                def update_chapter_progress(state):
                    from smart_audiobook.book_manifest import manifest_payload, write_json
                    running.update(int(key) for key, value in state.get("chapters", {}).items()
                                   if value.get("status") == "running" and int(key) in selected)
                    completed = {int(key) for key, value in state.get("chapters", {}).items()
                                 if value.get("status") == "completed" and int(key) in running}
                    if state.get("completed_chapter") in selected:
                        completed.add(state["completed_chapter"])
                    if not completed:
                        return
                    chapters = tuple(replace(ch, status=ChapterStatus.COMPLETED) if ch.number in completed else ch
                                     for ch in project.analysis.document.chapters)
                    if chapters != project.analysis.document.chapters:
                        project.analysis = replace(project.analysis, document=replace(project.analysis.document, chapters=chapters))
                        write_json(directory / "project.json", manifest_payload(project))
                kwargs["state_callback"] = update_chapter_progress
        if project.imported:
            project.analysis = replace(project.analysis, document=replace(project.analysis.document,
                chapters=tuple(replace(ch, status=ChapterStatus.GENERATING_AUDIO) if ch.number in selected else ch
                               for ch in project.analysis.document.chapters)))
            self.store.save(project)
        try:
            result = self.application.generate(analysis, directory / "audio", **kwargs)
        except Exception:
            if project.imported:
                project.analysis = replace(project.analysis, document=replace(project.analysis.document,
                    chapters=tuple(replace(ch, status=ChapterStatus.FAILED) if ch.number in selected and ch.status != ChapterStatus.COMPLETED else ch
                                   for ch in project.analysis.document.chapters)))
                self.store.save(project)
            raise
        project.output = result.output
        if project.imported:
            project.analysis = replace(project.analysis, document=replace(project.analysis.document,
                chapters=tuple(replace(ch, status=ChapterStatus.COMPLETED) if ch.number in selected else ch
                               for ch in project.analysis.document.chapters)))
            # Include previously completed, still-valid chapters in the assembled book.
            from smart_audiobook.audio import combine_wav_files_with_pauses
            from smart_audiobook.tts_config import TTSConfig
            from smart_audiobook.output_files import safe_filename
            files = []
            for ch in project.analysis.document.chapters:
                path = result.output.directory / f"{ch.number:02d}_{safe_filename(ch.title, f'chapter_{ch.number}')}.wav"
                if ch.status == ChapterStatus.COMPLETED and path.is_file():
                    files.append(path)
            chapter_pause = TTSConfig.from_environment().chapter_pause_ms
            combine_wav_files_with_pauses([(path, chapter_pause if index else 0)
                for index, path in enumerate(files)], result.output.full_audiobook)
            project.output = replace(result.output, chapter_files=tuple(files))
            metadata = json.loads(result.output.metadata.read_text(encoding="utf-8"))
            metadata["chapter_count"] = len(files)
            metadata["book_chapter_count"] = len(project.analysis.document.chapters)
            metadata["outputs"]["chapters"] = [p.name for p in files]
            from smart_audiobook.book_manifest import write_json
            write_json(result.output.metadata, metadata)
        self.store.save(project)
        return project

    def generation_status(self, processing_id: str) -> dict[str, object]:
        self.load(processing_id)
        path = self.store.directory(processing_id) / "generation_state.json"
        if not path.is_file():
            return {"status": "pending", "percent": 0}
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"status": "pending", "percent": 0}
        completed = int(state.get("segments_completed", 0))
        total = max(1, int(state.get("segments_total", 1)))
        state["percent"] = min(100, round(completed * 100 / total))
        return state

    def cancel_generation(self, processing_id: str) -> None:
        self.load(processing_id)
        marker = self.store.directory(processing_id) / "cancel.requested"
        marker.write_text("cancel\n", encoding="utf-8")


def _all_segments(analysis: BookAnalysis) -> tuple[TextSegment, ...]:
    return tuple(
        segment for chapter in analysis.chapters for segment in chapter.segments
    )


def _canonical_name(
    requested: str,
    known: list[str],
    exclude: str | None = None,
) -> str:
    compact = " ".join(requested.strip().split())
    if not compact:
        raise ReviewStateError("El nombre del personaje no puede estar vacío.")
    key = normalize_character_name(compact)
    for name in known:
        if name == exclude:
            continue
        if normalize_character_name(name) == key:
            return name
    if key == normalize_character_name(NARRATOR):
        return NARRATOR
    if key == normalize_character_name(UNKNOWN_SPEAKER):
        return UNKNOWN_SPEAKER
    return compact


def _find_character(characters: tuple[str, ...], requested: str) -> str:
    key = normalize_character_name(requested)
    for character in characters:
        if normalize_character_name(character) == key:
            return character
    raise ReviewStateError("El personaje indicado no existe.")


def _replace_segment_speakers(
    analysis: BookAnalysis,
    changes: dict[str, str],
) -> BookAnalysis:
    chapters = tuple(
        replace(
            chapter,
            segments=tuple(
                replace(
                    segment,
                    speaker=changes[segment.id],
                    confidence=1.0,
                    resolution_method="manual",
                    review_needed=False,
                    new_character_candidate=None,
                )
                if segment.id in changes
                else segment
                for segment in chapter.segments
            ),
        )
        for chapter in analysis.chapters
    )
    return replace(analysis, chapters=chapters)


def _replace_character(
    analysis: BookAnalysis,
    source: str,
    target: str,
) -> BookAnalysis:
    source_key = normalize_character_name(source)
    changes = {
        segment.id: target
        for segment in _all_segments(analysis)
        if normalize_character_name(segment.speaker) == source_key
    }
    return _replace_segment_speakers(analysis, changes)


def _with_rebuilt_characters(
    analysis: BookAnalysis,
    preferred: list[str] | None = None,
) -> BookAnalysis:
    ordered = [NARRATOR]
    candidates = list(preferred or ()) + [
        segment.speaker for segment in _all_segments(analysis)
    ]
    seen = {normalize_character_name(NARRATOR)}
    for name in candidates:
        key = normalize_character_name(name)
        if key in seen or key == normalize_character_name(UNKNOWN_SPEAKER):
            continue
        seen.add(key)
        ordered.append(name)
    for name in ordered:
        analysis.registry.register(name)
    return replace(analysis, characters=analysis.registry.characters)


def _complete_voice_assignments(project: ReviewProject) -> None:
    voice_ids = [voice.id for voice in project.voices]
    if not voice_ids:
        return
    project.analysis.registry.rebuild_statistics(project.segments)
    automatic = assign_profile_voices(project.analysis.registry, project.voices, project.voice_assignments)
    if any(s.speaker == UNKNOWN_SPEAKER for s in project.segments):
        automatic.setdefault(UNKNOWN_SPEAKER, voice_ids[-1])
    for character, voice_id in automatic.items():
        project.voice_assignments.setdefault(character, voice_id)


def _move_voice_assignment(
    project: ReviewProject,
    source: str,
    target: str,
) -> None:
    source_voice = project.voice_assignments.pop(source, None)
    if source_voice is not None:
        project.voice_assignments.setdefault(target, source_voice)
    profile = project.analysis.registry.find(target)
    if profile and profile.voice_manual and profile.voice_id:
        project.voice_assignments[target] = profile.voice_id
    _complete_voice_assignments(project)
