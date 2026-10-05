"""Tests for Gemini response validation without external API calls."""

import unittest
from types import SimpleNamespace

from smart_audiobook.gemini_resolver import GeminiSpeakerResolver
from smart_audiobook.models import SpeakerContext, UNKNOWN_SPEAKER, TextSegment


class FakeModels:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text

    def generate_content(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(text=self.output_text)


class GeminiSpeakerResolverTests(unittest.TestCase):
    def make_context(self) -> SpeakerContext:
        return SpeakerContext(
            dialogue=TextSegment(
                type="dialogue",
                text="No pienso hacerlo.",
                speaker=UNKNOWN_SPEAKER,
            ),
            before=(),
            after=(),
            known_characters=("Narrator",),
        )

    def test_valid_structured_response_is_accepted(self) -> None:
        client = SimpleNamespace(
            models=FakeModels('{"speaker": "María", "confidence": 0.91}')
        )
        resolver = GeminiSpeakerResolver(api_key="test", client=client)

        resolution = resolver.resolve(self.make_context())

        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.speaker, "María")
        self.assertEqual(resolution.confidence, 0.91)

    def test_invalid_response_is_rejected(self) -> None:
        client = SimpleNamespace(models=FakeModels("not valid JSON"))
        resolver = GeminiSpeakerResolver(api_key="test", client=client)

        self.assertIsNone(resolver.resolve(self.make_context()))

    def test_semantically_invalid_response_is_rejected(self) -> None:
        client = SimpleNamespace(
            models=FakeModels('{"speaker": "María", "confidence": 5}')
        )
        resolver = GeminiSpeakerResolver(api_key="test", client=client)

        self.assertIsNone(resolver.resolve(self.make_context()))

    def test_missing_response_text_is_rejected(self) -> None:
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=lambda **kwargs: SimpleNamespace()
            )
        )
        resolver = GeminiSpeakerResolver(api_key="test", client=client)

        self.assertIsNone(resolver.resolve(self.make_context()))


if __name__ == "__main__":
    unittest.main()
