"""Omni photos must reach Director's existing vision-aware planners."""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services.director_pipeline import _director_planner_image_inputs


class PlannerReferenceTests(unittest.TestCase):
    def test_omni_photos_reach_planner_with_labels_without_mutating_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            paths = [str(Path(root) / f'{i}.jpg') for i in range(3)]
            for path in paths:
                Path(path).touch()
            params = {'minimax_h3_references': [
                {'type': 'image', 'path': paths[0], 'role': 'Aya'},
                {'type': 'image', 'path': paths[1], 'role': 'Aya'},
                {'type': 'image', 'path': paths[2], 'role': 'Bedroom', 'image_intent': 'scene'},
            ]}
            original = copy.deepcopy(params)
            result = _director_planner_image_inputs(params)
            self.assertEqual(result['reference_image_path'], paths[0])
            self.assertEqual(result['character_ref_paths'], paths[1:2])
            self.assertEqual(result['character_ref_labels'], ['Aya'])
            self.assertEqual(result['location_ref_paths'], paths[2:])
            self.assertEqual(params, original)

    def test_missing_photo_stops_planning(self):
        with self.assertRaisesRegex(ValueError, 'photo is missing'):
            _director_planner_image_inputs({'minimax_h3_references': [
                {'type': 'image', 'path': '/does-not-exist/identity.jpg'}]})

    def test_existing_main_photo_is_not_duplicated(self):
        params = {'reference_image_path': '/main.jpg', 'minimax_h3_references': [
            {'type': 'image', 'path': '/main.jpg'}]}
        result = _director_planner_image_inputs(params)
        self.assertEqual(result['reference_image_path'], '/main.jpg')
        self.assertEqual(result['character_ref_paths'], [])
