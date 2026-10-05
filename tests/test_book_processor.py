"""Tests for chapter outputs and metadata without invoking system TTS."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from smart_audiobook.book_processor import analyze_book, generate_book
from smart_audiobook.document_loaders import load_document
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver

FIXTURES = Path(__file__).parent / "fixtures"


def _fake_generate(_segments: object, output_path: Path, **_kwargs: object) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"chapter")
    return output_path


def _fake_combine(_inputs: object, output_path: Path) -> Path:
    output_path.write_bytes(b"full")
    return output_path


class BookProcessorTests(unittest.TestCase):
    @patch("smart_audiobook.book_processor.combine_wav_files", _fake_combine)
    @patch("smart_audiobook.book_processor.generate_audiobook", _fake_generate)
    @patch(
        "smart_audiobook.book_processor.get_available_voice_ids",
        return_value=["voice-1", "voice-2", "voice-3"],
    )
    def test_generates_chapters_full_book_and_metadata(self, _voices: object) -> None:
        document = load_document(FIXTURES / "sample_book.txt")
        analysis = analyze_book(
            document,
            SpeakerIdentificationService(RuleBasedSpeakerResolver()),
        )
        with tempfile.TemporaryDirectory() as directory:
            result = generate_book(analysis, Path(directory))
            self.assertEqual(len(result.chapter_files), len(document.chapters))
            self.assertTrue(result.full_audiobook.is_file())
            metadata = json.loads(result.metadata.read_text(encoding="utf-8"))
            self.assertEqual(metadata["chapter_count"], len(document.chapters))
            self.assertEqual(metadata["format"], "txt")
            self.assertNotIn("GEMINI_API_KEY", metadata)
            self.assertEqual(
                set(metadata["voice_assignments"]),
                set(analysis.characters),
            )


if __name__ == "__main__":
    unittest.main()
