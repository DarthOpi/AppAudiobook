"""Inspect a bounded PDF sample and the configured voice catalog, without API calls."""

import argparse
import json
import os
from collections import Counter
from pathlib import Path


def main():
    from pypdf import PdfReader
    from dotenv import load_dotenv
    from smart_audiobook.segmenter import segment_text
    from smart_audiobook.tts_config import build_tts_provider
    from smart_audiobook.speaker_identification import SpeakerIdentificationService
    from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
    from smart_audiobook.document_loaders import _pdf_page_texts
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--pages", type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.pages <= 10 or args.start_page < 1:
        parser.error("Use 1-10 pages and a positive start page.")
    reader = PdfReader(args.pdf)
    selected_pages = [reader.pages[i].extract_text() or "" for i in range(args.start_page-1,
        min(len(reader.pages), args.start_page-1+args.pages))]
    reconstructed = _pdf_page_texts(selected_pages)
    reports = []
    for offset, text in enumerate(selected_pages):
        index = args.start_page - 1 + offset
        segments = segment_text(text)
        analysis = SpeakerIdentificationService(RuleBasedSpeakerResolver()).identify(segments)
        reports.append({"page": index+1, "sample": text[:180], "raw_lines": len(text.splitlines()),
            "segments": dict(Counter(s.type for s in segments)),
            "leading_em_dash": sum(line.lstrip().startswith("—") for line in text.splitlines()),
            "double_quotes": text.count('"') + text.count("“"),
            "single_quotes": text.count("'") + text.count("‘")})
        reports[-1]["characters_by_rules"] = list(analysis.characters)
        reports[-1]["reconstructed_paragraphs"] = len(reconstructed[offset].split("\n\n"))
        reports[-1]["paragraph_samples"] = [p[:150] for p in reconstructed[offset].split("\n\n")[:3]]
        reports[-1]["narrative_samples"] = [line[:150] for line in text.splitlines()
            if line.lstrip().startswith(("—", "'", "‘", '"', "“")) or "dijo" in line or "respondió" in line][:8]
    load_dotenv(Path.cwd() / ".env")
    print(json.dumps({"pdf_pages": len(reader.pages), "sample_pages": reports,
        "api_key_configured": bool(os.getenv("GEMINI_API_KEY"))}, ensure_ascii=False, indent=2), flush=True)
    provider = build_tts_provider()
    print(json.dumps({"provider": provider.provider_id, "model": provider.model_id,
        "voices": [{"id": v.id, "name": v.name, "language": v.language} for v in provider.list_voices()]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
