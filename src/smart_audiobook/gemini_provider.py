"""Google Gemini implementation of the provider-neutral LLM contract."""

import json
import logging
from typing import Any

DEFAULT_GEMINI_MODEL = "gemini-3.5-flash-lite"

LOGGER = logging.getLogger(__name__)


class GeminiProvider:
    """Access Gemini exclusively through the official ``google-genai`` SDK."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Gemini API key cannot be empty.")
        if client is None:
            from google import genai

            client = genai.Client(api_key=api_key)
        self._client = client
        self._model = model

    def generate_structured(
        self,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Request JSON constrained by a schema and parse the returned object."""
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": response_schema,
                    "temperature": 0.1,
                },
            )
            data = json.loads(response.text)
        except Exception as error:
            LOGGER.warning(
                "Gemini request could not be completed: %s",
                type(error).__name__,
            )
            return None
        return data if isinstance(data, dict) else None
