"""Small project manifests, lazy chapter sources and inexpensive estimates."""

import hashlib
import json
import os
import re
from dataclasses import asdict, replace
from pathlib import Path

from smart_audiobook.models import Chapter, ChapterStatus, Document, DocumentBlock
from smart_audiobook.segmenter import segment_text
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
from smart_audiobook.models import SpeakerContext

MANIFEST_SCHEMA_VERSION = 3


def write_json(path: Path, payload: dict) -> None:
    """Do not rewrite large, immutable chapter files when the payload is unchanged."""
    value = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == value:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def persist_document(document: Document, directory: Path) -> Document:
    """Store each source chapter once; keep only the index in memory thereafter."""
    chapters = []
    for chapter in document.chapters:
        identity = chapter.id or "ch_" + hashlib.sha256(f"{document.format}:{chapter.number}".encode()).hexdigest()[:16]
        if not re.fullmatch(r"ch_[0-9a-f]{16}", identity):
            raise ValueError("Identificador de capítulo no seguro.")
        path = directory / "chapters" / f"{identity}.json"
        if chapter.text_path is None:
            write_json(path, {"raw_text": chapter.raw_text or chapter.text,
                "normalized_text": chapter.text, "blocks": [asdict(b) for b in chapter.blocks],
                "cleaning_notes": list(chapter.cleaning_notes)})
        chapters.append(replace(chapter, id=identity, text="", raw_text=None, blocks=(), text_path=path,
            word_count=chapter.word_count or len(chapter.read_text().split())))
    return replace(document, full_text="", blocks=(), chapters=tuple(chapters))


def document_index(document: Document) -> dict:
    return {"title": document.title, "author": document.author, "language": document.language,
        "publisher": document.publisher, "identifier": document.identifier, "source_format": document.format,
        "chapter_count": len(document.chapters)}


def manifest_payload(project) -> dict:
    return {"schema_version": MANIFEST_SCHEMA_VERSION, "processing_id": project.processing_id,
        "book": document_index(project.analysis.document), "source_name": project.source_name,
        "analysis_reference": "analysis.json", "character_registry_reference": "analysis.json#character_profiles",
        "voice_assignments": project.voice_assignments,
        "generation_reference": "generation_state.json", "imported": project.imported,
        "chapters": [{"id": ch.id, "number": ch.number, "title": ch.title,
            "source_reference": ch.source_reference, "content_reference": f"chapters/{ch.id}.json",
            "word_count": ch.word_count, "status": ch.status.value,
            "selected_for_processing": ch.selected_for_processing, "narrate": ch.narrate,
            "section_kind": ch.section_kind, "consistency_warning": ch.consistency_warning,
            "cleaning_notes": list(ch.cleaning_notes)} for ch in project.analysis.document.chapters]}


def load_manifest_document(directory: Path, source_name: str) -> tuple[Document, bool]:
    payload = json.loads((directory / "project.json").read_text(encoding="utf-8"))
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError("Versión de manifest no compatible.")
    book = payload["book"]
    chapters = []
    seen_ids = set()
    seen_numbers = set()
    for item in payload["chapters"]:
        if (not re.fullmatch(r"ch_[0-9a-f]{16}", item["id"]) or item["id"] in seen_ids
                or int(item["number"]) < 1 or int(item["number"]) in seen_numbers):
            raise ValueError("Índice de capítulos no válido.")
        seen_ids.add(item["id"])
        seen_numbers.add(int(item["number"]))
        content = (directory / item["content_reference"]).resolve()
        if not content.is_relative_to((directory / "chapters").resolve()):
            raise ValueError("Referencia de capítulo no segura.")
        chapters.append(Chapter(int(item["number"]), item["title"], "", item["id"],
            item.get("source_reference"), status=ChapterStatus(item["status"]),
            selected_for_processing=bool(item["selected_for_processing"]), narrate=bool(item["narrate"]),
            section_kind=item.get("section_kind"), word_count=int(item["word_count"]), text_path=content,
            consistency_warning=item.get("consistency_warning"), cleaning_notes=tuple(item.get("cleaning_notes", ()))))
    return Document(book["title"], directory / "source" / source_name, book["source_format"], "", tuple(chapters),
        author=book.get("author"), language=book.get("language"), publisher=book.get("publisher"),
        identifier=book.get("identifier")), bool(payload.get("imported", True))


def load_chapter(chapter: Chapter) -> Chapter:
    if chapter.text_path is None:
        return chapter
    payload = json.loads(chapter.text_path.read_text(encoding="utf-8"))
    blocks = tuple(DocumentBlock(text=b["text"], heading_level=b.get("heading_level"),
        emphasis=tuple(b.get("emphasis", ())), scene_break=bool(b.get("scene_break", False)),
        source_reference=b.get("source_reference")) for b in payload.get("blocks", ()))
    return replace(chapter, text=payload["normalized_text"], raw_text=payload.get("raw_text"),
                   blocks=blocks, text_path=None)


def select_range(expression: str, chapter_numbers: set[int]) -> set[int]:
    """Parse 1-10,25,100-150 with bounded expansion and reject unknown chapters."""
    result = set()
    try:
        for part in expression.split(","):
            bounds = [int(value.strip()) for value in part.split("-")]
            if len(bounds) == 1:
                result.add(bounds[0])
            elif len(bounds) == 2 and 0 < bounds[0] <= bounds[1] <= max(chapter_numbers, default=0):
                result.update(range(bounds[0], bounds[1]+1))
            else:
                raise ValueError
        if not result or not result.issubset(chapter_numbers):
            raise ValueError
    except ValueError as error:
        raise ValueError("Rango de capítulos no válido; usa por ejemplo 1-10,25.") from error
    return result


def book_statistics(document: Document, *, detailed: bool = False,
                    words_per_minute: int | None = None, provider: str = "piper", device: str = "auto") -> dict:
    wpm = int(os.getenv("READING_WORDS_PER_MINUTE", "160")) if words_per_minute is None else words_per_minute
    if wpm <= 0:
        raise ValueError("READING_WORDS_PER_MINUTE debe ser positivo.")
    selected = [ch for ch in document.chapters if ch.selected_for_processing and ch.narrate]
    words = sum(ch.word_count for ch in selected)
    result = {"chapters": len(document.chapters), "selected_chapters": len(selected),
        "total_words": sum(ch.word_count for ch in document.chapters), "selected_words": words,
        "approximate_reading_minutes": round(words / wpm, 1), "words_per_minute": wpm,
        "size_category": "Small" if words < 10_000 else "Medium" if words < 50_000 else "Large" if words < 150_000 else "Very large",
        "tts_provider": provider, "device_requested": device,
        "processing_note": "Duración de lectura estimada; la generación depende del equipo, modelo y caché.",
        "cost_note": "TTS local sin tarifa API. El coste/cuota Gemini depende del modelo y plan; no se calcula una tarifa inventada."}
    if detailed:
        dialogue_count = segments_count = explicit = 0
        rules = RuleBasedSpeakerResolver()
        for chapter in selected:
            segments = segment_text(chapter.read_text())
            segments_count += len(segments)
            for i, segment in enumerate(segments):
                if segment.type == "dialogue":
                    dialogue_count += 1
                    context = SpeakerContext(segment, tuple(segments[max(0, i-4):i]), tuple(segments[i+1:i+5]), ())
                    explicit += int(rules.resolve(context) is not None)
        result.update(estimated_dialogues=dialogue_count, estimated_segments=segments_count,
            estimated_rule_resolution_percent=round(explicit*100/max(1, dialogue_count)),
            estimated_llm_calls_upper_bound=dialogue_count-explicit,
            llm_estimate_note="Máximo aproximado antes de continuidad, candidatos y caché; cero llamadas si LLM está desactivado.")
    return result
