"""Provider-neutral contract for structured LLM calls."""

from typing import Any, Protocol


class LLMProvider(Protocol):
    """Generate structured data without exposing a vendor SDK to the domain."""

    def generate_structured(
        self,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Return structured data or ``None`` when the provider cannot respond."""
