"""Text-to-speech service for generating local audio files."""

from pathlib import Path

class SpeechGenerationError(RuntimeError):
    """Raised when the speech engine cannot generate an audio file."""


def get_available_voice_ids() -> list[str]:
    """Return the identifiers of voices installed in the local speech engine."""
    from smart_audiobook.tts_providers import LocalTTSProvider

    return [voice.id for voice in LocalTTSProvider().list_voices()]


def synthesize_to_wav(
    text: str,
    output_path: Path,
    voice_id: str | None = None,
) -> Path:
    """Convert text to a WAV file using the operating system speech engine."""
    from smart_audiobook.tts_providers import LocalTTSProvider

    provider = LocalTTSProvider()
    selected_voice = voice_id
    if selected_voice is None:
        voices = provider.list_voices()
        if not voices:
            raise SpeechGenerationError("No se encontraron voces instaladas.")
        selected_voice = voices[0].id
    return provider.synthesize(text, output_path, selected_voice)
