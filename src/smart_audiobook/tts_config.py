"""Centralized TTS configuration and provider construction."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from smart_audiobook.piper_provider import PiperTTSProvider
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import SystemTTSProvider, TTSProvider


@dataclass(frozen=True, slots=True)
class TTSConfig:
    provider: str = "piper"
    piper_voices_directory: Path = Path("voices")
    piper_device: str = "auto"
    chunk_max_chars: int = 400
    segment_pause_ms: int = 180
    paragraph_pause_ms: int = 320
    speaker_change_pause_ms: int = 260
    chapter_pause_ms: int = 700

    @classmethod
    def from_environment(cls, provider_override: str | None = None) -> "TTSConfig":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        try:
            config = cls(
                provider=(provider_override or os.getenv("TTS_PROVIDER", "piper")).casefold(),
                piper_voices_directory=Path(os.getenv("PIPER_VOICES_DIR", "voices")),
                piper_device=os.getenv("PIPER_DEVICE", "auto").casefold(),
                chunk_max_chars=int(os.getenv("TTS_CHUNK_MAX_CHARS", "400")),
                segment_pause_ms=int(os.getenv("TTS_SEGMENT_PAUSE_MS", "180")),
                paragraph_pause_ms=int(os.getenv("TTS_PARAGRAPH_PAUSE_MS", "320")),
                speaker_change_pause_ms=int(os.getenv("TTS_SPEAKER_CHANGE_PAUSE_MS", "260")),
                chapter_pause_ms=int(os.getenv("TTS_CHAPTER_PAUSE_MS", "700")),
            )
        except ValueError as error:
            raise SpeechGenerationError(
                "La configuración numérica de TTS no es válida."
            ) from error
        if config.provider not in {"piper", "system"}:
            raise SpeechGenerationError("TTS_PROVIDER debe ser piper o system.")
        if not 80 <= config.chunk_max_chars <= 2000:
            raise SpeechGenerationError(
                "TTS_CHUNK_MAX_CHARS debe estar entre 80 y 2000."
            )
        if any(
            value < 0
            for value in (
                config.segment_pause_ms,
                config.paragraph_pause_ms,
                config.speaker_change_pause_ms,
                config.chapter_pause_ms,
            )
        ):
            raise SpeechGenerationError("Las pausas TTS no pueden ser negativas.")
        return config


def build_tts_provider(config: TTSConfig | None = None) -> TTSProvider:
    config = config or TTSConfig.from_environment()
    if config.provider == "piper":
        return PiperTTSProvider(
            config.piper_voices_directory,
            device=config.piper_device,  # type: ignore[arg-type]
        )
    if config.provider == "system":
        return SystemTTSProvider()
    raise SpeechGenerationError(f"Proveedor TTS no soportado: {config.provider}")
