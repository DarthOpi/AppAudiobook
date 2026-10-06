"""Exercise EPUB import, review, two processing blocks and resumable audio."""

import argparse
import json
import tempfile
import wave
from pathlib import Path

from create_demo_epub import create_demo_epub
from smart_audiobook.review_service import ReviewService
from smart_audiobook.review_store import ReviewProjectStore
from smart_audiobook.tts_config import build_tts_provider
from smart_audiobook.tts_providers import VoiceInfo


class DemoPCMProvider:
    """A valid, silent mock WAV, for a portable smoke test without models/network."""
    provider_id = "demo-pcm"
    model_id = "offline-smoke"

    def list_voices(self):
        return [VoiceInfo("demo", "Demo PCM")]

    def synthesize(self, text, output_path, voice_id, settings=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(22050)
            wav.writeframes(b"\x00\x00" * 2205)
        return output_path


def verify(work_root: Path, real_tts: bool):
    work_root.mkdir(parents=True, exist_ok=True)
    source = create_demo_epub(work_root / "demo_v09.epub")
    review = ReviewService(ReviewProjectStore(work_root / "projects"),
                           build_tts_provider() if real_tts else DemoPCMProvider())
    project = review.books.import_book(source)
    identity = project.processing_id
    assert len(project.analysis.document.chapters) == 3 and not project.analysis.chapters
    review.books.select_expression(identity, "1")
    project = review.books.analyze(identity, use_llm=False)
    dialogue = next(s for s in project.dialogues if s.speaker == "Elena")
    review.update_speakers(identity, {dialogue.id: "Elena"})
    first = review.generate(identity)
    assert len(first.output.chapter_files) == 1
    first_audio = first.output.chapter_files[0]
    first_bytes = first_audio.read_bytes()
    review.edit_alias(identity, "Elena", "Luz", 2)
    review.books.select_expression(identity, "2-3")
    review.books.analyze(identity, use_llm=False)
    final = review.generate(identity)
    assert len(final.output.chapter_files) == 3 and first_audio.read_bytes() == first_bytes
    resumed = review.generate(identity)
    assert resumed.output.full_audiobook.is_file()
    reviewed = review.books.analyze(identity, {1}, reanalyze=True, use_llm=False)
    assert next(s for s in reviewed.dialogues if s.id == dialogue.id).resolution_method == "manual"
    assert reviewed.analysis.registry.find("Luz", 1) is None
    assert reviewed.analysis.registry.find("Luz", 2).canonical_name == "Elena"
    final = review.generate(identity, {1})
    with wave.open(str(final.output.full_audiobook)) as wav:
        duration = round(wav.getnframes() / wav.getframerate(), 2)
    print(json.dumps({"project_id": identity, "title": final.analysis.document.title,
        "author": final.analysis.document.author, "chapters_analyzed": len(final.analysis.chapters),
        "chapter_audio_files": len(final.output.chapter_files), "manual_override_preserved": True,
        "future_alias_hidden": True, "tts": "real local" if real_tts else "silent mock PCM",
        "audio_seconds": duration, "audio": str(final.output.full_audiobook)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--real-tts", action="store_true")
    args = parser.parse_args()
    if args.work_root:
        verify(args.work_root, args.real_tts)
    else:
        with tempfile.TemporaryDirectory(prefix="smart-audiobook-v09-") as directory:
            verify(Path(directory), args.real_tts)
