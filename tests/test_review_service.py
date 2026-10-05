"""Tests for persisted character and voice review without real providers."""

import tempfile
import unittest
from pathlib import Path

from smart_audiobook.application import ProcessingResult
from smart_audiobook.book_processor import AnalyzedChapter, BookAnalysis, BookOutput
from smart_audiobook.models import Document, NARRATOR, UNKNOWN_SPEAKER, TextSegment
from smart_audiobook.review_service import ReviewService
from smart_audiobook.review_store import ReviewProjectStore, ReviewStateError
from smart_audiobook.tts import SpeechGenerationError
from smart_audiobook.tts_providers import VoiceInfo


class FakeReviewApplication:
    def __init__(self) -> None:
        self.generated_analysis: BookAnalysis | None = None
        self.generated_voices: dict[str, str] | None = None

    def analyze(self, source_path: Path, use_llm: bool = True) -> BookAnalysis:
        segments = (
            TextSegment(
                "narration", "Capítulo 1", NARRATOR, "seg_000001", 1, 1, 1.0, "system"
            ),
            TextSegment(
                "dialogue", "No iré.", UNKNOWN_SPEAKER, "seg_000002", 1, 2, None, "unknown"
            ),
            TextSegment(
                "dialogue", "Hola.", "Maria", "seg_000003", 1, 3, 0.99, "rule"
            ),
            TextSegment(
                "dialogue", "Espera.", "Nephies", "seg_000004", 1, 4, 0.7, "llm"
            ),
            TextSegment(
                "dialogue", "Aquí estoy.", "Nephis", "seg_000005", 1, 5, 0.9, "llm"
            ),
        )
        document = Document(
            title="Review book",
            source_path=source_path,
            format="txt",
            full_text="\n".join(segment.text for segment in segments),
        )
        return BookAnalysis(
            document,
            (AnalyzedChapter(1, "Capítulo 1", segments),),
            (NARRATOR, "Maria", "Nephies", "Nephis"),
        )

    def generate(
        self,
        analysis: BookAnalysis,
        output_root: Path,
        voices_by_speaker: dict[str, str] | None = None,
        tts_provider: object | None = None,
    ) -> ProcessingResult:
        self.generated_analysis = analysis
        self.generated_voices = dict(voices_by_speaker or {})
        directory = output_root / "review_book"
        directory.mkdir(parents=True, exist_ok=True)
        chapter = directory / "01_capitulo_1.wav"
        full = directory / "full_audiobook.wav"
        metadata = directory / "metadata.json"
        chapter.write_bytes(b"RIFF-chapter")
        full.write_bytes(b"RIFF-full")
        metadata.write_text("{}", encoding="utf-8")
        output = BookOutput(
            directory,
            (chapter,),
            full,
            metadata,
            dict(voices_by_speaker or {}),
        )
        return ProcessingResult(analysis, output)


class FakeTTSProvider:
    def __init__(self) -> None:
        self.synthesize_calls = 0

    def list_voices(self) -> list[VoiceInfo]:
        return [
            VoiceInfo("voice-a", "Ana", "es-ES", "female"),
            VoiceInfo("voice-b", "Bruno", "es-ES", "male"),
            VoiceInfo("voice-c", "Clara"),
            VoiceInfo("voice-d", "Diego"),
            VoiceInfo("voice-e", "Elena"),
        ]

    def synthesize(self, text: str, output_path: Path, voice_id: str) -> Path:
        self.synthesize_calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"RIFF-preview")
        return output_path


class UnavailableTTSProvider(FakeTTSProvider):
    def list_voices(self) -> list[VoiceInfo]:
        raise SpeechGenerationError("No voice engine")


class ReviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.store = ReviewProjectStore(self.root / "work")
        self.provider = FakeTTSProvider()
        self.application = FakeReviewApplication()
        self.service = ReviewService(self.store, self.provider, self.application)
        source = self.root / "source.txt"
        source.write_text("Capítulo 1\n—No iré.", encoding="utf-8")
        self.project = self.service.start_analysis(source, "source.txt")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_analysis_creates_processing_id_and_json(self) -> None:
        self.assertRegex(self.project.processing_id, r"^[0-9a-f]{32}$")
        directory = self.store.directory(self.project.processing_id)
        self.assertTrue((directory / "analysis.json").is_file())
        self.assertTrue((directory / "source" / "source.txt").is_file())

    def test_existing_analysis_can_be_loaded(self) -> None:
        loaded = self.service.load(self.project.processing_id)
        self.assertEqual(loaded.analysis.document.title, "Review book")
        self.assertEqual(loaded.dialogues[0].id, "seg_000002")

    def test_analysis_survives_an_unavailable_voice_catalog(self) -> None:
        service = ReviewService(
            ReviewProjectStore(self.root / "work-no-voices"),
            UnavailableTTSProvider(),
            self.application,
        )
        source = self.root / "without-voices.txt"
        source.write_text("Una historia", encoding="utf-8")
        project = service.start_analysis(source, source.name)
        self.assertEqual(project.voices, ())
        with self.assertRaisesRegex(SpeechGenerationError, "voces disponibles"):
            service.generate(project.processing_id)

    def test_speaker_can_be_changed_manually(self) -> None:
        updated = self.service.update_speakers(
            self.project.processing_id, {"seg_000002": "Maria"}
        )
        segment = next(item for item in updated.segments if item.id == "seg_000002")
        self.assertEqual(segment.speaker, "Maria")
        self.assertEqual(segment.resolution_method, "manual")
        self.assertEqual(segment.confidence, 1.0)

    def test_new_character_is_created_and_receives_voice(self) -> None:
        updated = self.service.update_speakers(
            self.project.processing_id, {"seg_000002": "Sunny"}
        )
        self.assertIn("Sunny", updated.characters)
        self.assertIn("Sunny", updated.voice_assignments)

    def test_rename_updates_every_associated_segment(self) -> None:
        updated = self.service.rename_character(
            self.project.processing_id, "Maria", "María"
        )
        speakers = [item.speaker for item in updated.dialogues]
        self.assertIn("María", speakers)
        self.assertNotIn("Maria", speakers)

    def test_rename_to_existing_character_merges_both(self) -> None:
        updated = self.service.rename_character(
            self.project.processing_id, "Nephies", "Nephis"
        )
        self.assertNotIn("Nephies", updated.characters)
        self.assertEqual(
            sum(item.speaker == "Nephis" for item in updated.dialogues), 2
        )

    def test_merge_moves_all_segments_to_target(self) -> None:
        updated = self.service.merge_characters(
            self.project.processing_id, "Nephies", "Maria"
        )
        self.assertNotIn("Nephies", updated.characters)
        self.assertEqual(
            next(item for item in updated.dialogues if item.id == "seg_000004").speaker,
            "Maria",
        )

    def test_new_character_uses_existing_normalized_name(self) -> None:
        updated = self.service.update_speakers(
            self.project.processing_id, {"seg_000002": "  MARÍA  "}
        )
        self.assertEqual(
            next(item for item in updated.dialogues if item.id == "seg_000002").speaker,
            "Maria",
        )
        self.assertEqual(sum(name.casefold() == "maria" for name in updated.characters), 1)

    def test_voice_can_be_selected(self) -> None:
        updated = self.service.select_voice(
            self.project.processing_id, "Maria", "voice-e"
        )
        self.assertEqual(updated.voice_assignments["Maria"], "voice-e")

    def test_preview_is_generated(self) -> None:
        path = self.service.preview_voice(self.project.processing_id, "voice-a")
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), b"RIFF-preview")

    def test_preview_is_cached(self) -> None:
        first = self.service.preview_voice(self.project.processing_id, "voice-a")
        second = self.service.preview_voice(self.project.processing_id, "voice-a")
        self.assertEqual(first, second)
        self.assertEqual(self.provider.synthesize_calls, 1)

    def test_generation_uses_corrected_speakers_and_selected_voices(self) -> None:
        self.service.update_speakers(
            self.project.processing_id, {"seg_000002": "Sunny"}
        )
        self.service.select_voice(self.project.processing_id, "Sunny", "voice-e")
        generated = self.service.generate(self.project.processing_id)
        self.assertIsNotNone(generated.output)
        speakers = [
            item.speaker
            for chapter in self.application.generated_analysis.chapters
            for item in chapter.segments
        ]
        self.assertIn("Sunny", speakers)
        self.assertEqual(self.application.generated_voices["Sunny"], "voice-e")

    def test_invalid_processing_id_is_controlled(self) -> None:
        with self.assertRaisesRegex(ReviewStateError, "no válido"):
            self.service.load("../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
