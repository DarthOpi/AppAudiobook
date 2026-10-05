"""Tests for Gemini response validation without external API calls."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from smart_audiobook.gemini_provider import GeminiProvider
from smart_audiobook.gemini_resolver import GeminiSpeakerResolver
from smart_audiobook.gemini_resolver import build_gemini_resolver_from_environment
from smart_audiobook.llm_resolver import LLMResolver
from smart_audiobook.models import SpeakerContext, UNKNOWN_SPEAKER, TextSegment


class FakeModels:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.last_request: dict[str, object] | None = None

    def generate_content(self, **kwargs: object) -> SimpleNamespace:
        self.last_request = kwargs
        return SimpleNamespace(text=self.output_text)


class FakeProvider:
    def __init__(self, response: dict[str, object] | None) -> None:
        self.response = response
        self.prompt = ""
        self.schema: dict[str, object] = {}

    def generate_structured(
        self,
        prompt: str,
        response_schema: dict[str, object],
    ) -> dict[str, object] | None:
        self.prompt = prompt
        self.schema = response_schema
        return self.response


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

    def test_gemini_provider_uses_structured_json_configuration(self) -> None:
        models = FakeModels('{"speaker": "María", "confidence": 0.8}')
        provider = GeminiProvider(
            api_key="test-key",
            model="gemini-test",
            client=SimpleNamespace(models=models),
        )

        data = provider.generate_structured(
            "Resolve the speaker",
            {"type": "object"},
        )

        self.assertEqual(data, {"speaker": "María", "confidence": 0.8})
        self.assertIsNotNone(models.last_request)
        request = models.last_request or {}
        self.assertEqual(request["model"], "gemini-test")
        self.assertEqual(request["contents"], "Resolve the speaker")
        config = request["config"]
        self.assertEqual(config["response_mime_type"], "application/json")
        self.assertEqual(config["response_json_schema"], {"type": "object"})

    def test_llm_resolver_uses_provider_without_vendor_dependencies(self) -> None:
        provider = FakeProvider({"speaker": "Pedro", "confidence": 0.72})
        resolver = LLMResolver(provider)

        resolution = resolver.resolve(self.make_context())

        self.assertIsNotNone(resolution)
        self.assertEqual(resolution.speaker, "Pedro")
        self.assertEqual(resolution.source, "llm")
        self.assertIn("No pienso hacerlo", provider.prompt)
        self.assertEqual(provider.schema["required"], ["speaker", "confidence"])

    def test_llm_resolver_returns_none_when_provider_fails(self) -> None:
        resolver = LLMResolver(FakeProvider(None))
        self.assertIsNone(resolver.resolve(self.make_context()))

    def test_missing_environment_key_disables_gemini(self) -> None:
        fake_dotenv = SimpleNamespace(load_dotenv=lambda: None)
        with patch.dict(os.environ, {}, clear=True), patch.dict(
            sys.modules,
            {"dotenv": fake_dotenv},
        ):
            resolver = build_gemini_resolver_from_environment()

        self.assertIsNone(resolver)

    def test_environment_key_builds_provider_backed_resolver(self) -> None:
        fake_dotenv = SimpleNamespace(load_dotenv=lambda: None)
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "environment-test-key"},
            clear=True,
        ), patch.dict(sys.modules, {"dotenv": fake_dotenv}), patch(
            "smart_audiobook.gemini_resolver.GeminiProvider"
        ) as provider_class:
            resolver = build_gemini_resolver_from_environment()

        self.assertIsInstance(resolver, LLMResolver)
        provider_class.assert_called_once_with(api_key="environment-test-key")

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
