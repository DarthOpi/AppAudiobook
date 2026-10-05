"""Tests for automatic character-to-voice assignment."""

import unittest

from smart_audiobook.models import TextSegment
from smart_audiobook.voice_assignment import assign_voices


class AssignVoicesTests(unittest.TestCase):
    def test_each_speaker_gets_a_different_voice_when_possible(self) -> None:
        segments = [
            TextSegment(type="narration", text="Inicio.", speaker="Narrator"),
            TextSegment(type="dialogue", text="Hola.", speaker="María"),
            TextSegment(type="dialogue", text="Hola.", speaker="Pedro"),
        ]

        assignments = assign_voices(segments, ["voice-a", "voice-b", "voice-c"])

        self.assertEqual(
            assignments,
            {"Narrator": "voice-a", "María": "voice-b", "Pedro": "voice-c"},
        )

    def test_voices_are_reused_cyclically_when_they_run_out(self) -> None:
        segments = [
            TextSegment(type="dialogue", text="Uno.", speaker="María"),
            TextSegment(type="dialogue", text="Dos.", speaker="Pedro"),
        ]

        assignments = assign_voices(segments, ["voice-a", "voice-b"])

        self.assertEqual(assignments["Narrator"], "voice-a")
        self.assertEqual(assignments["María"], "voice-b")
        self.assertEqual(assignments["Pedro"], "voice-a")


if __name__ == "__main__":
    unittest.main()

