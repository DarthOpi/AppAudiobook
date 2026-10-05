"""Command-line interface for Smart Audiobook."""

import argparse
import json
import logging
from pathlib import Path
from typing import Sequence

from smart_audiobook.application import AudiobookApplicationService
from smart_audiobook.book_processor import BookAnalysis
from smart_audiobook.document_loaders import DocumentLoadError
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
    _configure_cli_logging()
    args = build_parser().parse_args(argv)

    try:
        service = AudiobookApplicationService()
        if args.analyze_only:
            analysis = service.analyze(
                args.input,
                use_llm=not args.no_llm,
            )
            _print_analysis(analysis)
            return 0

        legacy_output = args.output if args.output.suffix.casefold() == ".wav" else None
        output_root = args.output.parent if legacy_output else args.output
        result = service.process(
            args.input,
            output_root,
            use_llm=not args.no_llm,
            full_audiobook_path=legacy_output,
        )
        if args.show_segments:
            _print_analysis(result.analysis)
    except (DocumentLoadError, SpeechGenerationError) as error:
        print(f"Error: {error}")
        return 1

    generated = result.output
    print(f"Audiolibro generado correctamente: {generated.directory.resolve()}")
    print(f"Audio completo: {generated.full_audiobook.resolve()}")
    print(f"Metadatos: {generated.metadata.resolve()}")
    return 0


def _print_analysis(analysis: BookAnalysis) -> None:
    """Render the shared analysis as CLI JSON."""
    document = analysis.document
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


def _configure_cli_logging() -> None:
    """Show project events without enabling verbose dependency logs."""
    project_logger = logging.getLogger("smart_audiobook")
    if not project_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        project_logger.addHandler(handler)
    project_logger.setLevel(logging.INFO)
    project_logger.propagate = False


if __name__ == "__main__":
    raise SystemExit(main())
