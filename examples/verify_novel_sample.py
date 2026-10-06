"""Bounded real-PDF acceptance; opt-in Gemini, no TTS and no project mutations."""

import argparse
from collections import Counter
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from pypdf import PdfReader

from smart_audiobook.book_processor import analyze_book, analysis_warnings, character_metrics
from smart_audiobook.document_loaders import _build_document, _pdf_page_texts
from smart_audiobook.gemini_resolver import build_gemini_resolver_from_environment
from smart_audiobook.models import NARRATOR, UNKNOWN_SPEAKER, TextSegment
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
from smart_audiobook.tts_config import build_tts_provider


class BoundedResolver:
    """Stop after a small call budget or first provider failure (e.g. quota)."""

    def __init__(self, resolver, limit):
        self.resolver, self.limit = resolver, limit

    @property
    def actual_calls(self):
        return self.resolver.actual_calls

    @property
    def failures(self):
        return self.resolver.failures

    def cache_key(self, context):
        return self.resolver.cache_key(context)

    def resolve(self, context):
        if self.actual_calls >= self.limit or self.failures:
            return None
        return self.resolver.resolve(context)


def legacy_segments(text):
    """Frozen old segmentation, for a comparable regression baseline."""
    result = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"(?:\*\s*\*\s*\*|---|§)", line):
            continue
        if not line.startswith("—"):
            result.append(TextSegment("narration", line, NARRATOR))
        else:
            for i, fragment in enumerate(line.split("—")[1:]):
                if fragment.strip():
                    result.append(TextSegment("dialogue" if i % 2 == 0 else "narration",
                        fragment.strip(), UNKNOWN_SPEAKER if i % 2 == 0 else NARRATOR))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--start-page", type=int, default=8)
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--llm", action="store_true", help="Send compact local context to Gemini using .env")
    parser.add_argument("--max-llm-calls", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.pages <= 10 or args.start_page < 1 or not 1 <= args.max_llm_calls <= 20:
        parser.error("Use 1-10 pages and 1-20 LLM calls.")
    load_dotenv(Path.cwd() / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    reader = PdfReader(args.pdf)
    if args.start_page > len(reader.pages):
        parser.error("Start page exceeds PDF size.")
    pages = [reader.pages[i].extract_text() or "" for i in range(args.start_page-1,
        min(len(reader.pages), args.start_page-1+args.pages))]
    raw = "\n".join(pages)
    cleaned = _pdf_page_texts(pages)
    document = _build_document(args.pdf, "pdf", "\n\n".join(cleaned), source_pages=pages)
    resolver = build_gemini_resolver_from_environment() if args.llm else None
    service = SpeakerIdentificationService(RuleBasedSpeakerResolver(),
        BoundedResolver(resolver, args.max_llm_calls) if resolver else None)
    analysis = analyze_book(document, service)
    voices = build_tts_provider().list_voices()
    summary = {"source_pages": [args.start_page, args.start_page+len(pages)-1],
        "api_key_configured": bool(os.getenv("GEMINI_API_KEY")),
        "before_raw_segments": dict(Counter(s.type for s in legacy_segments(raw))),
        "after_logical_paragraphs": sum(len(p.split("\n\n")) for p in cleaned),
        "after": character_metrics(analysis),
        "characters": [p.canonical_name for p in analysis.registry.profiles if p.canonical_name != NARRATOR and not p.pending],
        "pending_characters": [p.canonical_name for p in analysis.registry.profiles if p.pending],
        "warnings": analysis_warnings(analysis),
        "narrator_absorbed_dialogues_or_thoughts": sum(s.type != "narration" and s.speaker == NARRATOR for ch in analysis.chapters for s in ch.segments),
        "voice_catalog": [{"id": v.id, "name": v.name, "language": v.language, "provider": v.provider} for v in voices]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    assert summary["after"]["dialogue_segments"] > 0, "No speech detected in selected pages"
    assert summary["after"]["internal_thought_segments"] > 0, "No thoughts detected in selected pages"
    assert summary["narrator_absorbed_dialogues_or_thoughts"] == 0
    if args.llm and resolver and not resolver.actual_calls:
        raise SystemExit("No real Gemini call was needed/completed; inspect sample and rules.")


if __name__ == "__main__":
    main()
