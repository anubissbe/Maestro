"""Suggestions must stay inside the installed catalog and fail closed on conflicts."""
import ast
import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from services.lora_suggestions import suggest_loras


class SuggestionsTests(unittest.TestCase):
    def setUp(self):
        self.catalog = [{"filename": "style.safetensors", "lora_id": "civitai:12",
                         "trained_words": ["exactTrigger"], "guide": "Watercolor style"}]

    def run_response(self, rows, active=()):
        generate = Mock(return_value=json.dumps({"suggestions": rows}))
        result = suggest_loras(self.catalog, "paint a forest", "flux", ["image.png"], generate, active)
        self.assertEqual(generate.call_args.kwargs["image_paths"], ["image.png"])
        return result

    def test_hallucinations_duplicates_and_keywords(self):
        row = {"filename": "style.safetensors", "reason": "Matches watercolor", "conflicts": [],
               "trained_words": ["invented"]}
        result = self.run_response([{"filename": "missing.safetensors", "reason": "fake"}, row, row])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["trained_words"], ["exactTrigger"])
        self.assertEqual(result[0]["conflicts"], [])

    def test_alternative_version_blocked(self):
        result = self.run_response([{"filename": "style.safetensors", "reason": "match", "conflicts": []}],
                                   [{"filename": "old.safetensors", "lora_id": "civitai:12"}])
        self.assertTrue(result[0]["conflicts"])

    def test_missing_assessment_is_advisory_for_manual_addition(self):
        result = self.run_response([{"filename": "style.safetensors", "reason": "match"}])
        self.assertEqual(result[0]["conflicts"], [])
        self.assertTrue(result[0]["warnings"])
        self.assertFalse(result[0]["assessment_complete"])

    def test_llm_interaction_claim_is_not_a_verified_conflict(self):
        for field in ("warnings", "conflicts"):
            result = self.run_response([{"filename": "style.safetensors", "reason": "match",
                                        field: ["Another style could affect the appearance"]}])
            self.assertEqual(result[0]["conflicts"], [])
            self.assertEqual(result[0]["warnings"], ["Another style could affect the appearance"])

    def test_clear_assessment_has_no_warning(self):
        result = self.run_response([{"filename": "style.safetensors", "reason": "match", "warnings": []}])
        self.assertTrue(result[0]["assessment_complete"])
        self.assertEqual(result[0]["warnings"], [])

    def test_active_file_never_suggested(self):
        self.assertEqual(self.run_response(
            [{"filename": "style.safetensors", "reason": "match", "conflicts": []}], self.catalog), [])

    def test_malformed_response_fails(self):
        with self.assertRaises(ValueError):
            suggest_loras(self.catalog, "x", "flux", [], Mock(return_value='{"suggestions":null}'))

    def test_endpoint_excludes_missing_managed_and_hidden_files(self):
        # Compile the route alone to avoid loading diffusion models in unit tests.
        tree = ast.parse((ROOT / "app/launch.py").read_text())
        route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "llm_suggest_loras")
        route.decorator_list = []
        rows = self.catalog + [{"filename": "missing.safetensors", "managed": True},
                               {"filename": "hidden.safetensors", "nsfw": True}]
        namespace = {
            "wgp": SimpleNamespace(server_config={}, resolve_lora_path=lambda m, f: f),
            "list_loras_details": lambda m: {"loras": rows}, "os": os,
            "_guard_interactive_llm_against_generation": Mock(), "_ensure_llm_loaded": Mock(),
        }
        exec(compile(ast.Module(body=[route], type_ignores=[]), "launch.py", "exec"), namespace)
        with patch("os.path.isfile", side_effect=lambda p: p != "missing.safetensors"), \
             patch("services.lora_suggestions.suggest_loras", return_value=[]) as suggest:
            namespace["llm_suggest_loras"]({"model_type": "flux", "prompt": "forest"})
        self.assertEqual(suggest.call_args.args[0], self.catalog)


if __name__ == "__main__":
    unittest.main()
