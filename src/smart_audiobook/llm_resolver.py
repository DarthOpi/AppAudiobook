"""Provider-independent LLM fallback for dialogue speaker resolution."""

from typing import Any

from smart_audiobook.llm_providers import LLMProvider
from smart_audiobook.models import SpeakerContext, SpeakerResolution

SPEAKER_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "speaker": {
            "type": "string",
            "description": "Character name or Unknown when evidence is insufficient.",
        },
        "confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Confidence between 0 and 1.",
        },
    },
    "required": ["speaker", "confidence"],
    "additionalProperties": False,
}


class LLMResolver:
    """Resolve speakers using any provider that returns structured data."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def resolve(self, context: SpeakerContext) -> SpeakerResolution | None:
        """Build domain context and validate the provider response."""
        data = self._provider.generate_structured(
            prompt=_build_prompt(context),
            response_schema=SPEAKER_RESPONSE_SCHEMA,
        )
        return _parse_speaker_response(data)


def _build_prompt(context: SpeakerContext) -> str:
    before = _render_segments(context.before) or "(sin contexto anterior)"
    after = _render_segments(context.after) or "(sin contexto posterior)"
    known = ", ".join(context.known_characters) or "(ninguno)"
    return (
        "Identifica quién pronuncia el diálogo indicado usando solo el contexto. "
        "Conserva la grafía del nombre. Si no hay evidencia suficiente, "
        "usa Unknown.\n\n"
        f"Personajes conocidos: {known}\n\n"
        f"Contexto anterior:\n{before}\n\n"
        f"Diálogo a resolver:\n{context.dialogue.text}\n\n"
        f"Contexto posterior:\n{after}"
    )


def _render_segments(segments: tuple[Any, ...]) -> str:
    return "\n".join(
        f"[{segment.type} | {segment.speaker}] {segment.text}"
        for segment in segments
    )


def _parse_speaker_response(
    data: dict[str, Any] | None,
) -> SpeakerResolution | None:
    if data is None:
        return None
    speaker = data.get("speaker")
    confidence = data.get("confidence")
    if not isinstance(speaker, str) or not speaker.strip():
        return None
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return None
    if not 0 <= float(confidence) <= 1:
        return None
    return SpeakerResolution(
        speaker=speaker.strip(),
        confidence=float(confidence),
        source="llm",
    )
