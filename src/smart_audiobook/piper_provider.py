"""Optional local neural TTS adapter for Piper."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import (
    ProviderCapabilities,
    SynthesisSettings,
    VoiceInfo,
    _validate_generated_file,
    _validate_wav_path,
)

LOGGER = logging.getLogger(__name__)
DeviceChoice = Literal["auto", "cpu", "cuda"]


@dataclass(frozen=True, slots=True)
class _PiperVoice:
    info: VoiceInfo
    model_path: Path
    config_path: Path
    speaker_id: int | None


class PiperTTSProvider:
    """Offline Piper provider with lazy model loading and multi-speaker support."""

    provider_id = "piper"
    capabilities = ProviderCapabilities(
        supports_speed=True, supports_volume=True, supports_gpu=True
    )

    def __init__(self, voices_directory: Path, device: DeviceChoice = "auto") -> None:
        if device not in {"auto", "cpu", "cuda"}:
            raise SpeechGenerationError("PIPER_DEVICE debe ser auto, cpu o cuda.")
        self.voices_directory = voices_directory.resolve()
        self.device = device
        self.use_cuda = self._resolve_cuda(device)
        self.model_id = f"piper:{self.voices_directory.as_posix()}"
        self._catalog: dict[str, _PiperVoice] | None = None
        self._loaded_models: dict[Path, Any] = {}
        self._lock = RLock()
        LOGGER.info(
            "Piper configured: device=%s voices=%s",
            "cuda" if self.use_cuda else "cpu",
            self.voices_directory,
        )
        if not self.use_cuda:
            LOGGER.info("Piper uses CPU; long books may take more time.")

    def list_voices(self) -> list[VoiceInfo]:
        return [entry.info for entry in self._voice_catalog().values()]

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice_id: str,
        settings: SynthesisSettings | None = None,
    ) -> Path:
        _validate_wav_path(output_path)
        settings = settings or SynthesisSettings()
        settings.validate()
        if settings.pitch is not None or settings.style is not None:
            raise SpeechGenerationError("Piper no admite pitch ni style.")
        try:
            selected = self._voice_catalog()[voice_id]
        except KeyError as error:
            raise SpeechGenerationError(
                f"La voz Piper '{voice_id}' no está disponible."
            ) from error
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as error:
            raise SpeechGenerationError(
                "Piper no está instalado. Ejecuta: pip install -e .[local-tts]"
            ) from error
        with self._lock:
            voice = self._loaded_models.get(selected.model_path)
            if voice is None:
                LOGGER.info("Loading Piper model: %s", selected.model_path.name)
                try:
                    voice = PiperVoice.load(
                        selected.model_path,
                        config_path=selected.config_path,
                        use_cuda=self.use_cuda,
                    )
                except Exception:
                    if self.device != "auto" or not self.use_cuda:
                        raise
                    LOGGER.warning(
                        "CUDA was detected but could not load Piper; retrying on CPU."
                    )
                    self.use_cuda = False
                    voice = PiperVoice.load(
                        selected.model_path,
                        config_path=selected.config_path,
                        use_cuda=False,
                    )
                self._loaded_models[selected.model_path] = voice
        config = SynthesisConfig(
            speaker_id=selected.speaker_id,
            length_scale=1.0 / settings.speed,
            volume=settings.volume,
        )
        try:
            import wave

            with wave.open(str(output_path), "wb") as wav_file:
                voice.synthesize_wav(text, wav_file, syn_config=config)
        except Exception as error:
            output_path.unlink(missing_ok=True)
            raise SpeechGenerationError(
                f"Piper no pudo sintetizar el segmento: {error}"
            ) from error
        _validate_generated_file(output_path)
        return output_path

    def _voice_catalog(self) -> dict[str, _PiperVoice]:
        if self._catalog is not None:
            return self._catalog
        if not self.voices_directory.is_dir():
            raise SpeechGenerationError(
                f"No existe el directorio de voces Piper: {self.voices_directory}"
            )
        catalog: dict[str, _PiperVoice] = {}
        for model_path in sorted(self.voices_directory.rglob("*.onnx")):
            config_path = Path(f"{model_path}.json")
            if not config_path.is_file():
                LOGGER.warning("Ignoring Piper model without config: %s", model_path)
                continue
            catalog.update(self._read_model_voices(model_path, config_path))
        if not catalog:
            raise SpeechGenerationError(
                "No hay modelos Piper instalados. Descarga una voz oficial en voices/."
            )
        self._catalog = catalog
        return catalog

    def _read_model_voices(
        self, model_path: Path, config_path: Path
    ) -> dict[str, _PiperVoice]:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SpeechGenerationError(
                f"Configuración Piper no válida: {config_path.name}"
            ) from error
        language = data.get("language", {})
        locale = language.get("code") if isinstance(language, dict) else None
        relative = model_path.relative_to(self.voices_directory).as_posix()
        speakers = data.get("speaker_id_map") or {}
        if not speakers:
            voice_id = relative
            info = VoiceInfo(
                id=voice_id,
                name=_model_display_name(model_path),
                language=str(locale) if locale else None,
                provider=self.provider_id,
                description="Modelo local Piper",
            )
            return {voice_id: _PiperVoice(info, model_path, config_path, None)}
        result: dict[str, _PiperVoice] = {}
        for speaker_name, speaker_id in speakers.items():
            voice_id = f"{relative}::{speaker_id}"
            info = VoiceInfo(
                id=voice_id,
                name=f"{_model_display_name(model_path)} · {speaker_name}",
                language=str(locale) if locale else None,
                provider=self.provider_id,
                description="Speaker de un modelo local Piper",
            )
            result[voice_id] = _PiperVoice(
                info, model_path, config_path, int(speaker_id)
            )
        return result

    @staticmethod
    def _resolve_cuda(device: DeviceChoice) -> bool:
        if device == "cpu":
            return False
        try:
            import onnxruntime

            available = "CUDAExecutionProvider" in onnxruntime.get_available_providers()
        except ImportError:
            available = False
        if device == "cuda" and not available:
            raise SpeechGenerationError(
                "Se solicitó CUDA, pero onnxruntime-gpu/CUDA no está disponible."
            )
        if device == "auto" and not available:
            LOGGER.info("CUDA not detected; Piper will fall back to CPU.")
        return available


def _model_display_name(path: Path) -> str:
    return path.stem.replace("_", " ").replace("-", " ").strip().title()
