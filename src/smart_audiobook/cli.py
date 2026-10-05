"""Command-line interface for Smart Audiobook."""

import argparse
import json
from pathlib import Path
from typing import Sequence

from smart_audiobook.book_processor import analyze_book, generate_book
from smart_audiobook.document_loaders import DocumentLoadError, load_document
from smart_audiobook.gemini_resolver import build_gemini_resolver_from_environment
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
from smart_audiobook.tts import SpeechGenerationError


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="smart-audiobook",
        description="Convierte documentos TXT, PDF o DOCX en un audiolibro WAV.",
    )
    parser.add_argument("input", type=Path, help="Ruta del documento TXT, PDF o DOCX.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("output"),
        help=(
            "Carpeta raíz de salida (por defecto: output) o ruta .wav "
            "compatible con versiones anteriores."
        ),
    )
    parser.add_argument(
        "--show-segments",
        action="store_true",
        help="Muestra el análisis de narración y diálogo por capítulo.",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Carga, divide y analiza el documento sin generar audio.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Desactiva la resolución LLM aunque exista una clave configurada.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Smart Audiobook command-line application."""
    args = build_parser().parse_args(argv)

    try:
        document = load_document(args.input)
        llm_resolver = (
            None if args.no_llm else build_gemini_resolver_from_environment()
        )
        analysis = analyze_book(
            document,
            SpeakerIdentificationService(
                rule_resolver=RuleBasedSpeakerResolver(),
                llm_resolver=llm_resolver,
            ),
        )

        if args.show_segments or args.analyze_only:
            print(
                json.dumps(
                    {
                        "title": document.title,
                        "format": document.format,
                        "chapters": [
                            {
                                "number": chapter.number,
                                "title": chapter.title,
                                "segments": [
                                    segment.as_dict() for segment in chapter.segments
                                ],
                            }
                            for chapter in analysis.chapters
                        ],
                        "characters": list(analysis.characters),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )

        if args.analyze_only:
            return 0

        legacy_output = args.output if args.output.suffix.casefold() == ".wav" else None
        output_root = args.output.parent if legacy_output else args.output
        generated = generate_book(
            analysis,
            output_root,
            full_audiobook_path=legacy_output,
        )
    except (DocumentLoadError, SpeechGenerationError) as error:
        print(f"Error: {error}")
        return 1

    print(f"Audiolibro generado correctamente: {generated.directory.resolve()}")
    print(f"Audio completo: {generated.full_audiobook.resolve()}")
    print(f"Metadatos: {generated.metadata.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
