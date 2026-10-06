"""Persistent content-addressed cache for synthesized WAV segments."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from smart_audiobook.tts_providers import (
    SynthesisSettings,
    TTSProvider,
    provider_id,
    provider_model,
)

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CacheMetrics:
    hits: int = 0
    misses: int = 0
    generated: int = 0


def audio_cache_key(
    text: str,
    voice_id: str,
    provider: object,
    settings: SynthesisSettings | None = None,
) -> str:
    """Build a stable key that invalidates on every audible input."""
    payload = {
        "schema": 1,
        "text": text,
        "voice_id": voice_id,
        "provider": provider_id(provider),
        "model": provider_model(provider),
        "settings": (settings or SynthesisSettings()).as_dict(),
    }
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AudioCache:
    """Store immutable WAV files and publish each entry atomically."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.metrics = CacheMetrics()
        self._lock = RLock()

    def path_for(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.wav"

    def synthesize(
        self,
        provider: TTSProvider,
        text: str,
        voice_id: str,
        settings: SynthesisSettings | None = None,
        force: bool = False,
    ) -> tuple[Path, bool]:
        settings = settings or SynthesisSettings()
        key = audio_cache_key(text, voice_id, provider, settings)
        target = self.path_for(key)
        with self._lock:
            if not force and target.is_file() and target.stat().st_size:
                self.metrics.hits += 1
                LOGGER.info("TTS cache hit: %s", key[:12])
                return target, True
            self.metrics.misses += 1
            LOGGER.info("TTS cache miss: %s", key[:12])
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp.wav")
            temporary.unlink(missing_ok=True)
            try:
                if settings == SynthesisSettings():
                    provider.synthesize(text, temporary, voice_id)
                else:
                    provider.synthesize(text, temporary, voice_id, settings)
                temporary.replace(target)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            self.metrics.generated += 1
            LOGGER.info("TTS segment synthesized: %s", key[:12])
            return target, False
