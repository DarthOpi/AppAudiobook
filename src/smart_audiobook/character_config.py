"""Conservative, centralized speaker confidence and context policy."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CharacterConfig:
    accept_confidence: float = 0.85
    review_confidence: float = 0.60
    candidate_limit: int = 6
    active_window: int = 12
    prompt_version: str = "v2"

    def __post_init__(self) -> None:
        if not 0 <= self.review_confidence <= self.accept_confidence <= 1:
            raise ValueError("Umbrales de confianza inválidos.")
        if not 1 <= self.candidate_limit <= 12 or self.active_window < 1:
            raise ValueError("Límites de contexto de personajes inválidos.")

    @classmethod
    def from_environment(cls) -> "CharacterConfig":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        return cls(
            accept_confidence=float(os.getenv("SPEAKER_ACCEPT_CONFIDENCE", "0.85")),
            review_confidence=float(os.getenv("SPEAKER_REVIEW_CONFIDENCE", "0.60")),
            candidate_limit=int(os.getenv("SPEAKER_CANDIDATE_LIMIT", "6")),
            active_window=int(os.getenv("CHARACTER_ACTIVE_WINDOW", "12")),
            prompt_version=os.getenv("SPEAKER_PROMPT_VERSION", "v2"),
        )
