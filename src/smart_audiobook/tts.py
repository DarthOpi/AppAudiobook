"""Text-to-speech service for generating local audio files."""

from pathlib import Path

import pyttsx3


class SpeechGenerationError(RuntimeError):
    """Raised when the speech engine cannot generate an audio file."""


def get_available_voice_ids() -> list[str]:
    """Return the identifiers of voices installed in the local speech engine."""
    engine = None
    try:
        engine = pyttsx3.init()
        return list(dict.fromkeys(voice.id for voice in engine.getProperty("voices")))
    except Exception as error:
        raise SpeechGenerationError(
            f"No se pudieron consultar las voces instaladas: {error}"
        ) from error
    finally:
        if engine is not None:
            engine.stop()


def synthesize_to_wav(
    text: str,
    output_path: Path,
    voice_id: str | None = None,
) -> Path:
    """Convert text to a WAV file using the operating system speech engine."""
    if output_path.suffix.lower() != ".wav":
        raise SpeechGenerationError("El archivo de salida debe tener extensión .wav.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    engine = None
    try:
        engine = pyttsx3.init()
        if voice_id is not None:
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
