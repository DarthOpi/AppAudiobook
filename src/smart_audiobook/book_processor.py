"""End-to-end orchestration for a multi-chapter audiobook."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from smart_audiobook.audio import combine_wav_files, generate_audiobook
from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.models import Document, TextSegment
from smart_audiobook.output_files import safe_filename, write_metadata
from smart_audiobook.segmenter import segment_text
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.tts import SpeechGenerationError, get_available_voice_ids
from smart_audiobook.voice_assignment import assign_voices


@dataclass(frozen=True, slots=True)
class AnalyzedChapter:
    """A chapter after speaker analysis."""

    number: int
    title: str
    segments: tuple[TextSegment, ...]


@dataclass(frozen=True, slots=True)
class BookAnalysis:
    """A complete document analysis before audio generation."""

    document: Document
    chapters: tuple[AnalyzedChapter, ...]
    characters: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BookOutput:
    """Paths created for one audiobook."""

    directory: Path
    chapter_files: tuple[Path, ...]
    full_audiobook: Path
    metadata: Path


def analyze_book(
    document: Document,
    speaker_service: SpeakerIdentificationService,
) -> BookAnalysis:
    """Segment and identify speakers while sharing characters across chapters."""
    registry = CharacterRegistry()
    analyzed_chapters: list[AnalyzedChapter] = []
    for chapter in document.chapters:
        analysis = speaker_service.identify(segment_text(chapter.text), registry=registry)
        analyzed_chapters.append(
            AnalyzedChapter(chapter.number, chapter.title, analysis.segments)
        )
    return BookAnalysis(document, tuple(analyzed_chapters), registry.characters)


def generate_book(
    analysis: BookAnalysis,
    output_root: Path,
    full_audiobook_path: Path | None = None,
) -> BookOutput:
    """Generate chapter WAV files, the combined book and its metadata."""
    output_directory = output_root / safe_filename(analysis.document.title, "book")
    output_directory.mkdir(parents=True, exist_ok=True)

    all_segments = tuple(
        segment for chapter in analysis.chapters for segment in chapter.segments
    )
    if not all_segments:
        raise SpeechGenerationError("El documento no contiene texto para convertir.")
    voice_ids = get_available_voice_ids()
    if not voice_ids:
        raise SpeechGenerationError("No se encontraron voces instaladas.")
    voices_by_speaker = assign_voices(all_segments, voice_ids)

    chapter_files: list[Path] = []
    for chapter in analysis.chapters:
        chapter_name = safe_filename(chapter.title, f"chapter_{chapter.number}")
        chapter_path = output_directory / f"{chapter.number:02d}_{chapter_name}.wav"
        generate_audiobook(
            chapter.segments,
            chapter_path,
            voices_by_speaker=voices_by_speaker,
        )
        chapter_files.append(chapter_path)

    full_audiobook = full_audiobook_path or output_directory / "full_audiobook.wav"
    combine_wav_files(chapter_files, full_audiobook)
    metadata_path = output_directory / "metadata.json"
    write_metadata(
        {
            "title": analysis.document.title,
            "format": analysis.document.format,
            "chapter_count": len(analysis.chapters),
            "detected_characters": list(analysis.characters),
            "voice_assignments": dict(voices_by_speaker),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": str(analysis.document.source_path),
            "outputs": {
                "chapters": [path.name for path in chapter_files],
                "full_audiobook": full_audiobook.name,
            },
        },
        metadata_path,
    )
    return BookOutput(
        directory=output_directory,
        chapter_files=tuple(chapter_files),
        full_audiobook=full_audiobook,
        metadata=metadata_path,
    )
