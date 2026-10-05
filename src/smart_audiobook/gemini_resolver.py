"""Gemini wiring and compatibility facade for speaker resolution."""

import os
from typing import Any

from smart_audiobook.gemini_provider import (
    DEFAULT_GEMINI_MODEL,
    GeminiProvider,
)
from smart_audiobook.llm_resolver import LLMResolver


class GeminiSpeakerResolver(LLMResolver):
    """Backward-compatible resolver composed from the Gemini provider."""

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_GEMINI_MODEL,
        client: Any | None = None,
    ) -> None:
        super().__init__(
            GeminiProvider(api_key=api_key, model=model, client=client)
        )


def build_gemini_resolver_from_environment() -> LLMResolver | None:
    """Build the Gemini fallback when ``GEMINI_API_KEY`` is configured."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    try:
        provider = GeminiProvider(api_key=api_key)
    except (ImportError, ValueError):
        return None
    return LLMResolver(provider)
