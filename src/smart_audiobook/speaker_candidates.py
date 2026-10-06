"""Compact temporal candidate lists and conservative conversation signals."""

from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.models import NARRATOR, UNKNOWN_SPEAKER, SpeakerContext, SpeakerResolution


def candidate_speakers(context: SpeakerContext, registry: CharacterRegistry, limit: int = 6) -> tuple[str, ...]:
    scores: dict[str, float] = {}
    for offset, segment in enumerate(reversed(context.before)):
        for name in registry.mentions(segment.text, context.chapter):
            scores[name] = scores.get(name, 0) + 4 / (offset + 1)
        if segment.type == "dialogue" and segment.speaker not in {NARRATOR, UNKNOWN_SPEAKER}:
            profile = registry.find(segment.speaker, context.chapter)
            if profile and not profile.pending:
                scores[profile.canonical_name] = scores.get(profile.canonical_name, 0) + 2
    for name in context.active_characters:
        scores[name] = scores.get(name, 0) + 1
    for segment in context.after:
        for name in registry.mentions(segment.text, context.chapter):
            scores[name] = scores.get(name, 0) + 2
    scores.pop(NARRATOR, None)
    scores = {name: score for name, score in scores.items()
              if (profile := registry.find(name, context.chapter)) is not None and not profile.pending}
    return tuple(sorted(scores, key=lambda name: (-scores[name], name))[:limit])


def candidate_resolution(context: SpeakerContext, registry: CharacterRegistry) -> SpeakerResolution | None:
    """An exact known-name label before a dialogue is stronger than mere mention."""
    if not context.before or context.before[-1].type != "narration":
        return None
    label = context.before[-1].text.strip()
    if not label.endswith(":"):
        return None
    profile = registry.find(label[:-1], context.chapter)
    if profile and not profile.pending and profile.canonical_name in context.candidate_speakers:
        return SpeakerResolution(profile.canonical_name, .97, "candidate")
    return None


def conversation_resolution(context: SpeakerContext) -> SpeakerResolution | None:
    """A-B-A + a short reply is evidence for B only in a two-person exchange."""
    speakers = context.previous_speakers[-3:]
    if len(speakers) != 3 or speakers[0] != speakers[2] or speakers[0] == speakers[1]:
        return None
    if set(context.candidate_speakers) != set(speakers):
        return None
    if len(context.dialogue.text) > 220 or any(
        s.type == "narration" and not s.text.lstrip().lower().startswith(
            ("dijo", "respondió", "preguntó", "contestó", "replicó")
        ) for s in context.before
    ):
        return None
    return SpeakerResolution(speakers[1], 0.86, "conversation")
