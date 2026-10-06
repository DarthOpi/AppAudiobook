"""V0.7 tests use tiny WAV fakes and never load/download neural models."""

import json
import os
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from smart_audiobook.audio_cache import AudioCache, audio_cache_key
from smart_audiobook.audio import combine_wav_files
from smart_audiobook.book_processor import (
    AnalyzedChapter,
    BookAnalysis,
    generate_book,
)
from smart_audiobook.models import Document, NARRATOR, TextSegment
from smart_audiobook.piper_provider import PiperTTSProvider
from smart_audiobook.text_chunking import chunk_text
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_config import TTSConfig, build_tts_provider
from smart_audiobook.tts_providers import (
    ProviderCapabilities,
    SynthesisSettings,
    TTSProvider,
    VoiceInfo,
    provider_capabilities,
)
from smart_audiobook.voice_assignment import assign_voices


class FakeWavProvider:
    provider_id = "fake-local"
    model_id = "fixture-v1"
    capabilities = ProviderCapabilities(supports_speed=True)

    def __init__(self, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def list_voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo("narrator", "Narrator", "es_ES", provider=self.provider_id),
            VoiceInfo("character-a", "Character A", "es_ES", provider=self.provider_id),
            VoiceInfo("character-b", "Character B", "es_ES", provider=self.provider_id),
        ]

    def synthesize(
        self,
        text: str,
        output_path: Path,
        voice_id: str,
        settings: SynthesisSettings | None = None,
    ) -> Path:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise SpeechGenerationError("fixture failure")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16_000)
            audio.writeframes(b"\x00\x00" * max(80, len(text)))
        return output_path


class AlternateFakeProvider(FakeWavProvider):
    provider_id = "fake-alternate"


class TTSV07Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.provider = FakeWavProvider()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_local_provider_implements_expected_protocol(self) -> None:
        self.assertIsInstance(self.provider, TTSProvider)

    def test_voice_catalog_has_formal_metadata(self) -> None:
        voice = self.provider.list_voices()[0]
        self.assertEqual(voice.display_name, "Narrator")
        self.assertEqual(voice.provider, "fake-local")
        self.assertEqual(voice.strategy, "dedicated")

    def test_piper_lists_single_and_multi_speaker_models(self) -> None:
        voices = self.root / "voices"
        voices.mkdir()
        for name, speakers in (("single", {}), ("cast", {"M": 0, "F": 1})):
            (voices / f"{name}.onnx").write_bytes(b"model")
            (voices / f"{name}.onnx.json").write_text(
                json.dumps(
                    {"language": {"code": "es_ES"}, "speaker_id_map": speakers}
                ),
                encoding="utf-8",
            )
        provider = PiperTTSProvider(voices, device="cpu")
        catalog = provider.list_voices()
        self.assertEqual(len(catalog), 3)
        self.assertTrue(all(voice.provider == "piper" for voice in catalog))

    def test_preview_synthesis_can_use_persistent_cache(self) -> None:
        cache = AudioCache(self.root / "cache")
        path, hit = cache.synthesize(self.provider, "Texto de preview", "narrator")
        self.assertTrue(path.is_file())
        self.assertFalse(hit)

    def test_voice_assignment_is_consistent(self) -> None:
        segments = self._analysis().chapters[0].segments
        voices = [voice.id for voice in self.provider.list_voices()]
        self.assertEqual(assign_voices(segments, voices), assign_voices(segments, voices))

    def test_same_inputs_produce_same_cache_key(self) -> None:
        first = audio_cache_key("Hola", "narrator", self.provider)
        second = audio_cache_key("Hola", "narrator", self.provider)
        self.assertEqual(first, second)

    def test_voice_change_invalidates_cache_key(self) -> None:
        first = audio_cache_key("Hola", "narrator", self.provider)
        second = audio_cache_key("Hola", "character-a", self.provider)
        self.assertNotEqual(first, second)

    def test_provider_or_settings_change_invalidates_cache_key(self) -> None:
        base = audio_cache_key("Hola", "narrator", self.provider)
        other_provider = audio_cache_key("Hola", "narrator", AlternateFakeProvider())
        faster = audio_cache_key(
            "Hola", "narrator", self.provider, SynthesisSettings(speed=1.2)
        )
        self.assertNotEqual(base, other_provider)
        self.assertNotEqual(base, faster)

    def test_cache_hit_avoids_synthesis(self) -> None:
        cache = AudioCache(self.root / "cache")
        cache.synthesize(self.provider, "Hola", "narrator")
        _path, hit = cache.synthesize(self.provider, "Hola", "narrator")
        self.assertTrue(hit)
        self.assertEqual(self.provider.calls, 1)

    def test_cache_miss_generates_audio(self) -> None:
        cache = AudioCache(self.root / "cache")
        _path, hit = cache.synthesize(self.provider, "Uno", "narrator")
        cache.synthesize(self.provider, "Dos", "narrator")
        self.assertFalse(hit)
        self.assertEqual(cache.metrics.misses, 2)

    def test_sentence_chunking_respects_limit_and_words(self) -> None:
        chunks = chunk_text(
            "Primera frase breve. Segunda frase algo más larga. Tercera frase.",
            30,
        )
        self.assertTrue(all(len(value) <= 30 for value in chunks))
        self.assertEqual(" ".join(chunks).split(), "Primera frase breve. Segunda frase algo más larga. Tercera frase.".split())

    def test_different_sample_rates_are_normalized(self) -> None:
        first = self.root / "16k.wav"
        second = self.root / "22k.wav"
        for path, rate in ((first, 16_000), (second, 22_050)):
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(rate)
                audio.writeframes(b"\x00\x00" * (rate // 10))
        output = combine_wav_files((first, second), self.root / "mixed.wav")
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getframerate(), 16_000)
            self.assertGreater(audio.getnframes(), 3_000)

    def test_provider_capabilities_are_exposed(self) -> None:
        capabilities = provider_capabilities(self.provider)
        self.assertTrue(capabilities.supports_speed)
        self.assertFalse(capabilities.supports_voice_cloning)

    def test_cpu_fallback_when_cuda_is_unavailable(self) -> None:
        fake_runtime = SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"])
        with patch.dict("sys.modules", {"onnxruntime": fake_runtime}):
            provider = PiperTTSProvider(self.root, device="auto")
        self.assertFalse(provider.use_cuda)

    def test_explicit_cuda_without_runtime_is_controlled(self) -> None:
        fake_runtime = SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"])
        with patch.dict("sys.modules", {"onnxruntime": fake_runtime}):
            with self.assertRaisesRegex(SpeechGenerationError, "CUDA"):
                PiperTTSProvider(self.root, device="cuda")

    def test_invalid_configuration_is_controlled(self) -> None:
        with patch.dict(os.environ, {"TTS_PROVIDER": "invented"}, clear=False):
            with self.assertRaisesRegex(SpeechGenerationError, "TTS_PROVIDER"):
                TTSConfig.from_environment()

    def test_invalid_provider_factory_is_controlled(self) -> None:
        config = TTSConfig(provider="invented")
        with self.assertRaisesRegex(SpeechGenerationError, "no soportado"):
            build_tts_provider(config)

    def test_completed_chapters_are_not_regenerated(self) -> None:
        analysis = self._analysis()
        assignments = self._assignments()
        first = generate_book(
            analysis,
            self.root / "output",
            voices_by_speaker=assignments,
            tts_provider=self.provider,
            cache_directory=self.root / "cache",
        )
        initial_calls = self.provider.calls
        second = generate_book(
            analysis,
            self.root / "output",
            voices_by_speaker=assignments,
            tts_provider=self.provider,
            cache_directory=self.root / "cache",
        )
        self.assertEqual(self.provider.calls, initial_calls)
        self.assertEqual(first.chapter_files, second.chapter_files)

    def test_resume_after_partial_failure_uses_cached_segments(self) -> None:
        failing = FakeWavProvider(fail_on_call=2)
        analysis = self._analysis()
        state = self.root / "state.json"
        with self.assertRaisesRegex(SpeechGenerationError, "fixture failure"):
            generate_book(
                analysis,
                self.root / "partial",
                voices_by_speaker=self._assignments(),
                tts_provider=failing,
                cache_directory=self.root / "cache-partial",
                state_path=state,
            )
        self.assertEqual(json.loads(state.read_text(encoding="utf-8"))["status"], "failed")
        resumed = FakeWavProvider()
        output = generate_book(
            analysis,
            self.root / "partial",
            voices_by_speaker=self._assignments(),
            tts_provider=resumed,
            cache_directory=self.root / "cache-partial",
            state_path=state,
        )
        self.assertTrue(output.full_audiobook.is_file())
        self.assertGreaterEqual(json.loads(state.read_text(encoding="utf-8"))["cache_hits"], 1)

    def test_segment_error_is_recorded_with_failed_chapter(self) -> None:
        state = self.root / "error-state.json"
        with self.assertRaises(SpeechGenerationError):
            generate_book(
                self._analysis(),
                self.root / "error-output",
                voices_by_speaker=self._assignments(),
                tts_provider=FakeWavProvider(fail_on_call=1),
                state_path=state,
            )
        payload = json.loads(state.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["chapters"]["1"]["status"], "failed")
        self.assertIn("fixture failure", payload["error"])

    def _analysis(self) -> BookAnalysis:
        chapter_one = AnalyzedChapter(
            1,
            "Capítulo 1",
            (
                TextSegment("narration", "La noche cayó.", NARRATOR, "s1", 1, 1),
                TextSegment("dialogue", "Hola, viajero.", "A", "s2", 1, 2),
            ),
        )
        chapter_two = AnalyzedChapter(
            2,
            "Capítulo 2",
            (TextSegment("dialogue", "Te esperaba.", "B", "s3", 2, 3),),
        )
        document = Document(
            title="Fixture book",
            source_path=self.root / "fixture.txt",
            format="txt",
            full_text="La noche cayó. Hola, viajero. Te esperaba.",
        )
        return BookAnalysis(document, (chapter_one, chapter_two), (NARRATOR, "A", "B"))

    @staticmethod
    def _assignments() -> dict[str, str]:
        return {NARRATOR: "narrator", "A": "character-a", "B": "character-b"}


@unittest.skipUnless(os.getenv("RUN_PIPER_INTEGRATION") == "1", "opt-in Piper test")
class PiperIntegrationTest(unittest.TestCase):
    def test_installed_piper_synthesizes_real_wav(self) -> None:
        provider = PiperTTSProvider(Path(os.getenv("PIPER_VOICES_DIR", "voices")))
        voices = provider.list_voices()
        self.assertTrue(voices)
        with tempfile.TemporaryDirectory() as directory:
            path = provider.synthesize(
                "Prueba real de Smart Audiobook.",
                Path(directory) / "integration.wav",
                voices[0].id,
            )
            with wave.open(str(path), "rb") as audio:
                self.assertGreater(audio.getnframes(), 0)


if __name__ == "__main__":
    unittest.main()
