"""Novel regression tests: no network, no real neural model or reference voice."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import json
import wave

import pytest

from smart_audiobook.audio_cache import audio_cache_key
from smart_audiobook.book_processor import AnalyzedChapter, BookAnalysis, analysis_warnings, character_metrics
from smart_audiobook.characters import CharacterRegistry
from smart_audiobook.chatterbox_provider import ChatterboxTTSProvider
from smart_audiobook.document_loaders import _build_document, _pdf_page_texts
from smart_audiobook.gemini_provider import GeminiProvider
from smart_audiobook.llm_resolver import LLMResolver
from smart_audiobook.models import Document, SpeakerContext, SpeakerResolution, TextSegment
from smart_audiobook.pdf_reconstruction import PdfTextReconstructor
from smart_audiobook.review_service import ReviewService
from smart_audiobook.review_store import ReviewProject, ReviewProjectStore
from smart_audiobook.segmenter import segment_text
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import ProviderCapabilities, SynthesisSettings, VoiceInfo
from smart_audiobook.voice_assignment import assign_profile_voices
from smart_audiobook.voice_catalog import CharacterVoiceProfile, ProfiledTTSProvider, load_voice_profiles


def identify(text, llm=None):
    segments = [replace(s, id=f"s{i}", chapter=1, order=i) for i, s in enumerate(segment_text(text))]
    registry = CharacterRegistry()
    service = SpeakerIdentificationService(RuleBasedSpeakerResolver(), llm)
    return service.identify(segments, registry), registry, service


def book(result, registry):
    return BookAnalysis(Document("Test", Path("test.txt"), "txt", ""),
        (AnalyzedChapter(1, "Test", result.segments),), registry.characters, registry)


def test_pdf_visual_wrap_is_one_logical_paragraph():
    text = PdfTextReconstructor().reconstruct("Daniel miró la puerta de\nla habitación.\n\nElena salió.")
    assert text == "Daniel miró la puerta de la habitación.\n\nElena salió."


def test_pdf_preserves_speech_thought_scene_and_heading():
    text = PdfTextReconstructor().reconstruct("Capítulo 1: Inicio\nDaniel dijo:\n—No voy a\nhacerlo.\n'Esto es\nun error.'\n***\nElena regresó.")
    result = segment_text(text)
    assert [(s.type, s.text) for s in result if s.type != "narration"] == [
        ("dialogue", "No voy a hacerlo."), ("internal_thought", "Esto es un error.")]
    assert result[-1].scene_break_before
    assert text.startswith("Capítulo 1: Inicio\n\n")


def test_pdf_preserves_blank_lines_and_indentation():
    assert PdfTextReconstructor().reconstruct("Primera idea\n\nSegunda idea\n   Otro párrafo") == "Primera idea\n\nSegunda idea\n\nOtro párrafo"


def test_pdf_hyphenation_and_boundary_headers():
    pages = _pdf_page_texts(["Header\nLa habita-\nción está abierta.\n1", "Header\nOtra página.\n2"])
    assert pages == ["La habitación está abierta.", "Otra página."]


def test_pdf_loader_uses_paragraphs_not_lines(tmp_path):
    pages = ["Elena encontró una\ncarta en la mesa.\n\nDaniel dijo:\n—Hola."]
    document = _build_document(tmp_path / "book.pdf", "pdf", "\n\n".join(_pdf_page_texts(pages)), source_pages=pages)
    assert len(document.blocks) == 3
    assert document.blocks[0].source_reference == "pdf:page-1"


def test_spaced_pdf_heading_and_contents_entries():
    from smart_audiobook.chapter_detection import is_chapter_heading
    heading = PdfTextReconstructor().reconstruct("C a p í t u l o   6 0 1 :   L a   p u e r t a")
    assert heading == "Capítulo 601: La puerta"
    assert is_chapter_heading(heading)
    assert not is_chapter_heading("Capítulo 601: La puerta........................7")


def test_usage_estimate_includes_unattributed_thoughts():
    from smart_audiobook.book_manifest import book_statistics
    from smart_audiobook.models import Chapter
    text = "'No sé.'\n—Hola."
    document = Document("Test", Path("test.txt"), "txt", text, (Chapter(1, "Test", text),))
    stats = book_statistics(document, detailed=True)
    assert stats["estimated_internal_thoughts"] == 1
    assert stats["estimated_llm_calls_upper_bound"] == 2


@pytest.mark.parametrize("text", ["'Debo salir.'", "‘Debo salir.’"])
def test_single_quotes_are_thoughts_not_speech(text):
    segment, = segment_text(text)
    assert segment.type == "internal_thought"
    assert segment.speaker == "Unknown"


@pytest.mark.parametrize("text", ['"Hola."', "“Hola.”", "«Hola.»", "—Hola."])
def test_supported_speech_delimiters(text):
    segment, = segment_text(text)
    assert segment.type == "dialogue"
    assert segment.text == "Hola."


def test_word_apostrophes_do_not_create_thoughts():
    assert all(s.type == "narration" for s in segment_text("D'Artagnan isn't here."))


@pytest.mark.parametrize("before", ["Sunny dijo:", "Sunny abrió la boca y dijo:", "Sunny abrió la boca, atónito, y dijo:", "Daniel se volvió y preguntó:", "Elena ordenó:", "Kai comentó:"])
def test_attribution_before_avoids_llm(before):
    llm = Mock()
    result, _, _ = identify(before + "\n—Hola.", llm)
    assert result.segments[-1].speaker == before.split()[0]
    llm.resolve.assert_not_called()


@pytest.mark.parametrize("verb", ["dijo", "respondió", "preguntó", "ordenó", "comentó"])
def test_attribution_after(verb):
    result, registry, _ = identify(f"—Hola —{verb} María.")
    assert result.segments[0].speaker == "María"
    assert registry.require("María").dialogue_count == 1


def test_indirect_subject_is_not_a_definitive_character():
    result, registry, _ = identify("La mujer respondió:\n—No.")
    assert result.segments[-1].speaker == "Unknown"
    assert registry.characters == ("Narrator",)


def test_thought_uses_thinking_not_speech_attribution():
    result, _, _ = identify("Elena pensó:\n'No puedo ir.'")
    assert result.segments[-1].speaker == "Elena"
    result, _, _ = identify("Elena dijo:\n'No puedo ir.'")
    assert result.segments[-1].speaker == "Unknown"


def test_narrator_response_rejected_for_dialogue_and_thought():
    llm = SimpleNamespace(resolve=lambda _: SpeakerResolution("Narrator", .99, "llm"))
    result, _, _ = identify("—Hola.\n'Qué extraño.'", llm)
    assert all(s.speaker == "Unknown" and s.review_needed for s in result.segments)


def test_gemini_receives_before_after_and_internal_thought_type():
    provider = Mock(spec=["generate_structured"])
    provider.generate_structured.return_value = {"speaker": "Elena", "confidence": .9, "is_new_character": True}
    resolver = LLMResolver(provider)
    result, registry, service = identify("La mujer miró a Daniel.\n—No.\n—Mi nombre es Elena.", resolver)
    prompt = provider.generate_structured.call_args.kwargs["prompt"]
    assert "La mujer miró a Daniel." in prompt
    assert "Mi nombre es Elena." in prompt
    assert result.segments[1].context_before == "La mujer miró a Daniel."
    assert "Mi nombre es Elena." in result.segments[1].context_after
    assert registry.require("Elena").pending is False  # explicit self-identification validates evidence


def test_dialogue_without_characters_has_warning():
    result, registry, _ = identify("Narración.\n—Sin atribución.")
    analysis = book(result, registry)
    assert analysis_warnings(analysis) == ("Dialogue was detected but no character speakers were resolved. Check speaker detection configuration.",)
    assert character_metrics(analysis)["unresolved_dialogues"] == 1


def test_realistic_original_fixture_multiple_speakers_and_unknown():
    result, registry, _ = identify((Path(__file__).parent / "fixtures/novel_regression.txt").read_text(encoding="utf-8"))
    assert set(registry.characters) == {"Narrator", "Daniel", "Elena"}
    assert sum(s.type == "internal_thought" for s in result.segments) == 2
    assert any(s.speaker == "Unknown" for s in result.segments)
    assert not any(s.speaker == "Narrator" and s.type != "narration" for s in result.segments)


class FakeEngine:
    provider_id, model_id = "fake", "fake-v1"
    capabilities = ProviderCapabilities(supports_speed=True, supports_voice_cloning=True)

    def list_voices(self):
        return [VoiceInfo("a", "Voice A", "es", provider="fake"), VoiceInfo("b", "Voice B", "en", provider="fake")]

    def synthesize(self, text, output_path, voice_id, settings=None):
        self.last = (voice_id, settings)
        return output_path


def test_catalog_preserves_all_real_voices_and_profiles():
    provider = ProfiledTTSProvider(FakeEngine(), (
        CharacterVoiceProfile("profile:neutral", "Neutral pool", "fake", "a", "es", strategy="generic_pool"),))
    assert [v.id for v in provider.list_voices()] == ["a", "b", "profile:neutral"]


def test_profile_routes_base_voice_without_fake_identity(tmp_path):
    engine = FakeEngine()
    provider = ProfiledTTSProvider(engine, (CharacterVoiceProfile("profile:calm", "Calm", "fake", "a", settings=SynthesisSettings(speed=.9)),))
    provider.synthesize("Hi", tmp_path / "test.wav", "profile:calm")
    assert engine.last == ("a", SynthesisSettings(speed=.9))


def test_assignment_stable_and_manual_wins():
    registry = CharacterRegistry()
    registry.register("Daniel", 1)
    provider = ProfiledTTSProvider(FakeEngine(), (CharacterVoiceProfile("profile:pool", "Pool", "fake", "a", strategy="generic_pool"),))
    assignments = assign_profile_voices(registry, provider.list_voices())
    assert assignments == assign_profile_voices(registry, provider.list_voices(), assignments)
    assert assign_profile_voices(registry, provider.list_voices(), {"Daniel": "b"})["Daniel"] == "b"


def synthetic_reference(path):
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        output.writeframes(b"\x00\x00" * 100)


def test_reference_requires_consent(tmp_path):
    reference = tmp_path / "synthetic.wav"
    synthetic_reference(reference)
    with pytest.raises(SpeechGenerationError, match="consent"):
        ProfiledTTSProvider(FakeEngine(), (CharacterVoiceProfile("p", "Reference", "fake", "a", reference_audio=str(reference)),))


def test_reference_content_changes_audio_cache(tmp_path):
    reference = tmp_path / "synthetic.wav"
    synthetic_reference(reference)
    p = CharacterVoiceProfile("p", "Reference", "fake", "a", reference_audio=str(reference), metadata={"consent_confirmed": True})
    provider = ProfiledTTSProvider(FakeEngine(), (p,))
    key = audio_cache_key("Hello", "p", provider)
    reference.write_bytes(reference.read_bytes() + b"\x00\x00")
    assert key != audio_cache_key("Hello", "p", provider)


def test_local_profile_json_relative_reference(tmp_path):
    reference = tmp_path / "synthetic.wav"
    synthetic_reference(reference)
    config = tmp_path / "profiles.json"
    config.write_text(json.dumps([{"id": "p", "name": "P", "provider": "fake", "base_voice_id": "a", "reference_audio": "synthetic.wav", "metadata": {"consent_confirmed": True}}]), encoding="utf-8")
    assert load_voice_profiles(config)[0].reference_audio == str(reference.resolve())


def test_chatterbox_mock_reference_never_loads_model_for_catalog(tmp_path):
    model = SimpleNamespace(conds="base", sr=24000, generate=Mock(return_value="fake PCM"))
    loader = Mock(return_value=model)
    provider = ChatterboxTTSProvider(tmp_path, loader=loader, writer=lambda path, audio, sr: synthetic_reference(path))
    assert provider.list_voices()[0].language == "es"
    loader.assert_not_called()
    reference = tmp_path / "synthetic.wav"
    synthetic_reference(reference)
    provider.synthesize_reference("Hola", tmp_path / "out.wav", "chatterbox:default", reference)
    assert model.generate.call_args.kwargs["audio_prompt_path"] == str(reference)
    assert model.generate.call_args.kwargs["language_id"] == "es"


def test_gemini_failed_request_is_visible_and_not_cached(caplog):
    client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(side_effect=RuntimeError("secret-key must not leak"))))
    provider = GeminiProvider("test", client=client)
    _, _, service = identify("—No sé.", LLMResolver(provider))
    assert provider.calls == provider.failures == 1
    assert service.diagnostics["gemini_calls"] == service.diagnostics["llm_errors"] == 1
    assert not service.resolution_cache
    assert "secret-key" not in caplog.text


def test_context_and_thought_roundtrip_old_schema_defaults(tmp_path):
    store = ReviewProjectStore(tmp_path)
    identity, _ = store.create_workspace()
    result, registry, _ = identify("Elena pensó:\n'No.'")
    project = ReviewProject(identity, "test.txt", book(result, registry), (), {})
    store.save(project)
    loaded = store.load(identity)
    assert loaded.segments[-1].type == "internal_thought"
    assert loaded.segments[-1].context_before == "Elena pensó:"
    service = ReviewService(store, FakeEngine())
    with pytest.raises(ValueError, match="Narrator"):
        service.update_speakers(identity, {loaded.segments[-1].id: "Narrator"})


def test_llm_prompt_identifies_thinker_not_spoken_dialogue():
    provider = Mock(spec=["generate_structured"])
    provider.generate_structured.return_value = {"speaker": "Sunny", "confidence": .9}
    result, _, _ = identify("—Hola —dijo Sunny.\n'Tengo dudas.'", LLMResolver(provider))
    assert result.segments[-1].speaker == "Sunny"
    assert "type=internal_thought" in provider.generate_structured.call_args.kwargs["prompt"]


def test_profiles_reject_unsupported_reference_engine(tmp_path):
    engine = FakeEngine()
    engine.capabilities = ProviderCapabilities(supports_speed=True)
    reference = tmp_path / "synthetic.wav"
    synthetic_reference(reference)
    with pytest.raises(SpeechGenerationError, match="compatible"):
        ProfiledTTSProvider(engine, (CharacterVoiceProfile("p", "Own", "fake", "a", reference_audio=str(reference), metadata={"consent_confirmed": True}),))


def test_profile_ids_must_not_hide_real_voices():
    with pytest.raises(SpeechGenerationError, match="duplicados"):
        ProfiledTTSProvider(FakeEngine(), (CharacterVoiceProfile("a", "Conflict", "fake", "a"),))


def test_consent_string_false_is_not_true(tmp_path):
    reference = tmp_path / "synthetic.wav"
    synthetic_reference(reference)
    with pytest.raises(SpeechGenerationError, match="consent"):
        ProfiledTTSProvider(FakeEngine(), (CharacterVoiceProfile("p", "Own", "fake", "a", reference_audio=str(reference), metadata={"consent_confirmed": "false"}),))


def test_review_web_shows_diagnostics_thoughts_profiles_and_preview(tmp_path):
    from fastapi.testclient import TestClient
    from smart_audiobook.web import create_app
    store = ReviewProjectStore(tmp_path)
    identity, _ = store.create_workspace()
    result, registry, _ = identify("Elena pensó:\n'No.'\n—Sin atribución.")
    engine = FakeEngine()
    store.save(ReviewProject(identity, "test.txt", book(result, registry), tuple(engine.list_voices()), {"Narrator": "a", "Elena": "b", "Unknown": "a"}))
    provider = ProfiledTTSProvider(engine, (CharacterVoiceProfile("profile:pool", "Neutral pool", "fake", "a", "es", strategy="generic_pool"),))
    service = ReviewService(store, provider)
    app = create_app(review_service=service, tts_provider=provider)
    with TestClient(app) as client:
        response = client.get(f"/review/{identity}")
    assert response.status_code == 200
    for phrase in ("Narration: 1", "Dialogue: 1", "Internal thoughts: 1", "Characters: 1", "profile:pool", "Neutral pool", "Preview", "Buscar voz", "internal_thought"):
        assert phrase in response.text
    assert service.load(identity).voice_assignments["Elena"] == "b"


def test_new_character_ambiguous_mention_stays_pending():
    provider = SimpleNamespace(generate_structured=lambda **_: {"speaker": "Elena", "confidence": .91, "is_new_character": True})
    result, registry, _ = identify("La mujer miró a Elena.\n—Hola.", LLMResolver(provider))
    assert result.segments[-1].speaker == "Unknown"
    assert result.segments[-1].new_character_candidate == "Elena"
    assert registry.require("Elena").pending
