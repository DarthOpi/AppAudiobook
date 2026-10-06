"""Analysis plus resumable, content-addressed audiobook generation."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, replace, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from smart_audiobook.audio import (
    GenerationCancelled,
    PauseSettings,
    combine_wav_files,
    generate_audiobook,
)
from smart_audiobook.audio_cache import AudioCache
from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.models import Document, TextSegment
from smart_audiobook.output_files import safe_filename, write_metadata
from smart_audiobook.segmenter import segment_text
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.tts import SpeechGenerationError, get_available_voice_ids
from smart_audiobook.text_chunking import chunk_text
from smart_audiobook.tts_config import TTSConfig
from smart_audiobook.tts_providers import (
    LocalTTSProvider,
    SynthesisSettings,
    TTSProvider,
    provider_id,
    provider_model,
)
from smart_audiobook.voice_assignment import assign_voices, assign_profile_voices
from smart_audiobook.character_config import CharacterConfig

LOGGER = logging.getLogger(__name__)
StateCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class AnalyzedChapter:
    number: int
    title: str
    segments: tuple[TextSegment, ...]


@dataclass(frozen=True, slots=True)
class BookAnalysis:
    document: Document
    chapters: tuple[AnalyzedChapter, ...]
    characters: tuple[str, ...]
    registry: CharacterRegistry = field(default_factory=CharacterRegistry)
    resolution_cache: dict[str, dict | None] = field(default_factory=dict)
    llm_calls_saved: int = 0
    resolution_diagnostics: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in self.characters:
            self.registry.register(name)


@dataclass(frozen=True, slots=True)
class BookOutput:
    directory: Path
    chapter_files: tuple[Path, ...]
    full_audiobook: Path
    metadata: Path
    voice_assignments: dict[str, str]
    generation_state: Path | None = None


def analyze_book(
    document: Document,
    speaker_service: SpeakerIdentificationService,
) -> BookAnalysis:
    registry = CharacterRegistry()
    analyzed_chapters: list[AnalyzedChapter] = []
    global_order = 0
    for chapter in document.chapters:
        indexed_segments = []
        for segment in segment_text(chapter.read_text()):
            global_order += 1
            indexed_segments.append(
                replace(
                    segment,
                    id=f"seg_{global_order:06d}",
                    chapter=chapter.number,
                    order=global_order,
                )
            )
        analysis = speaker_service.identify(indexed_segments, registry=registry)
        analyzed_chapters.append(
            AnalyzedChapter(chapter.number, chapter.title, analysis.segments)
        )
    return BookAnalysis(document, tuple(analyzed_chapters), registry.characters,
                        registry, speaker_service.resolution_cache, speaker_service.llm_calls_saved,
                        dict(speaker_service.diagnostics))


def generate_book(
    analysis: BookAnalysis,
    output_root: Path,
    full_audiobook_path: Path | None = None,
    voices_by_speaker: dict[str, str] | None = None,
    tts_provider: TTSProvider | None = None,
    *,
    resume: bool = True,
    force_regenerate: bool = False,
    cache_directory: Path | None = None,
    state_path: Path | None = None,
    synthesis_settings: SynthesisSettings | None = None,
    tts_config: TTSConfig | None = None,
    state_callback: StateCallback | None = None,
    cancel_check: CancelCheck | None = None,
    chapter_cache_directories: dict[int, Path] | None = None,
) -> BookOutput:
    """Generate chapters idempotently and persist progress after every chunk."""
    started = time.monotonic()
    config = tts_config or TTSConfig()
    settings = synthesis_settings or SynthesisSettings()
    pauses = PauseSettings(
        segment_ms=config.segment_pause_ms,
        paragraph_ms=config.paragraph_pause_ms,
        speaker_change_ms=config.speaker_change_pause_ms,
        scene_break_ms=config.scene_break_pause_ms,
    )
    output_directory = output_root / safe_filename(analysis.document.title, "book")
    output_directory.mkdir(parents=True, exist_ok=True)
    state_path = state_path or output_directory / "generation_state.json"
    cache = AudioCache(cache_directory or output_directory / ".audio-cache")
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
        analysis.registry.rebuild_statistics(all_segments)
        if tts_provider is not None:
            voices_by_speaker = assign_profile_voices(analysis.registry, provider.list_voices())
            if any(s.speaker == "Unknown" for s in all_segments):
                voices_by_speaker.setdefault("Unknown", voice_ids[-1])
        else:
            voices_by_speaker = assign_voices(all_segments, voice_ids)
    _validate_assignments(all_segments, voices_by_speaker, voice_ids)
    for name, voice_id in voices_by_speaker.items():
        profile = analysis.registry.find(name)
        if profile:
            profile.voice_id = voice_id
    chapter_chunk_counts = {
        chapter.number: sum(
            len(chunk_text(segment.text, config.chunk_max_chars))
            for segment in chapter.segments
        )
        for chapter in analysis.chapters
    }
    total_chunks = sum(chapter_chunk_counts.values())

    previous = _load_state(state_path) if resume and not force_regenerate else {}
    chapters_state = previous.get("chapters", {})
    state: dict[str, Any] = {
        "version": 1,
        "status": "running",
        "provider": provider_id(provider),
        "model": provider_model(provider),
        "segments_total": total_chunks,
        "segments_completed": 0,
        "chapter_current": 0,
        "chapter_total": len(analysis.chapters),
        "segment_current": 0,
        "segment_total": total_chunks,
        "cache_hits": 0,
        "cache_misses": 0,
        "segments_generated": 0,
        "error": None,
        "cancel_requested": bool(previous.get("cancel_requested", False)),
        "chapters": chapters_state,
    }
    _save_state(state_path, state, state_callback)
    chapter_files: list[Path] = []
    completed_segments = 0
    completed_chunks = 0
    try:
        for chapter_index, chapter in enumerate(analysis.chapters, start=1):
            if chapter_cache_directories and chapter.number in chapter_cache_directories:
                cache.root = chapter_cache_directories[chapter.number].resolve()
                cache.root.mkdir(parents=True, exist_ok=True)
            chapter_name = safe_filename(chapter.title, f"chapter_{chapter.number}")
            chapter_path = output_directory / f"{chapter.number:02d}_{chapter_name}.wav"
            fingerprint = _chapter_fingerprint(
                chapter, voices_by_speaker, provider, settings, pauses, config
            )
            key = str(chapter.number)
            old = chapters_state.get(key, {})
            if (
                resume
                and not force_regenerate
                and old.get("status") == "completed"
                and old.get("fingerprint") == fingerprint
                and chapter_path.is_file()
                and chapter_path.stat().st_size
            ):
                LOGGER.info("Resuming with completed chapter %d", chapter.number)
                chapter_files.append(chapter_path)
                completed_segments += len(chapter.segments)
                completed_chunks += chapter_chunk_counts[chapter.number]
                state["segments_completed"] = completed_chunks
                state["completed_chapter"] = chapter.number
                _save_state(state_path, state, state_callback)
                continue

            state["chapter_current"] = chapter_index
            chapters_state[key] = {
                "status": "running",
                "fingerprint": fingerprint,
                "file": chapter_path.name,
                "error": None,
            }
            _save_state(state_path, state, state_callback)

            def progress(current: int, total: int) -> None:
                state["segment_current"] = completed_chunks + current
                state["segment_total"] = total_chunks
                state["segments_completed"] = completed_chunks + current
                state["cache_hits"] = cache.metrics.hits
                state["cache_misses"] = cache.metrics.misses
                state["segments_generated"] = cache.metrics.generated
                _save_state(state_path, state, state_callback)

            generate_audiobook(
                chapter.segments,
                chapter_path,
                voices_by_speaker=voices_by_speaker,
                tts_provider=provider,
                cache=cache,
                settings=settings,
                pauses=pauses,
                chunk_max_chars=config.chunk_max_chars,
                force_regenerate=force_regenerate,
                trailing_pause_ms=(
                    config.chapter_pause_ms
                    if chapter_index < len(analysis.chapters)
                    else 0
                ),
                progress_callback=progress,
                cancel_check=lambda: bool(state.get("cancel_requested"))
                or (cancel_check() if cancel_check else False),
            )
            chapters_state[key]["status"] = "completed"
            chapter_files.append(chapter_path)
            completed_segments += len(chapter.segments)
            completed_chunks += chapter_chunk_counts[chapter.number]
            state["segments_completed"] = completed_chunks
            state["completed_chapter"] = chapter.number
            LOGGER.info("Chapter generated: %d", chapter.number)
            _save_state(state_path, state, state_callback)

        full_audiobook = full_audiobook_path or output_directory / "full_audiobook.wav"
        combine_wav_files(chapter_files, full_audiobook)
        state.update(
            status="completed",
            cache_hits=cache.metrics.hits,
            cache_misses=cache.metrics.misses,
            segments_generated=cache.metrics.generated,
            generation_time=round(time.monotonic() - started, 3),
        )
        _save_state(state_path, state, state_callback)
    except Exception as error:
        status = "cancelled" if isinstance(error, GenerationCancelled) else "failed"
        state.update(
            status=status,
            error=str(error),
            cache_hits=cache.metrics.hits,
            cache_misses=cache.metrics.misses,
            segments_generated=cache.metrics.generated,
            generation_time=round(time.monotonic() - started, 3),
        )
        current_key = str(analysis.chapters[max(0, int(state["chapter_current"]) - 1)].number)
        if current_key in chapters_state:
            chapters_state[current_key]["status"] = status
            chapters_state[current_key]["error"] = str(error)
        _save_state(state_path, state, state_callback)
        LOGGER.error("Audiobook generation %s: %s", status, error)
        raise

    metadata_path = output_directory / "metadata.json"
    write_metadata(
        {
            "title": analysis.document.title,
            "format": analysis.document.format,
            "chapter_count": len(analysis.chapters),
            "detected_characters": list(analysis.characters),
            "voice_assignments": dict(voices_by_speaker),
            "character_intelligence": character_metrics(analysis),
            "analysis_warnings": list(analysis_warnings(analysis)),
            "character_profiles": analysis.registry.to_list(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": analysis.document.source_path.name,
            "generation": {
                "segments_total": total_chunks,
                "segments_generated": cache.metrics.generated,
                "cache_hits": cache.metrics.hits,
                "cache_misses": cache.metrics.misses,
                "generation_time": state["generation_time"],
                "provider": provider_id(provider),
                "model": provider_model(provider),
            },
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
        generation_state=state_path,
    )


def character_metrics(analysis: BookAnalysis) -> dict[str, int]:
    threshold = CharacterConfig.from_environment().accept_confidence
    segments = [s for ch in analysis.chapters for s in ch.segments]
    spoken = [s for s in segments if s.type == "dialogue"]
    dialogues = [s for s in segments if s.type in {"dialogue", "internal_thought"}]
    profiles = [p for p in analysis.registry.profiles if p.canonical_name != "Narrator"]
    return {
        "narration_segments": sum(s.type == "narration" for s in segments),
        "dialogue_segments": len(spoken),
        "internal_thought_segments": sum(s.type == "internal_thought" for s in segments),
        "resolved_dialogues": sum(s.speaker not in {"Unknown", "Narrator"} for s in spoken),
        "unresolved_dialogues": sum(s.speaker in {"Unknown", "Narrator"} for s in spoken),
        "characters_detected": sum(not p.pending for p in profiles),
        **analysis.resolution_diagnostics,
        "speakers_resolved_by_rules": sum(s.resolution_method in {"rule", "conversation", "candidate"} for s in dialogues),
        "speakers_resolved_by_llm": sum(s.resolution_method == "llm" and s.speaker != "Unknown" for s in dialogues),
        "speakers_resolved_manually": sum(s.resolution_method == "manual" for s in dialogues),
        "unresolved_segments": sum(s.speaker == "Unknown" for s in dialogues),
        "low_confidence_segments": sum(s.confidence is not None and s.confidence < threshold for s in dialogues),
        "characters_total": len(profiles),
        "major_characters": sum(p.importance == "major" for p in profiles),
        "supporting_characters": sum(p.importance == "supporting" for p in profiles),
        "minor_characters": sum(p.importance == "minor" for p in profiles),
        "llm_calls_saved": analysis.llm_calls_saved,
    }


def analysis_warnings(analysis: BookAnalysis) -> tuple[str, ...]:
    metrics = character_metrics(analysis)
    warnings = []
    if metrics["dialogue_segments"] and not metrics["characters_detected"]:
        warnings.append("Dialogue was detected but no character speakers were resolved. Check speaker detection configuration.")
    if metrics.get("llm_errors", 0):
        warnings.append("Gemini/LLM failed during analysis. Unresolved segments remain Unknown; check the safe server logs and configuration.")
    return tuple(warnings)


def request_cancellation(state_path: Path) -> None:
    state = _load_state(state_path)
    if state:
        state["cancel_requested"] = True
        _save_state(state_path, state)


def _validate_assignments(
    segments: tuple[TextSegment, ...],
    assignments: dict[str, str],
    voice_ids: list[str],
) -> None:
    used = {segment.speaker for segment in segments}
    missing = sorted(used.difference(assignments))
    invalid = sorted(set(assignments.values()).difference(voice_ids))
    if missing:
        raise SpeechGenerationError("Falta una voz para: " + ", ".join(missing))
    if invalid:
        raise SpeechGenerationError("Hay voces seleccionadas que ya no están disponibles.")


def _chapter_fingerprint(
    chapter: AnalyzedChapter,
    assignments: dict[str, str],
    provider: object,
    settings: SynthesisSettings,
    pauses: PauseSettings,
    config: TTSConfig,
) -> str:
    payload = {
        "segments": [
            {"text": item.text, "speaker": item.speaker, "voice": assignments[item.speaker],
             "scene_break_before": item.scene_break_before}
            for item in chapter.segments
        ],
        "provider": provider_id(provider),
        "model": provider_model(provider),
        "settings": settings.as_dict(),
        "pauses": pauses.as_dict(),
        "chapter_pause_ms": config.chapter_pause_ms,
        "chunk_max_chars": config.chunk_max_chars,
    }
    value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(
    path: Path,
    state: dict[str, Any],
    callback: StateCallback | None = None,
) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    if callback:
        callback(dict(state))
