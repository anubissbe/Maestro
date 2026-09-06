import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services.director_minimax import requests_for_shots, render_clips


class Outputs(list):
    def __init__(self, values, mapping):
        super().__init__(values)
        self.clip_output_files = mapping


class TestDirectorMiniMax(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_reference_images_and_duration_follow_plan(self):
        from PIL import Image
        image = self.root / 'character.png'
        Image.new('RGB', (256, 256)).save(image)
        params = {'character_ref_paths': [str(image)], 'reference_image_path': str(image)}
        shots = requests_for_shots('abcd1234', params, [{'video_prompt': 'A sunrise'}],
                                   [{'start': 5, 'end': 11.25}], [], self.root)
        body, duration = shots[0]
        self.assertEqual(duration, 6.25)
        self.assertEqual(body['duration'], 7)
        self.assertEqual(body['references'], [{'role': 'reference_image', 'path': str(image)}])
        self.assertEqual(body['client_id'], 'director-abcd1234-shot-0001')

    def test_omni_photo_numbers_survive_legacy_and_generated_images(self):
        import base64
        from PIL import Image
        from services.minimax_api import build_video_payload
        paths = []
        for name, color in [('identity', 'red'), ('legacy', 'blue'), ('shot', 'green')]:
            path = self.root / (name + '.png')
            Image.new('RGB', (256, 256), color).save(path)
            paths.append(path)
        params = {'minimax_h3_references': [{'type': 'image', 'path': str(paths[0])}],
                  'reference_image_path': str(paths[1])}
        shots = requests_for_shots('abcd1234', params,
            [{'video_prompt': 'Keep the identity of <Picture 1>'}] * 2,
            [], [paths[2].name] * 2, self.root)
        for body, _ in shots:
            self.assertEqual([r['path'] for r in body['references']], list(map(str, paths)))
            payload = build_video_payload(body, self.root)
            for item, path in zip(payload['content'][1:], paths):
                self.assertEqual(item['role'], 'reference_image')
                self.assertEqual(base64.b64decode(item['image_url']['url'].split(',')[1]), path.read_bytes())

    def test_invalid_later_shot_prevents_any_paid_request(self):
        with patch('services.director_minimax.MiniMaxJobs') as manager:
            with self.assertRaisesRegex(ValueError, '15-second'):
                render_clips('abcd1234', {}, [{'video_prompt': 'one'}, {'video_prompt': 'two'}],
                    [{'duration_sec': 5}, {'duration_sec': 20}], [], self.root, Mock(), Mock(), Mock(), lambda: False, Outputs)
            manager.assert_not_called()

    def test_completed_results_are_trimmed_and_mapped_to_shots(self):
        manager = Mock()
        manager.start.return_value = {'status': 'completed', 'task_id': 'task-1', 'output': 'raw.mp4'}
        (self.root / 'raw.mp4').write_bytes(b'video')
        def trim(args, **kwargs):
            Path(args[-1]).write_bytes(b'trimmed')
        with patch('services.director_minimax.MiniMaxJobs', return_value=manager), patch('services.director_minimax.subprocess.run', side_effect=trim) as ffmpeg:
            outputs = render_clips('abcd1234', {}, [{'video_prompt': 'sunrise'}], [{'duration_sec': 4.5}],
                    [], self.root, Mock(), Mock(), Mock(), lambda: False, Outputs)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs.clip_output_files[0], outputs[0])
        self.assertTrue((self.root / outputs[0]).exists())
        self.assertIn('4.5', ffmpeg.call_args.args[0])

    def test_single_shot_rerun_keeps_song_and_original_offset(self):
        from services import director_pipeline as pipeline
        state = {'clips': [{'video_prompt': 'Sing', 'planned_clip': {'start': 12, 'duration_sec': 5}}],
                 '_params_snapshot': {'video_engine': 'minimax', 'audio_path': 'song.mp3'}}
        with patch.object(pipeline, 'load_pipeline_state', return_value=state), \
             patch.object(pipeline, '_find_pipeline_file', return_value=None), \
             patch.object(pipeline, '_update_saved_pipeline'), \
             patch('services.director_minimax.render_clips', return_value=['clip.mp4']) as render:
            pipeline._rerun_clip_video_impl(str(self.root), 'project', 0)
        self.assertEqual(render.call_args.args[1]['audio_path'], 'song.mp3')
        self.assertEqual(render.call_args.args[3][0]['start'], 12)
        self.assertFalse(render.call_args.kwargs['join'])

    def test_cancelled_pipeline_does_not_submit(self):
        with patch('services.director_minimax.MiniMaxJobs') as manager:
            outputs = render_clips('abcd1234', {}, [{'video_prompt': 'sunrise'}], [], [],
                                   self.root, Mock(), Mock(), Mock(), lambda: True, Outputs)
            self.assertFalse(outputs)
            manager.return_value.start.assert_not_called()

if __name__ == '__main__':
    unittest.main()
