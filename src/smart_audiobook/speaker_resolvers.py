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
    "ordenó",
    "comentó",
)
_NAME_TOKEN = r"[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'-]*"
_NAME_PATTERN = rf"{_NAME_TOKEN}(?:\s+(?:(?:from|of|de|del|la)\s+)?{_NAME_TOKEN})*"
_ATTRIBUTION_PATTERN = re.compile(
    rf"\b(?i:{'|'.join(_SPEECH_VERBS)})\s+(?P<speaker>{_NAME_PATTERN})"
)
_BEFORE_ATTRIBUTION_PATTERN = re.compile(
    rf"(?P<speaker>{_NAME_PATTERN})(?:\s+(?i:abrió|se volvió|sonrió|asintió|se acercó)\b[^:;.!?]{{0,100}}?\s+y)?\s+(?i:{'|'.join(_SPEECH_VERBS)})\s*:\s*$"
)


class SpeakerResolver(Protocol):
    """Contract implemented by dialogue speaker resolution strategies."""

    def resolve(self, context: SpeakerContext) -> SpeakerResolution | None:
        """Resolve the dialogue speaker or return ``None`` when uncertain."""


class RuleBasedSpeakerResolver:
    """Resolve explicit Spanish dialogue attributions with deterministic rules."""

    def resolve(self, context: SpeakerContext) -> SpeakerResolution | None:
        """Look for a speech verb followed by a proper name after the dialogue."""
        match = None
        if context.dialogue.type == "internal_thought":
            # No speech attribution or A-B alternation for a private thought.
            thought = re.search(rf"(?P<speaker>{_NAME_PATTERN})\s+(?:pensó|se dijo)(?:\s+para sí)?[.:]?\s*$", context.before[-1].text) if context.before else None
            if thought and thought.group("speaker") not in {"La", "El", "Una", "Un"}:
                return SpeakerResolution(thought.group("speaker"), .99, "rule")
            return None
        self_identification = re.search(rf"(?:[Mm]i nombre es|[Yy]o,)\s+(?P<speaker>{_NAME_PATTERN})(?:[,!.?]|$)", context.dialogue.text)
        if self_identification:
            return SpeakerResolution(self_identification.group("speaker"), .99, "rule")
        if context.after and context.after[0].type == "narration":
            match = _ATTRIBUTION_PATTERN.search(context.after[0].text)
        if match is None and context.before and context.before[-1].type == "narration":
            match = _BEFORE_ATTRIBUTION_PATTERN.search(context.before[-1].text)
        if match is None:
            return None
        if match.group("speaker") in {"La", "El", "Una", "Un", "Ella", "Él", "Narrator", "Unknown"}:
            return None

        return SpeakerResolution(
            speaker=match.group("speaker"),
            confidence=0.99,
            source="rule",
        )
