"""Provider-neutral text-to-speech contracts and the system adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

import pyttsx3

from smart_audiobook.tts import SpeechGenerationError

VoiceStrategy = Literal["dedicated", "generic_pool"]


@dataclass(frozen=True, slots=True)
class VoiceInfo:
    """Provider-neutral metadata for a selectable voice."""

    id: str
    name: str
    language: str | None = None
    gender: str | None = None
    provider: str = "unknown"
    description: str | None = None
    style: str | None = None
    reference_audio: str | None = None
    strategy: VoiceStrategy = "dedicated"

    @property
    def display_name(self) -> str:
        """Keep the V0.6 ``name`` API while exposing the formal field name."""
        return self.name


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Optional features exposed without checking concrete provider names."""

    supports_speed: bool = False
    supports_pitch: bool = False
    supports_volume: bool = False
    supports_style: bool = False
    supports_voice_cloning: bool = False
    supports_gpu: bool = False


@dataclass(frozen=True, slots=True)
class SynthesisSettings:
    """Portable synthesis settings; providers reject unsupported non-defaults."""

    speed: float = 1.0
    pitch: float | None = None
    volume: float = 1.0
    style: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    def validate(self) -> None:
        if not 0.5 <= self.speed <= 2.0:
            raise SpeechGenerationError("La velocidad debe estar entre 0.5 y 2.0.")
        if not 0.0 <= self.volume <= 2.0:
            raise SpeechGenerationError("El volumen debe estar entre 0.0 y 2.0.")


@runtime_checkable
class TTSProvider(Protocol):
    """Contract implemented by local and future remote speech providers."""

    provider_id: str
    model_id: str
    capabilities: ProviderCapabilities

    def list_voices(self) -> list[VoiceInfo]: ...

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice_id: str,
        settings: SynthesisSettings | None = None,
    ) -> Path: ...


class SystemTTSProvider:
    """Operating-system TTS backed by ``pyttsx3`` (the V0.6 provider)."""

    provider_id = "system"
    model_id = "operating-system"
    capabilities = ProviderCapabilities(supports_speed=True, supports_volume=True)

    def list_voices(self) -> list[VoiceInfo]:
        engine = None
        try:
            engine = pyttsx3.init()
            voices: list[VoiceInfo] = []
            seen: set[str] = set()
            for voice in engine.getProperty("voices"):
                voice_id = str(voice.id)
                if voice_id in seen:
                    continue
                seen.add(voice_id)
                languages = getattr(voice, "languages", None) or []
                language = _clean_language(languages[0]) if languages else None
                voices.append(
                    VoiceInfo(
                        id=voice_id,
                        name=str(getattr(voice, "name", voice_id)),
                        language=language,
                        gender=_optional_text(getattr(voice, "gender", None)),
                        provider=self.provider_id,
                        description="Voz instalada en el sistema operativo",
                    )
                )
            return voices
        except Exception as error:
            raise SpeechGenerationError(
                f"No se pudieron consultar las voces instaladas: {error}"
            ) from error
        finally:
            if engine is not None:
                engine.stop()

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
            raise SpeechGenerationError("El proveedor system no admite pitch ni style.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        engine = None
        try:
            engine = pyttsx3.init()
            engine.setProperty("voice", voice_id)
            engine.setProperty("rate", int(engine.getProperty("rate") * settings.speed))
            engine.setProperty(
                "volume", min(1.0, float(engine.getProperty("volume")) * settings.volume)
            )
            engine.save_to_file(text, str(output_path.resolve()))
            engine.runAndWait()
        except Exception as error:
            raise SpeechGenerationError(
                f"No se pudo generar audio con el motor del sistema: {error}"
            ) from error
        finally:
            if engine is not None:
                engine.stop()
        _validate_generated_file(output_path)
        return output_path


# Backwards-compatible V0.6 name.
LocalTTSProvider = SystemTTSProvider


def provider_id(provider: object) -> str:
    return str(getattr(provider, "provider_id", provider.__class__.__name__))


def provider_model(provider: object) -> str:
    return str(getattr(provider, "model_id", "unspecified"))


def provider_capabilities(provider: object) -> ProviderCapabilities:
    value = getattr(provider, "capabilities", None)
    return value if isinstance(value, ProviderCapabilities) else ProviderCapabilities()


def _validate_wav_path(output_path: Path) -> None:
    if output_path.suffix.lower() != ".wav":
        raise SpeechGenerationError("El archivo de salida debe tener extensión .wav.")


def _validate_generated_file(output_path: Path) -> None:
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise SpeechGenerationError(
            "El motor de voz terminó sin crear un archivo de audio válido."
        )


def _clean_language(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").lstrip("\x05") or None
    return _optional_text(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
