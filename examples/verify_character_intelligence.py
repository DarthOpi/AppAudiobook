"""Run a reproducible V0.8 review smoke check, without APIs or TTS models."""

import json
import tempfile
from pathlib import Path

from smart_audiobook.application import AudiobookApplicationService
from smart_audiobook.book_processor import character_metrics
from smart_audiobook.llm_resolver import LLMResolver
from smart_audiobook.review_service import ReviewService
from smart_audiobook.review_store import ReviewProjectStore
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
from smart_audiobook.tts_providers import VoiceInfo


class FixtureLLM:
    model = "offline-smoke-fixture"

    def generate_structured(self, prompt, response_schema):
        if "Diálogo a resolver:\nVengo a ayudaros." in prompt:
            return {"speaker": "Elena", "confidence": .91, "is_new_character": True}
        return {"speaker": "Unknown", "confidence": .25, "is_new_character": False}


class FixtureVoices:
    def list_voices(self):
        return [VoiceInfo(f"fixture-{i}", f"Fixture {i}") for i in range(3)]


def main():
    engine = SpeakerIdentificationService(RuleBasedSpeakerResolver(), LLMResolver(FixtureLLM()))
    application = AudiobookApplicationService(lambda use_llm: engine)
    source = Path(__file__).with_name("character_intelligence.txt")
    with tempfile.TemporaryDirectory(prefix="smart-audiobook-v08-") as directory:
        review = ReviewService(ReviewProjectStore(Path(directory)), FixtureVoices(), application)
        project = review.start_analysis(source, source.name)
        assert ("Sir Gilead", "Gilead") in project.analysis.registry.duplicate_suggestions()
        assert ("Sunny", "Sunless") in project.analysis.registry.duplicate_suggestions()
        provisional = next(s for s in project.dialogues if s.new_character_candidate == "Elena")
        review.update_speakers(project.processing_id, {provisional.id: "Elena"})
        review.merge_characters(project.processing_id, "Sunless", "Sunny", 2)
        review.merge_characters(project.processing_id, "Gilead", "Sir Gilead", 1)
        review.edit_alias(project.processing_id, "Sunny", "Lost from Light", 2)
        loaded = review.load(project.processing_id)
        assert loaded.analysis.registry.find("Lost from Light", 1) is None
        assert loaded.analysis.registry.find("Lost from Light", 2).canonical_name == "Sunny"
        assert loaded.analysis.registry.require("Elena").pending is False
        corrected = next(s for s in loaded.dialogues if s.id == provisional.id)
        review.reanalyze(project.processing_id, {provisional.id}, speaker_service=engine)
        final = review.load(project.processing_id)
        assert next(s for s in final.dialogues if s.id == provisional.id) == corrected
        print(json.dumps({"characters": final.analysis.characters,
                          "metrics": character_metrics(final.analysis),
                          "aliases": [a.name for a in final.analysis.registry.require("Sunny").aliases],
                          "manual_override_preserved": True,
                          "future_alias_hidden": True}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
