"""Tests for hybrid speaker identification and character consistency."""

import unittest

from smart_audiobook.characters import CharacterRegistry, normalize_character_name
from smart_audiobook.models import SpeakerResolution, UNKNOWN_SPEAKER
from smart_audiobook.segmenter import segment_text
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver


class StaticResolver:
    def __init__(self, resolution: SpeakerResolution | None) -> None:
        self.resolution = resolution
        self.calls = 0

    def resolve(self, context: object) -> SpeakerResolution | None:
        self.calls += 1
        return self.resolution


class SpeakerIdentificationTests(unittest.TestCase):
    def identify(self, text: str) -> tuple[list[str], tuple[str, ...]]:
        analysis = SpeakerIdentificationService(
            rule_resolver=RuleBasedSpeakerResolver()
        ).identify(segment_text(text))
        speakers = [segment.speaker for segment in analysis.segments]
        return speakers, analysis.characters

    def test_rule_detects_dijo_maria(self) -> None:
        speakers, characters = self.identify("—Hola —dijo María.")

        self.assertEqual(speakers, ["María", "Narrator"])
        self.assertEqual(characters, ("Narrator", "María"))

    def test_rule_detects_respondio_pedro(self) -> None:
        speakers, characters = self.identify("—Hola —respondió Pedro.")

        self.assertEqual(speakers, ["Pedro", "Narrator"])
        self.assertEqual(characters, ("Narrator", "Pedro"))

    def test_multiple_characters_alternate_dialogues(self) -> None:
        text = """—Hola —dijo María.
—Buenos días —respondió Pedro.
—Pasad —gritó Carlos."""

        speakers, characters = self.identify(text)

        self.assertEqual(
            speakers,
            ["María", "Narrator", "Pedro", "Narrator", "Carlos", "Narrator"],
        )
        self.assertEqual(characters, ("Narrator", "María", "Pedro", "Carlos"))

    def test_name_normalization_avoids_obvious_duplicates(self) -> None:
        registry = CharacterRegistry()

        self.assertEqual(registry.register("María"), "María")
        self.assertEqual(registry.register("MARÍA"), "María")
        self.assertEqual(registry.register("Maria"), "María")
        self.assertEqual(registry.register("UNKNOWN"), UNKNOWN_SPEAKER)
        self.assertEqual(normalize_character_name("  MARÍA "), "maria")
        self.assertEqual(registry.characters, ("Narrator", "María"))

    def test_llm_is_used_when_rules_cannot_resolve(self) -> None:
        llm_resolver = StaticResolver(
            SpeakerResolution(speaker="María", confidence=0.82, source="llm")
        )
        analysis = SpeakerIdentificationService(
            rule_resolver=RuleBasedSpeakerResolver(),
            llm_resolver=llm_resolver,
        ).identify(segment_text("—No pienso hacerlo."))

        self.assertEqual(analysis.segments[0].speaker, "María")
        self.assertEqual(llm_resolver.calls, 1)

    def test_unresolved_dialogue_becomes_unknown_without_llm(self) -> None:
        analysis = SpeakerIdentificationService(
            rule_resolver=RuleBasedSpeakerResolver()
        ).identify(segment_text("—No pienso hacerlo."))

        self.assertEqual(analysis.segments[0].speaker, UNKNOWN_SPEAKER)
        self.assertEqual(analysis.characters, ("Narrator",))

    def test_identical_llm_queries_are_cached_during_one_run(self) -> None:
        rule_resolver = StaticResolver(None)
        llm_resolver = StaticResolver(
            SpeakerResolution(speaker=UNKNOWN_SPEAKER, confidence=0.2, source="llm")
        )
        segments = segment_text("—Hola.\n—Hola.")

        SpeakerIdentificationService(
            rule_resolver=rule_resolver,
            llm_resolver=llm_resolver,
            context_window=0,
        ).identify(segments)

        self.assertEqual(llm_resolver.calls, 1)


if __name__ == "__main__":
    unittest.main()
