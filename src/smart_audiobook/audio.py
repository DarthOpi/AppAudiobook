"""Cached segment synthesis and streaming WAV composition."""

from __future__ import annotations

import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from smart_audiobook.audio_cache import AudioCache
from smart_audiobook.models import TextSegment
from smart_audiobook.text_chunking import chunk_text
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import (
    LocalTTSProvider,
    SynthesisSettings,
    TTSProvider,
)
from smart_audiobook.voice_assignment import assign_voices

ProgressCallback = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


class GenerationCancelled(SpeechGenerationError):
    """Raised between segments so already cached work remains reusable."""


@dataclass(frozen=True, slots=True)
class PauseSettings:
    segment_ms: int = 180
    paragraph_ms: int = 320
    speaker_change_ms: int = 260

    def as_dict(self) -> dict[str, int]:
        return {
            "segment_ms": self.segment_ms,
            "paragraph_ms": self.paragraph_ms,
            "speaker_change_ms": self.speaker_change_ms,
        }


def generate_audiobook(
    segments: Sequence[TextSegment],
    output_path: Path,
    voices_by_speaker: Mapping[str, str] | None = None,
    tts_provider: TTSProvider | None = None,
    *,
    cache: AudioCache | None = None,
    settings: SynthesisSettings | None = None,
    pauses: PauseSettings | None = None,
    chunk_max_chars: int = 400,
    force_regenerate: bool = False,
    trailing_pause_ms: int = 0,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> Path:
    """Synthesize short chunks, cache them, then stream them into one WAV."""
    if not segments:
        raise SpeechGenerationError("No hay segmentos de texto para convertir.")
    provider = tts_provider or LocalTTSProvider()
    if voices_by_speaker is None:
        voice_ids = [voice.id for voice in provider.list_voices()]
        if not voice_ids:
            raise SpeechGenerationError("No se encontraron voces instaladas.")
        voices_by_speaker = assign_voices(segments, voice_ids)
    settings = settings or SynthesisSettings()
    pauses = pauses or PauseSettings()

    chunks: list[tuple[str, str, int]] = []
    previous: TextSegment | None = None
    for segment in segments:
        parts = chunk_text(segment.text, chunk_max_chars)
        for part_index, part in enumerate(parts):
            pause_ms = 0
            if chunks:
                if part_index:
                    pause_ms = pauses.paragraph_ms if "\n\n" in segment.text else pauses.segment_ms
                elif previous is not None and previous.speaker != segment.speaker:
                    pause_ms = pauses.speaker_change_ms
                else:
                    pause_ms = pauses.segment_ms
            chunks.append((part, voices_by_speaker[segment.speaker], pause_ms))
        previous = segment
    if not chunks:
        raise SpeechGenerationError("No hay texto utilizable para convertir.")

    with tempfile.TemporaryDirectory(prefix="smart-audiobook-") as directory:
        temporary_directory = Path(directory)
        active_cache = cache or AudioCache(temporary_directory / "cache")
        audio_parts: list[tuple[Path, int]] = []
        for index, (text, voice_id, pause_ms) in enumerate(chunks, start=1):
            if cancel_check and cancel_check():
                raise GenerationCancelled("La generación fue cancelada.")
            path, _hit = active_cache.synthesize(
                provider,
                text,
                voice_id,
                settings,
                force=force_regenerate,
            )
            audio_parts.append((path, pause_ms))
            if progress_callback:
                progress_callback(index, len(chunks))
        combine_wav_files_with_pauses(
            audio_parts, output_path, trailing_pause_ms=trailing_pause_ms
        )
    return output_path


def combine_wav_files(input_paths: Sequence[Path], output_path: Path) -> Path:
    """Compatibility wrapper that streams compatible WAV inputs."""
    return combine_wav_files_with_pauses([(path, 0) for path in input_paths], output_path)


def combine_wav_files_with_pauses(
    input_parts: Sequence[tuple[Path, int]],
    output_path: Path,
    trailing_pause_ms: int = 0,
) -> Path:
    if not input_parts:
        raise SpeechGenerationError("No hay fragmentos de audio para combinar.")
    if output_path.suffix.lower() != ".wav":
        raise SpeechGenerationError("El archivo de salida debe tener extensión .wav.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with wave.open(str(input_parts[0][0]), "rb") as first_file:
            expected_format = _audio_format(first_file)
        with wave.open(str(output_path), "wb") as output_file:
            output_file.setnchannels(expected_format[0])
            output_file.setsampwidth(expected_format[1])
            output_file.setframerate(expected_format[2])
            output_file.setcomptype(expected_format[3], expected_format[4])
            for input_path, pause_ms in input_parts:
                if pause_ms:
                    _write_silence(output_file, expected_format, pause_ms)
                with wave.open(str(input_path), "rb") as input_file:
                    actual_format = _audio_format(input_file)
                    if actual_format == expected_format:
                        while frames := input_file.readframes(16_384):
                            output_file.writeframesraw(frames)
                    else:
                        _write_resampled_pcm(output_file, input_file, expected_format)
            if trailing_pause_ms:
                _write_silence(output_file, expected_format, trailing_pause_ms)
    except (OSError, wave.Error) as error:
        output_path.unlink(missing_ok=True)
        raise SpeechGenerationError(
            f"No se pudieron combinar los fragmentos de audio: {error}"
        ) from error
    return output_path


def _write_silence(
    output_file: wave.Wave_write,
    audio_format: tuple[int, int, int, str, str],
    duration_ms: int,
) -> None:
    channels, sample_width, frame_rate, _compression, _name = audio_format
    frame_count = frame_rate * duration_ms // 1000
    silent_frame = b"\x00" * channels * sample_width
    block = silent_frame * min(frame_count, 16_384)
    remaining = frame_count
    while remaining:
        count = min(remaining, 16_384)
        output_file.writeframesraw(block[: count * len(silent_frame)])
        remaining -= count


def _write_resampled_pcm(
    output_file: wave.Wave_write,
    input_file: wave.Wave_read,
    target_format: tuple[int, int, int, str, str],
) -> None:
    """Normalize one short PCM chunk without retaining the rest of the book."""
    source_format = _audio_format(input_file)
    if (
        source_format[0] != target_format[0]
        or source_format[1] != 2
        or target_format[1] != 2
        or source_format[3] != "NONE"
        or target_format[3] != "NONE"
    ):
        raise SpeechGenerationError(
            "Los fragmentos requieren canales PCM de 16 bits compatibles."
        )
    try:
        import numpy as np
    except ImportError as error:
        raise SpeechGenerationError(
            "Se necesita numpy para combinar voces con frecuencias distintas. "
            "Instala el extra local-tts."
        ) from error
    raw = input_file.readframes(input_file.getnframes())
    samples = np.frombuffer(raw, dtype="<i2")
    channels = source_format[0]
    frames = samples.reshape(-1, channels)
    source_rate = source_format[2]
    target_rate = target_format[2]
    target_count = max(1, round(len(frames) * target_rate / source_rate))
    source_positions = np.arange(target_count, dtype=np.float64) * source_rate / target_rate
    source_axis = np.arange(len(frames), dtype=np.float64)
    converted = np.empty((target_count, channels), dtype=np.int16)
    for channel in range(channels):
        converted[:, channel] = np.clip(
            np.interp(source_positions, source_axis, frames[:, channel]),
            -32768,
            32767,
        ).astype(np.int16)
    output_file.writeframesraw(converted.astype("<i2", copy=False).tobytes())


def _audio_format(audio_file: wave.Wave_read) -> tuple[int, int, int, str, str]:
    return (
        audio_file.getnchannels(),
        audio_file.getsampwidth(),
        audio_file.getframerate(),
        audio_file.getcomptype(),
        audio_file.getcompname(),
    )
