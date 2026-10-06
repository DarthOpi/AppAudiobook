"""Temporal hybrid resolution with confidence policy and project cache."""

import hashlib
import json
from dataclasses import asdict, replace
from typing import Sequence

from smart_audiobook.character_config import CharacterConfig
from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.models import NARRATOR, UNKNOWN_SPEAKER, SpeakerAnalysis, SpeakerContext, SpeakerResolution, TextSegment
from smart_audiobook.speaker_candidates import candidate_speakers, candidate_resolution, conversation_resolution
from smart_audiobook.speaker_resolvers import SpeakerResolver


class SpeakerIdentificationService:
    def __init__(self, rule_resolver: SpeakerResolver, llm_resolver: SpeakerResolver | None = None,
                 context_window: int = 4, config: CharacterConfig | None = None,
                 resolution_cache: dict[str, dict | None] | None = None) -> None:
        if context_window < 0:
            raise ValueError("La ventana de contexto no puede ser negativa.")
        self.config = config or CharacterConfig()
        self._rule_resolver = rule_resolver
        self._llm_resolver = llm_resolver
        self._context_window = context_window
        self.resolution_cache = resolution_cache if resolution_cache is not None else {}
        self.llm_calls_saved = 0

    def identify(self, segments: Sequence[TextSegment], registry: CharacterRegistry | None = None,
                 selected_ids: set[str] | None = None) -> SpeakerAnalysis:
        registry = registry or CharacterRegistry()
        resolved = list(segments)
        for index, segment in enumerate(resolved):
            if segment.resolution_method == "manual" or (
                selected_ids is not None and segment.id not in selected_ids
            ):
                registry.observe(segment)
                continue
            if segment.type == "narration":
                updated = replace(segment, speaker=NARRATOR, confidence=1.0, resolution_method="system")
            else:
                context = self._build_context(resolved, index, registry)
                resolution = self._rule_resolver.resolve(context)
                if resolution is None:
                    resolution = conversation_resolution(context)
                if resolution is None:
                    resolution = candidate_resolution(context, registry)
                if resolution is None:
                    resolution = self._resolve_with_llm(context)
                elif self._llm_resolver is not None:
                    self.llm_calls_saved += 1
                updated = self._apply_policy(segment, resolution, registry)
            resolved[index] = updated
            registry.observe(updated)
        return SpeakerAnalysis(tuple(resolved), registry.characters)

    def _apply_policy(self, segment: TextSegment, resolution: SpeakerResolution | None,
                      registry: CharacterRegistry) -> TextSegment:
        speaker, provisional = UNKNOWN_SPEAKER, None
        confidence = resolution.confidence if resolution else None
        if (resolution and resolution.is_new_character and resolution.speaker != UNKNOWN_SPEAKER
                and resolution.confidence >= self.config.review_confidence):
            provisional = registry.register(resolution.speaker, segment.chapter, pending=True)
        elif resolution and resolution.confidence >= self.config.review_confidence:
            profile = registry.find(resolution.speaker, max(1, segment.chapter))
            if profile and profile.pending:
                provisional = profile.canonical_name
            else:
                speaker = registry.register(resolution.speaker, segment.chapter)
        needs_review = speaker == UNKNOWN_SPEAKER or confidence is None or confidence < self.config.accept_confidence
        return replace(segment, speaker=speaker, confidence=confidence,
                       resolution_method=resolution.source if resolution else "unknown",
                       review_needed=needs_review, new_character_candidate=provisional)

    def _build_context(self, segments: list[TextSegment], index: int,
                       registry: CharacterRegistry) -> SpeakerContext:
        current = segments[index]
        chapter = max(1, current.chapter)
        before = tuple(s for s in segments[max(0, index-self._context_window):index] if s.chapter == current.chapter)
        after = tuple(replace(s, speaker=UNKNOWN_SPEAKER if s.type == "dialogue" else NARRATOR)
                      for s in segments[index+1:index+self._context_window+1] if s.chapter == current.chapter)
        previous = tuple(s.speaker for s in segments[max(0, index-8):index]
                         if s.chapter == current.chapter and s.type == "dialogue"
                         and s.speaker not in {NARRATOR, UNKNOWN_SPEAKER})
        context = SpeakerContext(current, before, after, (),
            active_characters=registry.active_characters(chapter, current.order, self.config.active_window),
            previous_speakers=previous, chapter=chapter)
        candidates = candidate_speakers(context, registry, self.config.candidate_limit)
        aliases = tuple((name, alias.name, alias.known_from_chapter)
                        for name in candidates
                        for alias in [a for a in registry.require(name).aliases
                                      if a.known_from_chapter <= chapter][:3])
        return replace(context, known_characters=candidates, candidate_speakers=candidates,
                       active_characters=context.active_characters[:self.config.candidate_limit],
                       candidate_aliases=aliases)

    def _resolve_with_llm(self, context: SpeakerContext) -> SpeakerResolution | None:
        if self._llm_resolver is None:
            return None
        resolver_key = getattr(self._llm_resolver, "cache_key", None)
        key = resolver_key(context) if callable(resolver_key) else hashlib.sha256(
            json.dumps([context.cache_key(), self.config.prompt_version], ensure_ascii=False).encode()
        ).hexdigest()
        if key in self.resolution_cache:
            self.llm_calls_saved += 1
            value = self.resolution_cache[key]
            return SpeakerResolution(**value) if value else None
        resolution = self._llm_resolver.resolve(context)
        self.resolution_cache[key] = asdict(resolution) if resolution else None
        return resolution
