"""Temporal character intelligence tests without network or real TTS."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from smart_audiobook.book_processor import AnalyzedChapter, BookAnalysis, analyze_book, character_metrics
from smart_audiobook.character_config import CharacterConfig
from smart_audiobook.characters import CharacterProfile, CharacterRegistry
from smart_audiobook.llm_resolver import LLMResolver
from smart_audiobook.models import Document, TextSegment, SpeakerContext, SpeakerResolution
from smart_audiobook.review_service import ReviewService
from smart_audiobook.review_store import ReviewProject, ReviewProjectStore
from smart_audiobook.segmenter import segment_text
from smart_audiobook.speaker_candidates import candidate_speakers
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
from smart_audiobook.tts_providers import VoiceInfo
from smart_audiobook.voice_assignment import assign_profile_voices
from unittest.mock import patch
from fastapi.testclient import TestClient
from smart_audiobook.web import create_app


class FakeResolver:
    def __init__(self, resolution=None):
        self.resolution, self.calls = resolution, 0

    def resolve(self, context):
        self.calls += 1
        return self.resolution


class FakeProvider:
    model = "fixture-model"

    def __init__(self, response):
        self.response, self.calls = response, 0

    def generate_structured(self, prompt, response_schema):
        self.calls += 1
        return self.response


class FakeTTS:
    def list_voices(self):
        return [VoiceInfo("a", "A"), VoiceInfo("b", "B"), VoiceInfo("c", "C")]


def dialogue(text="Hola.", speaker="Unknown", chapter=1, order=1, method=None, confidence=None):
    return TextSegment("dialogue", text, speaker, f"s{order}", chapter, order,
                       confidence, method)


class CharacterIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.registry = CharacterRegistry()
        self.registry.register("Sunny", 1)
        self.registry.register("Nephis", 1)

    def service(self, resolution=None, **kwargs):
        self.llm = FakeResolver(resolution)
        return SpeakerIdentificationService(RuleBasedSpeakerResolver(), self.llm, **kwargs)

    def test_profile_unknown_attributes_and_stable_id(self):
        profile = CharacterProfile("char_000001", "Sunny")
        self.assertIsNone(profile.gender)
        self.assertEqual(profile.importance, "unknown")
        loaded = CharacterRegistry.from_list(self.registry.to_list())
        self.assertEqual(loaded.require("Sunny").id, self.registry.require("Sunny").id)

    def test_canonical_name_normalizes_spelling(self):
        self.assertEqual(self.registry.register("  SUNNY "), "Sunny")

    def test_alias_lookup_uses_same_identity(self):
        self.registry.add_alias("Sunny", "Sunless", 10)
        self.assertIs(self.registry.find("Sunless", 10), self.registry.find("Sunny"))
        self.assertNotIn("Sunless", self.registry.characters)

    def test_alias_not_visible_in_past(self):
        self.registry.add_alias("Sunny", "Lost from Light", 300)
        self.assertIsNone(self.registry.find("Lost from Light", 20))
        self.assertEqual(self.registry.mentions("Lost from Light", 20), [])
        self.assertEqual(self.registry.mentions("Lost from Light", 300), ["Sunny"])

    def test_future_character_excluded_from_candidates(self):
        self.registry.register("Cassie", 300)
        ctx = SpeakerContext(dialogue(chapter=20),
            (TextSegment("narration", "Cassie caminaba.", "Narrator", chapter=20),), (), (), chapter=20)
        self.assertNotIn("Cassie", candidate_speakers(ctx, self.registry))

    def test_statistics_count_mentions_dialogue_and_chapters(self):
        self.registry.rebuild_statistics([
            dialogue("Sunny llamó a Nephis.", "Sunny", 1, 1),
            dialogue("Hola, Sunny.", "Nephis", 2, 2)])
        profile = self.registry.require("Sunny")
        self.assertEqual((profile.dialogue_count, profile.mention_count, profile.chapter_count), (1, 2, 2))
        self.assertEqual((profile.first_seen_chapter, profile.last_seen_chapter), (1, 2))

    def test_statistics_rebuild_is_idempotent(self):
        segments = [dialogue(speaker="Sunny")]
        self.registry.rebuild_statistics(segments)
        self.registry.rebuild_statistics(segments)
        self.assertEqual(self.registry.require("Sunny").dialogue_count, 1)

    def test_importance_heuristics(self):
        self.registry.rebuild_statistics([dialogue(speaker="Sunny", order=i+1) for i in range(35)])
        self.assertEqual(self.registry.require("Sunny").importance, "major")
        self.registry.rebuild_statistics([dialogue(speaker="Sunny", order=i+1) for i in range(5)])
        self.assertEqual(self.registry.require("Sunny").importance, "supporting")

    def test_activity_excludes_old_and_future(self):
        self.registry.observe(dialogue(speaker="Sunny", order=5))
        self.registry.observe(dialogue(speaker="Nephis", chapter=300, order=900))
        self.assertEqual(self.registry.active_characters(1, 6), ("Sunny",))
        self.assertEqual(self.registry.active_characters(200, 800), ())

    def test_candidates_bounded_and_alias_aware(self):
        self.registry.add_alias("Sunny", "Sunless", 1)
        for i in range(100):
            self.registry.register(f"Person{i}", 1)
        context = SpeakerContext(dialogue(),
            (TextSegment("narration", "Sunless estaba junto a Nephis.", "Narrator", chapter=1),), (), (), chapter=1)
        candidates = candidate_speakers(context, self.registry, 2)
        self.assertEqual(set(candidates), {"Sunny", "Nephis"})

    def test_explicit_dialogue_saves_llm_call(self):
        service = self.service()
        result = service.identify(segment_text("—Hola —dijo María."))
        self.assertEqual(result.segments[0].speaker, "María")
        self.assertEqual(self.llm.calls, 0)
        self.assertEqual(service.llm_calls_saved, 1)

    def test_known_name_label_avoids_llm(self):
        segments = [TextSegment("narration", "Sunny:", "Narrator", "s1", 1, 1), dialogue(order=2)]
        result = self.service().identify(segments, self.registry)
        self.assertEqual(result.segments[1].speaker, "Sunny")
        self.assertEqual(self.llm.calls, 0)

    def test_pending_profile_cannot_be_assigned_without_confirmation(self):
        self.registry.register("Elena", 1, pending=True)
        result = self.service(SpeakerResolution("Elena", .9, "llm")).identify([dialogue()], self.registry)
        self.assertEqual(result.segments[0].speaker, "Unknown")
        self.assertEqual(result.segments[0].new_character_candidate, "Elena")

    def test_alias_explicit_attribution_resolves_temporally(self):
        self.registry.add_alias("Sunny", "Sunless", 10)
        segments = [replace(s, chapter=10, order=i+1) for i, s in enumerate(segment_text("—Hola —dijo Sunless."))]
        result = self.service().identify(segments, self.registry)
        self.assertEqual(result.segments[0].speaker, "Sunny")

    def test_safe_conversation_alternation(self):
        segments = [dialogue(speaker="Sunny", order=1, method="manual", confidence=1),
                    dialogue(speaker="Nephis", order=2, method="manual", confidence=1),
                    dialogue(speaker="Sunny", order=3, method="manual", confidence=1),
                    dialogue("De acuerdo.", order=4)]
        result = self.service().identify(segments, self.registry)
        self.assertEqual(result.segments[-1].speaker, "Nephis")
        self.assertEqual(result.segments[-1].resolution_method, "conversation")
        self.assertEqual(self.llm.calls, 0)

    def test_three_person_conversation_remains_ambiguous(self):
        self.registry.register("Cassie", 1)
        segments = [dialogue(speaker=name, order=i+1, method="manual", confidence=1)
                    for i, name in enumerate(["Cassie", "Sunny", "Nephis", "Sunny"])]
        segments.append(dialogue("Tal vez.", order=5))
        result = self.service().identify(segments, self.registry)
        self.assertEqual(result.segments[-1].speaker, "Unknown")
        self.assertEqual(self.llm.calls, 1)

    def test_medium_confidence_is_reviewable(self):
        result = self.service(SpeakerResolution("Sunny", .72, "llm")).identify([dialogue()], self.registry)
        self.assertEqual(result.segments[0].speaker, "Sunny")
        self.assertTrue(result.segments[0].review_needed)

    def test_low_confidence_stays_unknown(self):
        result = self.service(SpeakerResolution("Sunny", .4, "llm")).identify([dialogue()], self.registry)
        self.assertEqual(result.segments[0].speaker, "Unknown")

    def test_new_character_remains_pending(self):
        result = self.service(SpeakerResolution("Elena", .9, "llm", True)).identify([dialogue()], self.registry)
        self.assertEqual(result.segments[0].speaker, "Unknown")
        self.assertEqual(result.segments[0].new_character_candidate, "Elena")
        self.assertTrue(self.registry.require("Elena").pending)
        self.assertNotIn("Elena", self.registry.characters)

    def test_llm_rejects_new_name_without_evidence(self):
        resolver = LLMResolver(FakeProvider({"speaker": "Invented", "confidence": .9, "is_new_character": True}))
        self.assertIsNone(resolver.resolve(SpeakerContext(dialogue(), (), (), (), chapter=1)))

    def test_llm_rejects_name_outside_candidates(self):
        resolver = LLMResolver(FakeProvider({"speaker": "Other", "confidence": .9}))
        self.assertIsNone(resolver.resolve(SpeakerContext(dialogue(), (), (), (), candidate_speakers=("Sunny",), chapter=1)))

    def test_llm_alias_maps_to_canonical_candidate(self):
        resolver = LLMResolver(FakeProvider({"speaker": "Sunless", "confidence": .9}))
        context = SpeakerContext(dialogue(), (), (), (), candidate_speakers=("Sunny",),
                                 chapter=10, candidate_aliases=(("Sunny", "Sunless", 10),))
        self.assertEqual(resolver.resolve(context).speaker, "Sunny")

    def test_duplicate_title_is_suggestion_only(self):
        self.registry.register("Sir Gilead", 1)
        self.registry.register("Gilead", 2)
        self.assertIn(("Sir Gilead", "Gilead"), self.registry.duplicate_suggestions())
        self.assertNotEqual(self.registry.find("Gilead").id, self.registry.find("Sir Gilead").id)

    def test_alias_evidence_suggests_unrelated_names(self):
        self.registry.register("Sunless", 1)
        self.registry.observe(TextSegment("narration", "Sunny, también conocido como Sunless, esperaba.", "Narrator", chapter=2))
        self.assertIn(("Sunny", "Sunless"), self.registry.duplicate_suggestions())

    def test_manual_alias_add_remove(self):
        self.registry.add_alias("Sunny", "Sunless", 5)
        self.registry.remove_alias("Sunny", "Sunless")
        self.assertIsNone(self.registry.find("Sunless"))

    def test_merge_preserves_target_manual_voice(self):
        self.registry.require("Sunny").voice_id = "target-voice"
        self.registry.require("Sunny").voice_manual = True
        self.registry.require("Nephis").voice_id = "source-voice"
        result = self.registry.merge("Nephis", "Sunny", 10)
        self.assertEqual(result.voice_id, "target-voice")
        self.assertIsNone(self.registry.find("Nephis", 1))
        self.assertEqual(self.registry.find("Nephis", 10).canonical_name, "Sunny")

    def test_manual_override_is_never_overwritten(self):
        service = self.service(SpeakerResolution("Nephis", .99, "llm"))
        segment = dialogue(speaker="Sunny", method="manual", confidence=1)
        result = service.identify([segment], self.registry)
        self.assertEqual(result.segments, (segment,))
        self.assertEqual(self.llm.calls, 0)

    def test_prompt_version_and_model_invalidate_cache(self):
        context = SpeakerContext(dialogue(), (), (), ())
        provider = FakeProvider(None)
        a = LLMResolver(provider, "v2").cache_key(context)
        b = LLMResolver(provider, "v3").cache_key(context)
        self.assertNotEqual(a, b)
        provider.model = "new-model"
        self.assertNotEqual(a, LLMResolver(provider).cache_key(context))

    def test_persistent_resolution_hit_saves_call_and_candidates_invalidate(self):
        provider = FakeProvider({"speaker": "Sunny", "confidence": .9})
        context = SpeakerContext(dialogue(), (), (), ("Sunny",), candidate_speakers=("Sunny",), chapter=1)
        first = SpeakerIdentificationService(RuleBasedSpeakerResolver(), LLMResolver(provider))
        first._resolve_with_llm(context)
        persisted = json.loads(json.dumps(first.resolution_cache))
        second = SpeakerIdentificationService(RuleBasedSpeakerResolver(), LLMResolver(provider), resolution_cache=persisted)
        second._resolve_with_llm(context)
        self.assertEqual(provider.calls, 1)
        second._resolve_with_llm(replace(context, candidate_speakers=("Sunny", "Nephis")))
        self.assertEqual(provider.calls, 2)

    def test_alias_knowledge_in_prompt_and_cache_is_temporal(self):
        self.registry.add_alias("Sunny", "Sunless", 10)
        service = SpeakerIdentificationService(RuleBasedSpeakerResolver())
        before = TextSegment("narration", "Sunny está aquí.", "Narrator", "s1", 9, 1)
        segments = [before, dialogue("Sunless, ¿vienes?", chapter=9, order=2)]
        past = service._build_context(segments, 1, self.registry)
        present = service._build_context([replace(s, chapter=10) for s in segments], 1, self.registry)
        self.assertEqual(past.candidate_aliases, ())
        self.assertIn(("Sunny", "Sunless", 10), present.candidate_aliases)
        resolver = LLMResolver(FakeProvider(None))
        self.assertNotEqual(resolver.cache_key(past), resolver.cache_key(present))

    def test_voice_assignment_keeps_manual_and_uses_importance(self):
        profile = self.registry.require("Sunny")
        profile.importance = "major"
        voices = FakeTTS().list_voices()
        first = assign_profile_voices(self.registry, voices)
        self.assertEqual(profile.voice_strategy, "dedicated")
        self.assertEqual(assign_profile_voices(self.registry, voices, {"Sunny": "c"})["Sunny"], "c")
        self.assertEqual(first, assign_profile_voices(self.registry, voices, first))

    def test_confidence_configuration_validated(self):
        with self.assertRaises(ValueError):
            CharacterConfig(accept_confidence=.4, review_confidence=.8)


class ProjectIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ReviewProjectStore(Path(self.temp.name))
        pid, _ = self.store.create_workspace()
        registry = CharacterRegistry()
        registry.register("Sunny", 1)
        registry.register("Sunless", 1)
        segments = (dialogue(speaker="Sunny", order=1, method="manual", confidence=1),
                    dialogue(order=2), dialogue(order=3))
        analysis = BookAnalysis(Document("Test", Path("test.txt"), "txt", "text"),
            (AnalyzedChapter(1, "One", segments),), registry.characters, registry)
        self.project = ReviewProject(pid, "test.txt", analysis, tuple(FakeTTS().list_voices()),
                                     {"Narrator": "a", "Sunny": "b", "Sunless": "c", "Unknown": "c"})
        self.store.save(self.project)
        self.service = ReviewService(self.store, FakeTTS())

    def tearDown(self):
        self.temp.cleanup()

    def test_selective_reanalysis_preserves_other_and_manual_segments(self):
        engine = SpeakerIdentificationService(RuleBasedSpeakerResolver(), FakeResolver(SpeakerResolution("Sunny", .9, "llm")))
        result = self.service.reanalyze(self.project.processing_id, {"s1", "s2"}, speaker_service=engine)
        self.assertEqual(result.segments[0], self.project.segments[0])
        self.assertEqual(result.segments[1].speaker, "Sunny")
        self.assertEqual(result.segments[2], self.project.segments[2])

    def test_registry_and_alias_survive_reload(self):
        self.service.edit_alias(self.project.processing_id, "Sunny", "Lost from Light", 10)
        loaded = self.store.load(self.project.processing_id)
        self.assertEqual(loaded.analysis.registry.find("Lost from Light", 10).canonical_name, "Sunny")

    def test_merge_updates_segments_aliases_voice_and_statistics(self):
        self.service.select_voice(self.project.processing_id, "Sunny", "b")
        result = self.service.merge_characters(self.project.processing_id, "Sunless", "Sunny", 10)
        profile = result.analysis.registry.require("Sunny")
        self.assertEqual(profile.voice_id, "b")
        self.assertEqual(profile.dialogue_count, 1)
        self.assertNotIn("Sunless", result.analysis.characters)
        self.assertIsNotNone(profile.aliases)

    def test_legacy_json_migrates_without_losing_manual_edits(self):
        path = self.store.directory(self.project.processing_id) / "analysis.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("schema_version", "character_profiles", "resolution_cache"):
            data.pop(key)
        path.write_text(json.dumps(data), encoding="utf-8")
        loaded = self.store.load(self.project.processing_id)
        self.assertEqual(loaded.segments[0].resolution_method, "manual")
        self.assertTrue(loaded.analysis.registry.require("Sunny").voice_manual)
        self.store.save(loaded)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2)

    def test_resolution_cache_persists_per_project(self):
        self.project.analysis.resolution_cache["key"] = {"speaker": "Sunny", "confidence": .9, "source": "llm"}
        self.store.save(self.project)
        self.assertIn("key", self.store.load(self.project.processing_id).analysis.resolution_cache)

    def test_manual_correction_clears_review_issue(self):
        result = self.service.update_speakers(self.project.processing_id, {"s2": "Sunny"})
        self.assertFalse(result.segments[1].review_needed)
        self.assertEqual(character_metrics(result.analysis)["speakers_resolved_manually"], 2)

    def test_web_alias_and_registry_panel(self):
        with TestClient(create_app(review_service=self.service, tts_provider=FakeTTS(), output_root=Path(self.temp.name))) as client:
            result = client.post(f"/review/{self.project.processing_id}/characters/aliases",
                data={"character": "Sunny", "alias": "Lost from Light", "known_from": "10"}, follow_redirects=True)
            self.assertEqual(result.status_code, 200)
            self.assertIn("Character Registry", result.text)
            self.assertIn("Lost from Light", result.text)

    def test_web_review_issue_filter_and_reanalysis(self):
        engine = SpeakerIdentificationService(RuleBasedSpeakerResolver(), FakeResolver(SpeakerResolution("Sunny", .9, "llm")))
        with TestClient(create_app(review_service=self.service, tts_provider=FakeTTS(), output_root=Path(self.temp.name))) as client:
            response = client.get(f"/review/{self.project.processing_id}?issue=unresolved")
            self.assertEqual(response.status_code, 200)
            self.assertIn("Review Issues", response.text)
            with patch("smart_audiobook.application._build_speaker_service", return_value=engine):
                result = client.post(f"/review/{self.project.processing_id}/reanalyze",
                    data={"segment_ids": "s2"}, follow_redirects=True)
            self.assertEqual(result.status_code, 200)
        loaded = self.store.load(self.project.processing_id)
        self.assertEqual(loaded.segments[1].speaker, "Sunny")
        self.assertEqual(loaded.segments[2].speaker, "Unknown")

    def test_enrichment_uses_only_requested_past_evidence(self):
        segments = tuple(dialogue(f"Evidencia {i}.", "Sunny", chapter=i, order=i) for i in range(1, 5))
        self.project.analysis = replace(self.project.analysis,
            chapters=(AnalyzedChapter(1, "Test", segments),))
        self.store.save(self.project)
        class Enricher:
            def enrich(self, name, evidence, chapter):
                assert len(evidence) == 3
                assert "Evidencia 4." not in evidence
                return {"description": "Cauteloso en estos fragmentos.", "personality_traits": ["cautious"]}
        result = self.service.enrich_character(self.project.processing_id, "Sunny", 3, Enricher())
        self.assertEqual(result.analysis.registry.require("Sunny").knowledge["description"], 3)

    def test_web_rejects_alias_collision(self):
        with TestClient(create_app(review_service=self.service, tts_provider=FakeTTS(), output_root=Path(self.temp.name))) as client:
            result = client.post(f"/review/{self.project.processing_id}/characters/aliases",
                data={"character": "Sunny", "alias": "Sunless", "known_from": "10"})
            self.assertEqual(result.status_code, 400)

    def test_reanalysis_does_not_pass_future_speaker_labels(self):
        segments = [dialogue(order=1), dialogue(speaker="Sunless", order=2, method="manual", confidence=1)]
        engine = SpeakerIdentificationService(RuleBasedSpeakerResolver())
        context = engine._build_context(segments, 0, self.project.analysis.registry)
        self.assertEqual(context.after[0].speaker, "Unknown")


if __name__ == "__main__":
    unittest.main()
