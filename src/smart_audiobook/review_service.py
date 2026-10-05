"""Application service for reviewing speakers and voices before TTS."""

import hashlib
import logging
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from smart_audiobook.application import AudiobookApplicationService, ProcessingResult
from smart_audiobook.book_processor import AnalyzedChapter, BookAnalysis
from smart_audiobook.characters import normalize_character_name
from smart_audiobook.models import NARRATOR, UNKNOWN_SPEAKER, TextSegment
from smart_audiobook.review_store import (
    ReviewProject,
    ReviewProjectStore,
    ReviewStateError,
)
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import TTSProvider
from smart_audiobook.voice_assignment import assign_voices

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
            assignments = assign_voices(
                _all_segments(analysis),
                [voice.id for voice in voices],
            )
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

    def update_speakers(
        self,
        processing_id: str,
        updates: dict[str, str],
    ) -> ReviewProject:
        project = self.load(processing_id)
        known = list(project.characters)
        changed: dict[str, str] = {}
        for segment in project.dialogues:
            requested = updates.get(segment.id)
            if requested is None:
                continue
            canonical = _canonical_name(requested, known)
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
        project = self.load(processing_id)
        if current_name in {NARRATOR, UNKNOWN_SPEAKER}:
            raise ReviewStateError("Narrator y Unknown no se pueden renombrar.")
        current = _find_character(project.characters, current_name)
        target = _canonical_name(new_name, list(project.characters), exclude=current)
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
    ) -> ReviewProject:
        project = self.load(processing_id)
        if source_name in {NARRATOR, UNKNOWN_SPEAKER}:
            raise ReviewStateError("Ese personaje no se puede fusionar.")
        source = _find_character(project.characters, source_name)
        target = _find_character(project.characters, target_name)
        if normalize_character_name(source) == normalize_character_name(target):
            raise ReviewStateError("Selecciona dos personajes diferentes.")
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
        project = self.load(processing_id)
        canonical = _find_character(project.characters, character)
        if voice_id not in {voice.id for voice in project.voices}:
            raise ReviewStateError("La voz seleccionada no está disponible.")
        project.voice_assignments[canonical] = voice_id
        project.output = None
        self.store.save(project)
        return project

    def preview_voice(self, processing_id: str, voice_id: str) -> Path:
        project = self.load(processing_id)
        if voice_id not in {voice.id for voice in project.voices}:
            raise ReviewStateError("La voz seleccionada no está disponible.")
        digest = hashlib.sha256(voice_id.encode("utf-8")).hexdigest()[:20]
        path = self.store.directory(processing_id) / "previews" / f"{digest}.wav"
        if path.is_file() and path.stat().st_size:
            return path
        return self.tts_provider.synthesize(PREVIEW_TEXT, path, voice_id)

    def generate(self, processing_id: str) -> ReviewProject:
        project = self.load(processing_id)
        available_voice_ids = {voice.id for voice in project.voices}
        if not available_voice_ids:
            raise SpeechGenerationError("No se encontraron voces disponibles.")
        used_speakers = {segment.speaker for segment in project.segments}
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
        result = self.application.generate(
            project.analysis,
            self.store.directory(processing_id) / "audio",
            voices_by_speaker=project.voice_assignments,
            tts_provider=self.tts_provider,
        )
        project.output = result.output
        self.store.save(project)
        return project


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
    return replace(analysis, characters=tuple(ordered))


def _complete_voice_assignments(project: ReviewProject) -> None:
    voice_ids = [voice.id for voice in project.voices]
    if not voice_ids:
        return
    automatic = assign_voices(project.segments, voice_ids)
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
    _complete_voice_assignments(project)
