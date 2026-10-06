"""Gemini wiring and compatibility facade for speaker resolution."""

import os
import logging
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
        logging.getLogger(__name__).info("Gemini disabled: GEMINI_API_KEY missing; ambiguous segments remain Unknown")
        return None

    try:
        options = {"model": os.environ["GEMINI_MODEL"]} if os.getenv("GEMINI_MODEL") else {}
        provider = GeminiProvider(api_key=api_key, **options)
    except (ImportError, ValueError) as error:
        logging.getLogger(__name__).warning("Gemini initialization failed: %s; ambiguous segments remain Unknown", type(error).__name__)
        return None
    return LLMResolver(provider)
