"""JSON-backed temporary state for the V0.6 review workflow."""

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from smart_audiobook.book_processor import (
    AnalyzedChapter,
    BookAnalysis,
    BookOutput,
)
from smart_audiobook.models import Chapter, ChapterStatus, Document, DocumentFormat, TextSegment
from smart_audiobook.book_manifest import persist_document, manifest_payload, write_json, load_manifest_document
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
    imported: bool = False
    _persisted_chapters: dict[int, AnalyzedChapter] = field(default_factory=dict, repr=False)
    _persisted_voice_assignments: dict[str, str] = field(default_factory=dict, repr=False)

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

    def recover_interrupted(self) -> None:
        """On local server startup, make interrupted chapters retryable again."""
        for directory in self.root.iterdir():
            if not directory.is_dir() or PROCESSING_ID_PATTERN.fullmatch(directory.name) is None:
                continue
            path = directory / "project.json"
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("schema_version") != 3:
                    continue
                changed = False
                for chapter in payload.get("chapters", ()):
                    if chapter.get("status") in {ChapterStatus.ANALYZING.value, ChapterStatus.GENERATING_AUDIO.value}:
                        chapter["status"] = ChapterStatus.FAILED.value
                        chapter["consistency_warning"] = "Procesamiento interrumpido; vuelve a iniciar la acción para continuar."
                        changed = True
                if changed:
                    write_json(path, payload)
            except (ValueError, OSError, TypeError):
                continue

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
        document = project.analysis.document
        if not document.chapters:
            document = replace(document, chapters=tuple(Chapter(ch.number, ch.title,
                "\n\n".join(s.text for s in ch.segments), status=ChapterStatus.ANALYZED)
                for ch in project.analysis.chapters))
        document = persist_document(document, directory)
        if project.imported and project.output is None:
            changed = set()
            previous_assignments = project._persisted_voice_assignments
            state_file = directory / "analysis.json"
            if not previous_assignments and state_file.is_file():
                previous_assignments = json.loads(state_file.read_text(encoding="utf-8")).get("voice_assignments", {})
            for analyzed in project.analysis.chapters:
                unchanged = project._persisted_chapters.get(analyzed.number) == analyzed
                voices_changed = any(previous_assignments.get(s.speaker) != project.voice_assignments.get(s.speaker)
                                     for s in analyzed.segments)
                if unchanged and not voices_changed:
                    continue
                source = next(ch for ch in document.chapters if ch.number == analyzed.number)
                old_path = directory / "chapters" / f"{source.id}.analysis.json"
                if old_path.is_file():
                    old = json.loads(old_path.read_text(encoding="utf-8"))["segments"]
                    if (old != [s.as_dict() for s in analyzed.segments] or any(
                            previous_assignments.get(s.speaker) != project.voice_assignments.get(s.speaker)
                            for s in analyzed.segments)):
                        changed.add(analyzed.number)
            if changed:
                from smart_audiobook.book_workflow import chapter_state
                analyzed_by_number = {ch.number: ch for ch in project.analysis.chapters}
                document = replace(document, chapters=tuple(replace(ch,
                    status=chapter_state(analyzed_by_number[ch.number].segments)) if ch.number in changed else
                    replace(ch, consistency_warning=f"Revisar continuidad: cambió el capítulo {min(changed)}.")
                    if ch.number > min(changed) and ch.status != ChapterStatus.NOT_ANALYZED else ch for ch in document.chapters))
        project.analysis = replace(project.analysis, document=document)
        payload = _project_to_dict(project, directory)
        target = directory / "analysis.json"
        temporary = directory / "analysis.json.tmp"
        with self._lock:
            write_json(directory / "project.json", manifest_payload(project))
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(target)
            project._persisted_chapters = {ch.number: ch for ch in project.analysis.chapters}
            project._persisted_voice_assignments = dict(project.voice_assignments)

    def load(self, processing_id: str) -> ReviewProject:
        directory = self.directory(processing_id)
        target = directory / "analysis.json"
        if not target.is_file():
            raise ReviewStateError("El análisis solicitado no existe.")
        try:
            with self._lock:
                payload = json.loads(target.read_text(encoding="utf-8"))
            project = _project_from_dict(payload, directory)
            if (directory / "project.json").is_file():
                document, project.imported = load_manifest_document(directory, project.source_name)
                project.analysis = replace(project.analysis, document=document)
            else:
                # V0.8 projects had no manifest. Re-import structure only, never speakers.
                from smart_audiobook.document_loaders import load_document, DocumentLoadError
                try:
                    document = load_document(project.analysis.document.source_path)
                except DocumentLoadError:
                    document = replace(project.analysis.document, chapters=tuple(Chapter(ch.number, ch.title,
                        "\n\n".join(s.text for s in ch.segments)) for ch in project.analysis.chapters))
                analyzed = {ch.number: ch for ch in project.analysis.chapters}
                document = replace(document, chapters=tuple(replace(ch,
                    status=ChapterStatus.REVIEW_REQUIRED if any(s.review_needed for s in analyzed[ch.number].segments)
                    else ChapterStatus.READY_FOR_AUDIO) if ch.number in analyzed else ch for ch in document.chapters))
                project.analysis = replace(project.analysis, document=document)
                project.imported = True
            project._persisted_chapters = {ch.number: ch for ch in project.analysis.chapters}
            project._persisted_voice_assignments = dict(project.voice_assignments)
            return project
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as error:
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
                **(_persist_analysis_chapter(chapter, document, directory,
                    project._persisted_chapters.get(chapter.number) == chapter) if project.imported else
                   {"segments": [segment.as_dict() for segment in chapter.segments]}),
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


def _persist_analysis_chapter(chapter: AnalyzedChapter, document: Document, directory: Path, unchanged: bool) -> dict:
    source = next(ch for ch in document.chapters if ch.number == chapter.number)
    reference = f"chapters/{source.id}.analysis.json"
    if not unchanged or not (directory / reference).is_file():
        write_json(directory / reference, {"segments": [s.as_dict() for s in chapter.segments]})
    return {"analysis_reference": reference}


def _project_from_dict(payload: dict[str, Any], directory: Path) -> ReviewProject:
    if int(payload.get("schema_version", 1)) > 2:
        raise ReviewStateError("Versión de proyecto no compatible.")
    processing_id = str(payload["processing_id"])
    ReviewProjectStore._validate_id(processing_id)
    if processing_id != directory.name:
        raise ReviewStateError("Identificador de estado incoherente.")
    source_name = str(payload["source_name"])
    document_data = payload["document"]
    if Path(source_name).name != source_name or "\\" in source_name:
        raise ReviewStateError("Nombre de origen no seguro.")
    source_path = directory / "source" / source_name
    chapters = tuple(
        AnalyzedChapter(
            number=int(chapter["number"]),
            title=str(chapter["title"]),
            segments=tuple(_segment_from_dict(item) for item in _chapter_segments(chapter, directory)),
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
        emphasis=tuple(data.get("emphasis", ())),
        scene_break_before=bool(data.get("scene_break_before", False)),
    )


def _chapter_segments(chapter: dict, directory: Path) -> list:
    if "analysis_reference" in chapter:
        path = _safe_project_path(directory, chapter["analysis_reference"])
        return json.loads(path.read_text(encoding="utf-8"))["segments"]
    return chapter["segments"]


def _relative_path(path: Path, directory: Path) -> str:
    return path.resolve().relative_to(directory.resolve()).as_posix()


def _safe_project_path(directory: Path, relative: object) -> Path:
    candidate = (directory / str(relative)).resolve()
    if not candidate.is_relative_to(directory.resolve()):
        raise ReviewStateError("El estado contiene una ruta no permitida.")
    return candidate


def _optional_string(value: object) -> str | None:
    return str(value) if value is not None else None
