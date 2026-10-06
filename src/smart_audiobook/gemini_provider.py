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

            client = genai.Client(api_key=api_key, http_options={"timeout": 30000})
        self._client = client
        self._model = model
        self.calls = 0
        self.failures = 0
        self.last_error: str | None = None

    @property
    def model(self) -> str:
        """Expose model identity for provider-neutral cache invalidation."""
        return self._model

    def generate_structured(
        self,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Request JSON constrained by a schema and parse the returned object."""
        try:
            self.calls += 1
            LOGGER.info("Gemini requested: model=%s call=%d", self.model, self.calls)
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
            self.last_error = None
        except Exception as error:
            self.failures += 1
            # Error class and HTTP code only; SDK messages can include sensitive data.
            code = getattr(error, "code", None)
            self.last_error = f"{type(error).__name__}" + (f" (HTTP {code})" if isinstance(code, int) else "")
            LOGGER.warning(
                "Gemini request could not be completed: %s; speaker remains Unknown",
                self.last_error,
            )
            return None
        if not isinstance(data, dict):
            self.failures += 1
            self.last_error = "NonObjectJSON"
            LOGGER.warning("Gemini response rejected: expected JSON object; speaker remains Unknown")
            return None
        return data
