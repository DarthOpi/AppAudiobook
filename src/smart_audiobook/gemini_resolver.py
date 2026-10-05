"""Gemini adapter for ambiguous dialogue speaker identification."""

import json
import os
from typing import Any

from smart_audiobook.models import SpeakerContext, SpeakerResolution

DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite"

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "speaker": {
            "type": "string",
            "description": "Character name or Unknown when evidence is insufficient.",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence between 0 and 1.",
        },
    },
    "required": ["speaker", "confidence"],
    "additionalProperties": False,
}


class GeminiSpeakerResolver:
    """Resolve ambiguous speakers through the isolated Gemini API adapter."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        client: Any | None = None,
    ) -> None:
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            config: Any = types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
            )
        else:
            config = {
                "response_mime_type": "application/json",
                "response_schema": _RESPONSE_SCHEMA,
            }

        self._client = client
        self._model = model
        self._config = config

    def resolve(self, context: SpeakerContext) -> SpeakerResolution | None:
        """Ask Gemini for a structured speaker prediction and validate it."""
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=_build_prompt(context),
                config=self._config,
            )
        except Exception:
            return None

        try:
            output_text = response.text
        except Exception:
            return None

        return _parse_gemini_response(output_text)


def build_gemini_resolver_from_environment() -> GeminiSpeakerResolver | None:
    """Create the optional Gemini adapter when its API key is configured."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        return GeminiSpeakerResolver(api_key=api_key)
    except ImportError:
        return None


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


def _parse_gemini_response(output_text: str) -> SpeakerResolution | None:
    try:
        data = json.loads(output_text)
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
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
