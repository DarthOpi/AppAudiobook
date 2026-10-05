"""Hybrid orchestration for dialogue speaker identification."""

from dataclasses import replace
from typing import Sequence

from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.models import (
    NARRATOR,
    SpeakerAnalysis,
    SpeakerContext,
    SpeakerResolution,
    TextSegment,
    UNKNOWN_SPEAKER,
)
from smart_audiobook.speaker_resolvers import SpeakerResolver


class SpeakerIdentificationService:
    """Apply deterministic rules before an optional cached LLM fallback."""

    def __init__(
        self,
        rule_resolver: SpeakerResolver,
        llm_resolver: SpeakerResolver | None = None,
        context_window: int = 2,
    ) -> None:
        if context_window < 0:
            raise ValueError("La ventana de contexto no puede ser negativa.")

        self._rule_resolver = rule_resolver
        self._llm_resolver = llm_resolver
        self._context_window = context_window
        self._llm_cache: dict[tuple[object, ...], SpeakerResolution | None] = {}

    def identify(self, segments: Sequence[TextSegment]) -> SpeakerAnalysis:
        """Return segments enriched with canonical speaker names."""
        registry = CharacterRegistry()
        resolved_segments = list(segments)

        for index, segment in enumerate(resolved_segments):
            if segment.type == "narration":
                resolved_segments[index] = replace(segment, speaker=NARRATOR)
                continue

            context = self._build_context(resolved_segments, index, registry)
            resolution = self._rule_resolver.resolve(context)
            if resolution is None:
                resolution = self._resolve_with_llm(context)

            speaker = (
                registry.register(resolution.speaker)
                if resolution is not None
                else UNKNOWN_SPEAKER
            )
            resolved_segments[index] = replace(segment, speaker=speaker)

        return SpeakerAnalysis(
            segments=tuple(resolved_segments),
            characters=registry.characters,
        )

    def _build_context(
        self,
        segments: list[TextSegment],
        index: int,
        registry: CharacterRegistry,
    ) -> SpeakerContext:
        start = max(0, index - self._context_window)
        end = min(len(segments), index + self._context_window + 1)
        return SpeakerContext(
            dialogue=segments[index],
            before=tuple(segments[start:index]),
            after=tuple(segments[index + 1 : end]),
            known_characters=registry.characters,
        )

    def _resolve_with_llm(
        self,
        context: SpeakerContext,
    ) -> SpeakerResolution | None:
        if self._llm_resolver is None:
            return None

        cache_key = context.cache_key()
        if cache_key not in self._llm_cache:
            self._llm_cache[cache_key] = self._llm_resolver.resolve(context)
        return self._llm_cache[cache_key]

