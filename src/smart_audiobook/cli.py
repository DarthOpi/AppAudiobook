"""Command-line interface for Smart Audiobook."""

import argparse
from pathlib import Path
from typing import Sequence

from smart_audiobook.text_reader import TextFileError, read_text_file
from smart_audiobook.tts import SpeechGenerationError, synthesize_to_wav


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Smart Audiobook command-line application."""
    args = build_parser().parse_args(argv)
    output_path = args.output or Path("output") / f"{args.input.stem}.wav"

    try:
        text = read_text_file(args.input)
        generated_file = synthesize_to_wav(text, output_path)
    except (TextFileError, SpeechGenerationError) as error:
        print(f"Error: {error}")
        return 1

    print(f"Audio generado correctamente: {generated_file.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

