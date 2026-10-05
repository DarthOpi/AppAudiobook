"""Command-line interface for Smart Audiobook."""

import argparse
import json
from pathlib import Path
from typing import Sequence

from smart_audiobook.audio import generate_audiobook
from smart_audiobook.gemini_resolver import build_gemini_resolver_from_environment
from smart_audiobook.segmenter import segment_text
from smart_audiobook.speaker_identification import SpeakerIdentificationService
from smart_audiobook.speaker_resolvers import RuleBasedSpeakerResolver
from smart_audiobook.text_reader import TextFileError, read_text_file
from smart_audiobook.tts import SpeechGenerationError


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="smart-audiobook",
        description="Convierte un archivo TXT en un archivo de audio WAV.",
    )
    parser.add_argument("input", type=Path, help="Ruta del archivo TXT de entrada.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Ruta del WAV de salida (por defecto: output/<nombre>.wav).",
    )
    parser.add_argument(
        "--show-segments",
        action="store_true",
        help="Muestra por consola el análisis de narración y diálogo.",
    )
    parser.add_argument(
        "--analyze-only",
        action="store_true",
        help="Muestra el análisis sin generar audio.",
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
    output_path = args.output or Path("output") / f"{args.input.stem}.wav"

    try:
        text = read_text_file(args.input)
        segments = segment_text(text)
        llm_resolver = (
            None if args.no_llm else build_gemini_resolver_from_environment()
        )
        analysis = SpeakerIdentificationService(
            rule_resolver=RuleBasedSpeakerResolver(),
            llm_resolver=llm_resolver,
        ).identify(segments)

        if args.show_segments or args.analyze_only:
            print(
                json.dumps(
                    [segment.as_dict() for segment in analysis.segments],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            print("\nCharacters detected:")
            for character in analysis.characters:
                print(f"- {character}")

        if args.analyze_only:
            return 0

        generated_file = generate_audiobook(analysis.segments, output_path)
    except (TextFileError, SpeechGenerationError) as error:
        print(f"Error: {error}")
        return 1

    print(f"Audio generado correctamente: {generated_file.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
