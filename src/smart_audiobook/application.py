"""Application service shared by the command line and web interfaces."""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from smart_audiobook.book_processor import (
    BookAnalysis,
    BookOutput,
    analyze_book,
    generate_book,
)
from smart_audiobook.document_loaders import load_document
from smart_audiobook.gemini_resolver import build_gemini_resolver_from_environment
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
from smart_audiobook.tts_config import TTSConfig, build_tts_provider
from smart_audiobook.tts_providers import TTSProvider

LOGGER = logging.getLogger(__name__)

SpeakerServiceFactory = Callable[[bool], SpeakerIdentificationService]


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    """Analysis and generated files returned to any presentation layer."""

    analysis: BookAnalysis
    output: BookOutput

    @property
    def segment_count(self) -> int:
        return sum(len(chapter.segments) for chapter in self.analysis.chapters)

    @property
    def word_count(self) -> int:
        return len(self.analysis.document.full_text.split())


class AudiobookApplicationService:
    """Coordinate the existing document pipeline without UI concerns."""

    def __init__(
        self,
        speaker_service_factory: SpeakerServiceFactory | None = None,
    ) -> None:
        self._speaker_service_factory = (
            speaker_service_factory or _build_speaker_service
        )

    def analyze(self, source_path: Path, use_llm: bool = True) -> BookAnalysis:
        """Load and analyze a source document without generating audio."""
        LOGGER.info("Loading document: %s", source_path.name)
        document = load_document(source_path)
        LOGGER.info(
            "Document loaded: format=%s chapters=%d",
            document.format,
            len(document.chapters),
        )
        analysis = analyze_book(
            document,
            self._speaker_service_factory(use_llm),
        )
        LOGGER.info("Characters detected: %d", len(analysis.characters))
        return analysis

    def process(
        self,
        source_path: Path,
        output_root: Path,
        use_llm: bool = True,
        full_audiobook_path: Path | None = None,
        tts_provider: TTSProvider | None = None,
        resume: bool = True,
        force_regenerate: bool = False,
    ) -> ProcessingResult:
        """Run the complete pipeline and return its presentation-ready result."""
        analysis = self.analyze(source_path, use_llm=use_llm)
        LOGGER.info("Audio generation started: %s", source_path.name)
        provider = tts_provider or build_tts_provider()
        output = generate_book(
            analysis,
            output_root,
            full_audiobook_path=full_audiobook_path,
            tts_provider=provider,
            resume=resume,
            force_regenerate=force_regenerate,
            tts_config=TTSConfig.from_environment(
                provider_override=getattr(provider, "provider_id", None)
            ),
        )
        LOGGER.info(
            "Audio generation finished: chapters=%d",
            len(output.chapter_files),
        )
        return ProcessingResult(analysis=analysis, output=output)

    def generate(
        self,
        analysis: BookAnalysis,
        output_root: Path,
        voices_by_speaker: dict[str, str] | None = None,
        tts_provider: TTSProvider | None = None,
        **generation_options: object,
    ) -> ProcessingResult:
        """Generate audio from an already reviewed analysis without re-analysis."""
        LOGGER.info("Audio generation started: %s", analysis.document.title)
        if "tts_config" not in generation_options:
            selected = getattr(tts_provider, "provider_id", None)
            override = selected if selected in {"piper", "system"} else None
            generation_options["tts_config"] = TTSConfig.from_environment(
                provider_override=override
            )
        output = generate_book(
            analysis,
            output_root,
            voices_by_speaker=voices_by_speaker,
            tts_provider=tts_provider,
            **generation_options,
        )
        LOGGER.info("Audio generation finished: chapters=%d", len(output.chapter_files))
        return ProcessingResult(analysis=analysis, output=output)


def _build_speaker_service(use_llm: bool) -> SpeakerIdentificationService:
    llm_resolver = (
        build_gemini_resolver_from_environment() if use_llm else None
    )
    return SpeakerIdentificationService(
        rule_resolver=RuleBasedSpeakerResolver(),
        llm_resolver=llm_resolver,
    )
