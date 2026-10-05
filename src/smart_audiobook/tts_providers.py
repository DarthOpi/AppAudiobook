"""Provider-neutral text-to-speech contracts and the local adapter."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pyttsx3

from smart_audiobook.tts import SpeechGenerationError


@dataclass(frozen=True, slots=True)
class VoiceInfo:
    """Voice metadata exposed safely to application and presentation layers."""

    id: str
    name: str
    language: str | None = None
    gender: str | None = None


class TTSProvider(Protocol):
    """Contract that future local or remote speech providers can implement."""

    def list_voices(self) -> list[VoiceInfo]:
        """Return the currently available voices."""

    def synthesize(self, text: str, output_path: Path, voice_id: str) -> Path:
        """Generate a WAV file using one provider voice."""


class LocalTTSProvider:
    """Local operating-system TTS implementation backed by ``pyttsx3``."""

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

    def synthesize(self, text: str, output_path: Path, voice_id: str) -> Path:
        if output_path.suffix.lower() != ".wav":
            raise SpeechGenerationError(
                "El archivo de salida debe tener extensión .wav."
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        engine = None
        try:
            engine = pyttsx3.init()
            engine.setProperty("voice", voice_id)
            engine.save_to_file(text, str(output_path.resolve()))
            engine.runAndWait()
        except Exception as error:
            raise SpeechGenerationError(
                f"No se pudo generar el audio con el motor de voz local: {error}"
            ) from error
        finally:
            if engine is not None:
                engine.stop()
        if not output_path.is_file() or output_path.stat().st_size == 0:
            raise SpeechGenerationError(
                "El motor de voz terminó sin crear un archivo de audio válido."
            )
        return output_path


def _clean_language(value: object) -> str | None:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").lstrip("\x05") or None
    return _optional_text(value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
