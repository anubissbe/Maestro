import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services.h3_sequence_continuity import director_continuity_handoffs


class DirectorHandoffsTests(unittest.TestCase):
    def test_shared_scene_carries_state_across_camera_cuts_but_not_location_changes(self):
        plans = [{'_director_continuity_group': group, '_director_continuity_strategy': 'independent'}
                 for group in ('room_day', 'room_day', 'street_night', 'room_day')]
        self.assertEqual(director_continuity_handoffs(plans, []), [False, True, False, False])

    def test_missing_metadata_is_not_a_global_scene(self):
        self.assertEqual(director_continuity_handoffs([{}, {}, {}], []), [False] * 3)

    def test_split_segments_retain_state_without_scene_labels(self):
        timeline = [{'_director_source_clip_indices': [0], '_director_segment_index': index} for index in (0, 1)]
        self.assertEqual(director_continuity_handoffs([{}, {}], timeline), [False, True])

    def test_explicit_scene_change_overrides_shared_source(self):
        plans = [{'_director_continuity_group': 'a'}, {'_director_continuity_group': 'b'}]
        timeline = [{'_director_source_clip_indices': [0], '_director_segment_index': index} for index in (0, 1)]
        self.assertEqual(director_continuity_handoffs(plans, timeline), [False, False])


if __name__ == '__main__':
    unittest.main()
