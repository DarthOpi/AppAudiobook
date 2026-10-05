"""End-to-end orchestration for a multi-chapter audiobook."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from smart_audiobook.audio import combine_wav_files, generate_audiobook
from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.models import Document, TextSegment
from smart_audiobook.output_files import safe_filename, write_metadata
from smart_audiobook.segmenter import segment_text
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.tts import SpeechGenerationError, get_available_voice_ids
from smart_audiobook.tts_providers import LocalTTSProvider, TTSProvider
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
    voice_assignments: dict[str, str]


def analyze_book(
    document: Document,
    speaker_service: SpeakerIdentificationService,
) -> BookAnalysis:
    """Segment and identify speakers while sharing characters across chapters."""
    registry = CharacterRegistry()
    analyzed_chapters: list[AnalyzedChapter] = []
    global_order = 0
    for chapter in document.chapters:
        analysis = speaker_service.identify(segment_text(chapter.text), registry=registry)
        indexed_segments = []
        for segment in analysis.segments:
            global_order += 1
            indexed_segments.append(
                replace(
                    segment,
                    id=f"seg_{global_order:06d}",
                    chapter=chapter.number,
                    order=global_order,
                )
            )
        analyzed_chapters.append(
            AnalyzedChapter(chapter.number, chapter.title, tuple(indexed_segments))
        )
    return BookAnalysis(document, tuple(analyzed_chapters), registry.characters)


def generate_book(
    analysis: BookAnalysis,
    output_root: Path,
    full_audiobook_path: Path | None = None,
    voices_by_speaker: dict[str, str] | None = None,
    tts_provider: TTSProvider | None = None,
) -> BookOutput:
    """Generate chapter WAV files, the combined book and its metadata."""
    output_directory = output_root / safe_filename(analysis.document.title, "book")
    output_directory.mkdir(parents=True, exist_ok=True)

    all_segments = tuple(
        segment for chapter in analysis.chapters for segment in chapter.segments
    )
    if not all_segments:
        raise SpeechGenerationError("El documento no contiene texto para convertir.")
    provider = tts_provider or LocalTTSProvider()
    voice_ids = (
        [voice.id for voice in provider.list_voices()]
        if tts_provider is not None
        else get_available_voice_ids()
    )
    if not voice_ids:
        raise SpeechGenerationError("No se encontraron voces instaladas.")
    if voices_by_speaker is None:
        voices_by_speaker = assign_voices(all_segments, voice_ids)

    used_speakers = {segment.speaker for segment in all_segments}
    missing_speakers = sorted(used_speakers.difference(voices_by_speaker))
    invalid_voices = sorted(set(voices_by_speaker.values()).difference(voice_ids))
    if missing_speakers:
        raise SpeechGenerationError(
            "Falta una voz para: " + ", ".join(missing_speakers)
        )
    if invalid_voices:
        raise SpeechGenerationError("Hay voces seleccionadas que ya no están disponibles.")

    chapter_files: list[Path] = []
    for chapter in analysis.chapters:
        chapter_name = safe_filename(chapter.title, f"chapter_{chapter.number}")
        chapter_path = output_directory / f"{chapter.number:02d}_{chapter_name}.wav"
        generate_audiobook(
            chapter.segments,
            chapter_path,
            voices_by_speaker=voices_by_speaker,
            tts_provider=provider,
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
            "source": analysis.document.source_path.name,
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
        voice_assignments=dict(voices_by_speaker),
    )
