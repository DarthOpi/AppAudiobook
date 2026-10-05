"""Audiobook generation and WAV concatenation."""

import tempfile
import wave
from pathlib import Path
from typing import Mapping, Sequence

from smart_audiobook.models import TextSegment
from smart_audiobook.tts import (
    SpeechGenerationError,
    get_available_voice_ids,
    synthesize_to_wav,
)
from smart_audiobook.voice_assignment import assign_voices


def generate_audiobook(
    segments: Sequence[TextSegment],
    output_path: Path,
    voices_by_speaker: Mapping[str, str] | None = None,
) -> Path:
    """Synthesize ordered segments with separate narration and dialogue voices."""
    if not segments:
        raise SpeechGenerationError("No hay segmentos de texto para convertir.")

    if voices_by_speaker is None:
        voice_ids = get_available_voice_ids()
        if not voice_ids:
            raise SpeechGenerationError("No se encontraron voces instaladas.")
        voices_by_speaker = assign_voices(segments, voice_ids)

    with tempfile.TemporaryDirectory(prefix="smart-audiobook-") as directory:
        temporary_directory = Path(directory)
        segment_files: list[Path] = []

        for index, segment in enumerate(segments):
            segment_path = temporary_directory / f"segment-{index:04d}.wav"
            synthesize_to_wav(
                segment.text,
                segment_path,
                voice_id=voices_by_speaker[segment.speaker],
            )
            segment_files.append(segment_path)

        combine_wav_files(segment_files, output_path)

    return output_path


def combine_wav_files(input_paths: Sequence[Path], output_path: Path) -> Path:
    """Combine compatible WAV files in order into a single output file."""
    if not input_paths:
        raise SpeechGenerationError("No hay fragmentos de audio para combinar.")
    if output_path.suffix.lower() != ".wav":
        raise SpeechGenerationError("El archivo de salida debe tener extensión .wav.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with wave.open(str(input_paths[0]), "rb") as first_file:
            expected_format = _audio_format(first_file)

        with wave.open(str(output_path), "wb") as output_file:
            output_file.setnchannels(expected_format[0])
            output_file.setsampwidth(expected_format[1])
            output_file.setframerate(expected_format[2])
            output_file.setcomptype(expected_format[3], expected_format[4])

            for input_path in input_paths:
                with wave.open(str(input_path), "rb") as input_file:
                    if _audio_format(input_file) != expected_format:
                        raise SpeechGenerationError(
                            "Los fragmentos de audio tienen formatos incompatibles."
                        )
                    frames = input_file.readframes(input_file.getnframes())
                    output_file.writeframes(frames)
    except (OSError, wave.Error) as error:
        raise SpeechGenerationError(
            f"No se pudieron combinar los fragmentos de audio: {error}"
        ) from error

    return output_path


def _audio_format(audio_file: wave.Wave_read) -> tuple[int, int, int, str, str]:
    """Return the WAV properties that must match before concatenation."""
    return (
        audio_file.getnchannels(),
        audio_file.getsampwidth(),
        audio_file.getframerate(),
        audio_file.getcomptype(),
        audio_file.getcompname(),
    )
