"""Allow running the application with ``python -m smart_audiobook``."""

from smart_audiobook.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

