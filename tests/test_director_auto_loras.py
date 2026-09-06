"""Automatic Director additions must preserve schedules and model separation."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services.director_auto_loras import select_addition, apply_keywords
from services import director_pipeline as pipeline


class AutoLoraTests(unittest.TestCase):
    def setUp(self):
        self.catalog = [
            {"filename": "existing", "lora_id": "id:1"},
            {"filename": "alternate", "lora_id": "id:1"},
            {"filename": "style", "lora_id": "id:2", "trained_words": ["paint"],
             "recommended_weights": {"default": 0.7, "phases": [{"phase": 2, "default": 0.4}]}},
            {"filename": "other", "lora_id": "id:3"},
            {"filename": "turbo", "managed": True},
        ]
        self.selected = {"activated_loras": ["existing"], "loras_multipliers": "0.9;0.3"}

    def test_one_addition_preserves_existing_schedule_and_metadata_weights(self):
        generate = Mock(return_value=json.dumps({"suggestions": [
            {"filename": "style", "reason": "Watercolor project", "conflicts": []},
            {"filename": "other", "reason": "Alternative", "conflicts": []},
        ]}))
        updated, choice = select_addition(self.catalog, self.selected, "paint", "model", [], generate, 2)
        self.assertEqual(updated["activated_loras"], ["existing", "style"])
        self.assertEqual(updated["loras_multipliers"], "0.9;0.3 0.7;0.4")
        self.assertEqual(choice["trained_words"], ["paint"])
        sent = json.loads(generate.call_args.kwargs["prompt"])
        self.assertEqual([r["filename"] for r in sent["catalog"]], ["style", "other"])
        self.assertEqual(self.selected["activated_loras"], ["existing"])

    def test_conflicted_or_unassessed_choice_is_not_added(self):
        for conflicts in (["existing"], None):
            row = {"filename": "style", "reason": "match"}
            if conflicts is not None:
                row["conflicts"] = conflicts
            updated, choice = select_addition(self.catalog, self.selected, "paint", "model", [],
                Mock(return_value=json.dumps({"suggestions": [row]})))
            self.assertIsNone(choice)
            self.assertEqual(updated, self.selected)

    def test_keywords_scoped_and_idempotent_in_windows_and_keyframes(self):
        plans = [{"image_prompt": "A forest", "video_prompt": "A walk", "window_prompts": ["A walk"],
                  "keyframe_prompts": ["A forest"]}]
        choices = {"image": {"trained_words": ["paint"]}, "video": {"trained_words": ["motion"]}}
        apply_keywords(plans, choices)
        apply_keywords(plans, choices)
        self.assertEqual(plans[0]["image_prompt"], "A forest, paint")
        self.assertEqual(plans[0]["video_prompt"], "A walk, motion")
        self.assertEqual(plans[0]["window_prompts"], ["A walk, motion"])
        self.assertEqual(plans[0]["keyframe_prompts"], ["A forest, paint"])

    def test_pipeline_selects_once_and_skips_disabled_model(self):
        params = {"auto_select_loras": True, "image_model": "image", "video_model": "disabled",
                  "scene_description": "forest", "image_loras": {}}
        wgp = SimpleNamespace(server_config={}, get_model_def=lambda model: {"loras_disabled": model == "disabled"},
                              resolve_lora_path=lambda m, name: name)
        catalog = Mock(return_value={"loras": self.catalog, "guidance_max_phases": 1})
        with patch.object(pipeline, "_wgp", wgp), patch.object(pipeline, "_list_lora_details", catalog), \
             patch.object(pipeline, "_save_pipeline_state"), patch.object(pipeline, "_update_pipeline"), \
             patch("os.path.isfile", return_value=True), \
             patch("services.llm_service.generate", return_value='{"suggestions":[]}') as generate:
            pipeline._select_automatic_loras("test", params)
            pipeline._select_automatic_loras("test", params)
        self.assertEqual(catalog.call_count, 1)
        self.assertEqual(generate.call_count, 1)
        self.assertTrue(params["_auto_loras_checked"])

    def test_disabled_option_never_reads_catalog(self):
        with patch.object(pipeline, "_list_lora_details") as catalog:
            pipeline._select_automatic_loras("test", {"auto_select_loras": False})
        catalog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
