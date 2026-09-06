"""Exercise the real module namespace, without injected regex imports."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services import llm_service


class ReferenceCleanupTests(unittest.TestCase):
    def test_reference_prompt_without_dialogue_preserves_description(self):
        description = "<Subject 1> walks through a sunlit forest."
        result = "\n".join([
            "subject_definitions: A character.",
            "summary: A quiet walk.",
            "retention_analysis: Use the reference.",
            "detailed_description: " + description,
            "overall_soundscape: Birds and footsteps.",
            "non_diegetic_music: N/A",
        ])
        cleaned = llm_service._canonicalize_h3_ref2va_reference_fields(
            result, "<Picture 1>: identity", "A character walks through a forest."
        )
        self.assertIn("detailed_description: " + description, cleaned)
        self.assertIn("<Picture 1>", cleaned)
        self.assertIn("overall_soundscape: Birds and footsteps.", cleaned)


if __name__ == "__main__":
    unittest.main()
