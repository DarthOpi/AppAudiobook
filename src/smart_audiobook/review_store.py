"""JSON-backed temporary state for the V0.6 review workflow."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from smart_audiobook.book_processor import (
    AnalyzedChapter,
    BookAnalysis,
    BookOutput,
)
from smart_audiobook.models import Document, DocumentFormat, TextSegment
from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.tts_providers import VoiceInfo

PROCESSING_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class ReviewStateError(ValueError):
    """Raised for invalid, missing, or inconsistent temporary review state."""


@dataclass(slots=True)
class ReviewProject:
    """Editable analysis and voice configuration for one uploaded document."""

    processing_id: str
    source_name: str
    analysis: BookAnalysis
    voices: tuple[VoiceInfo, ...]
    voice_assignments: dict[str, str]
    output: BookOutput | None = None

    @property
    def segments(self) -> tuple[TextSegment, ...]:
        return tuple(
            segment
            for chapter in self.analysis.chapters
            for segment in chapter.segments
        )

    @property
    def dialogues(self) -> tuple[TextSegment, ...]:
        return tuple(segment for segment in self.segments if segment.type == "dialogue")

    @property
    def unresolved_count(self) -> int:
        return sum(segment.speaker == "Unknown" for segment in self.dialogues)

    @property
    def characters(self) -> tuple[str, ...]:
        characters = list(self.analysis.characters)
        if self.unresolved_count and "Unknown" not in characters:
            characters.append("Unknown")
        return tuple(characters)


class ReviewProjectStore:
    """Persist review projects beneath a controlled root using UUID identifiers."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def create_workspace(self) -> tuple[str, Path]:
        processing_id = uuid4().hex
        directory = self.root / processing_id
        (directory / "source").mkdir(parents=True)
        (directory / "audio").mkdir()
        (directory / "previews").mkdir()
        return processing_id, directory

    def directory(self, processing_id: str) -> Path:
        self._validate_id(processing_id)
        return self.root / processing_id

    def save(self, project: ReviewProject) -> None:
        directory = self.directory(project.processing_id)
        if not directory.is_dir():
            raise ReviewStateError("El análisis solicitado no existe.")
        for name, voice_id in project.voice_assignments.items():
            profile = project.analysis.registry.find(name)
            if profile:
                profile.voice_id = voice_id
        project.analysis.registry.rebuild_statistics(project.segments)
        payload = _project_to_dict(project, directory)
        target = directory / "analysis.json"
        temporary = directory / "analysis.json.tmp"
        with self._lock:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)

    def load(self, processing_id: str) -> ReviewProject:
        directory = self.directory(processing_id)
        target = directory / "analysis.json"
        if not target.is_file():
            raise ReviewStateError("El análisis solicitado no existe.")
        try:
            with self._lock:
                payload = json.loads(target.read_text(encoding="utf-8"))
            return _project_from_dict(payload, directory)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ReviewStateError("El estado temporal del análisis no es válido.") from error

    @staticmethod
    def _validate_id(processing_id: str) -> None:
        if PROCESSING_ID_PATTERN.fullmatch(processing_id) is None:
            raise ReviewStateError("Identificador de procesamiento no válido.")


def _project_to_dict(project: ReviewProject, directory: Path) -> dict[str, Any]:
    document = project.analysis.document
    payload: dict[str, Any] = {
        "version": 1,
        "schema_version": 2,
        "character_profiles": project.analysis.registry.to_list(),
        "resolution_cache": project.analysis.resolution_cache,
        "llm_calls_saved": project.analysis.llm_calls_saved,
        "processing_id": project.processing_id,
        "source_name": project.source_name,
        "document": {
            "title": document.title,
            "format": document.format,
            "full_text": document.full_text,
        },
        "chapters": [
            {
                "number": chapter.number,
                "title": chapter.title,
                "segments": [segment.as_dict() for segment in chapter.segments],
            }
            for chapter in project.analysis.chapters
        ],
        "characters": list(project.analysis.characters),
        "voices": [
            {
                "id": voice.id,
                "name": voice.name,
                "language": voice.language,
                "gender": voice.gender,
                "provider": voice.provider,
                "description": voice.description,
                "style": voice.style,
                "reference_audio": voice.reference_audio,
                "strategy": voice.strategy,
            }
            for voice in project.voices
        ],
        "voice_assignments": dict(project.voice_assignments),
        "output": None,
    }
    if project.output is not None:
        payload["output"] = {
            "directory": _relative_path(project.output.directory, directory),
            "chapter_files": [
                _relative_path(path, directory) for path in project.output.chapter_files
            ],
            "full_audiobook": _relative_path(
                project.output.full_audiobook, directory
            ),
            "metadata": _relative_path(project.output.metadata, directory),
            "voice_assignments": dict(project.output.voice_assignments),
            "generation_state": (
                _relative_path(project.output.generation_state, directory)
                if project.output.generation_state is not None
                else None
            ),
        }
    return payload


def _project_from_dict(payload: dict[str, Any], directory: Path) -> ReviewProject:
    if int(payload.get("schema_version", 1)) > 2:
        raise ReviewStateError("Versión de proyecto no compatible.")
    processing_id = str(payload["processing_id"])
    ReviewProjectStore._validate_id(processing_id)
    source_name = str(payload["source_name"])
    document_data = payload["document"]
    source_path = directory / "source" / source_name
    chapters = tuple(
        AnalyzedChapter(
            number=int(chapter["number"]),
            title=str(chapter["title"]),
            segments=tuple(_segment_from_dict(item) for item in chapter["segments"]),
        )
        for chapter in payload["chapters"]
    )
    document = Document(
        title=str(document_data["title"]),
        source_path=source_path,
        format=cast(DocumentFormat, str(document_data["format"])),
        full_text=str(document_data["full_text"]),
    )
    analysis = BookAnalysis(
        document=document,
        chapters=chapters,
        characters=tuple(str(value) for value in payload["characters"]),
        registry=CharacterRegistry.from_list(payload.get("character_profiles", [])),
        resolution_cache=payload.get("resolution_cache", {}),
        llm_calls_saved=int(payload.get("llm_calls_saved", 0)),
    )
    if "character_profiles" not in payload:
        analysis.registry.rebuild_statistics(s for ch in chapters for s in ch.segments)
        # Older assignments lack provenance: preserve them as manual conservatively.
        for name, voice_id in payload.get("voice_assignments", {}).items():
            profile = analysis.registry.find(name)
            if profile:
                profile.voice_id, profile.voice_manual = str(voice_id), True
    voices = tuple(
        VoiceInfo(
            id=str(voice["id"]),
            name=str(voice["name"]),
            language=_optional_string(voice.get("language")),
            gender=_optional_string(voice.get("gender")),
            provider=str(voice.get("provider", "unknown")),
            description=_optional_string(voice.get("description")),
            style=_optional_string(voice.get("style")),
            reference_audio=_optional_string(voice.get("reference_audio")),
            strategy=voice.get("strategy", "dedicated"),
        )
        for voice in payload["voices"]
    )
    output_data = payload.get("output")
    output = None
    if output_data:
        output = BookOutput(
            directory=_safe_project_path(directory, output_data["directory"]),
            chapter_files=tuple(
                _safe_project_path(directory, value)
                for value in output_data["chapter_files"]
            ),
            full_audiobook=_safe_project_path(
                directory, output_data["full_audiobook"]
            ),
            metadata=_safe_project_path(directory, output_data["metadata"]),
            voice_assignments={
                str(key): str(value)
                for key, value in output_data["voice_assignments"].items()
            },
            generation_state=(
                _safe_project_path(directory, output_data["generation_state"])
                if output_data.get("generation_state")
                else None
            ),
        )
    return ReviewProject(
        processing_id=processing_id,
        source_name=source_name,
        analysis=analysis,
        voices=voices,
        voice_assignments={
            str(key): str(value)
            for key, value in payload["voice_assignments"].items()
        },
        output=output,
    )


def _segment_from_dict(data: dict[str, Any]) -> TextSegment:
    return TextSegment(
        type=data["type"],
        text=str(data["text"]),
        speaker=str(data["speaker"]),
        id=str(data["id"]),
        chapter=int(data["chapter"]),
        order=int(data["order"]),
        confidence=(
            float(data["confidence"]) if data.get("confidence") is not None else None
        ),
        resolution_method=data.get("resolution_method"),
        review_needed=bool(data.get("review_needed", data["speaker"] == "Unknown" or (
            data.get("confidence") is not None and float(data["confidence"]) < 0.85
        ))),
        new_character_candidate=data.get("new_character_candidate"),
    )


def _relative_path(path: Path, directory: Path) -> str:
    return path.resolve().relative_to(directory.resolve()).as_posix()


def _safe_project_path(directory: Path, relative: object) -> Path:
    candidate = (directory / str(relative)).resolve()
    if not candidate.is_relative_to(directory.resolve()):
        raise ReviewStateError("El estado contiene una ruta no permitida.")
    return candidate


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
