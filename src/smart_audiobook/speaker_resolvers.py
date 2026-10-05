"""Provider-independent speaker resolver contracts and deterministic rules."""

import re
from typing import Protocol

from smart_audiobook.models import (
    SpeakerContext,
    SpeakerResolution,
)

_SPEECH_VERBS = (
    "dijo",
    "respondió",
    "preguntó",
    "gritó",
    "susurró",
    "exclamó",
    "contestó",
    "añadió",
    "replicó",
    "murmuró",
)
_NAME_TOKEN = r"[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'-]*"
_NAME_PATTERN = rf"{_NAME_TOKEN}(?:\s+{_NAME_TOKEN})*"
_ATTRIBUTION_PATTERN = re.compile(
    rf"\b(?i:{'|'.join(_SPEECH_VERBS)})\s+(?P<speaker>{_NAME_PATTERN})"
)


class SpeakerResolver(Protocol):
    """Contract implemented by dialogue speaker resolution strategies."""

    def resolve(self, context: SpeakerContext) -> SpeakerResolution | None:
        """Resolve the dialogue speaker or return ``None`` when uncertain."""


class RuleBasedSpeakerResolver:
    """Resolve explicit Spanish dialogue attributions with deterministic rules."""

    def resolve(self, context: SpeakerContext) -> SpeakerResolution | None:
        """Look for a speech verb followed by a proper name after the dialogue."""
        if not context.after:
            return None

        attribution = context.after[0]
        if attribution.type != "narration":
            return None

        match = _ATTRIBUTION_PATTERN.search(attribution.text)
        if match is None:
            return None

        return SpeakerResolution(
            speaker=match.group("speaker"),
            confidence=0.99,
            source="rule",
        )


