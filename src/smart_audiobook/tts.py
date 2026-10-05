"""Text-to-speech service for generating local audio files."""

from pathlib import Path

import pyttsx3


class SpeechGenerationError(RuntimeError):
    """Raised when the speech engine cannot generate an audio file."""


def synthesize_to_wav(text: str, output_path: Path) -> Path:
    """Convert text to a WAV file using the operating system speech engine."""
    if output_path.suffix.lower() != ".wav":
        raise SpeechGenerationError("El archivo de salida debe tener extensión .wav.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        engine = pyttsx3.init()
        engine.save_to_file(text, str(output_path.resolve()))
        engine.runAndWait()
        engine.stop()
    except Exception as error:
        raise SpeechGenerationError(
            f"No se pudo generar el audio con el motor de voz local: {error}"
        ) from error

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise SpeechGenerationError(
            "El motor de voz terminó sin crear un archivo de audio válido."
        )

    return output_path

