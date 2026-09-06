import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services.minimax_studio import studio_request, run_studio_job
from services.job_lifecycle import try_start, update_job, finish_job


class TestMiniMaxStudio(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.body = {'prompt': 'A sunrise', '_api_duration_seconds': 5, 'resolution': '1280x720'}

    def test_normal_frame_inputs_are_forwarded(self):
        from PIL import Image
        image = self.root / 'first.png'
        Image.new('RGB', (256, 256)).save(image)
        result = studio_request(self.body | {'image_start': str(image)}, self.root, 'studio-12345678-generation')
        self.assertEqual(result['references'], [{'role': 'first_frame', 'path': str(image)}])
        self.assertEqual(result['ratio'], '16:9')
        self.assertEqual(result['resolution'], '768P')

    def test_unsupported_controls_fail_before_generation(self):
        for extra in ({'activated_loras': ['lora']}, {'image_mode': 3}, {'_api_duration_seconds': 20}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                studio_request(self.body | extra, self.root, 'studio-12345678-generation')

    def test_cloud_result_finishes_the_normal_gallery_job(self):
        job = {'id': '12345678', 'status': 'queued', 'params': self.body, 'out_dir': str(self.root)}
        manager = Mock()
        manager.uploads.return_value = self.root
        manager.start.return_value = {'status': 'completed', 'output': 'result.mp4'}
        self.assertTrue(run_studio_job(job, manager, try_start, update_job, finish_job))
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(job['output_files'], ['result.mp4'])
        self.assertEqual(manager.start.call_args.kwargs['output_dir'], str(self.root))

    def test_cancelled_job_never_submits(self):
        job = {'id': '12345678', 'status': 'cancelled', 'params': self.body, 'out_dir': str(self.root)}
        manager = Mock()
        self.assertFalse(run_studio_job(job, manager, try_start, update_job, finish_job))
        manager.start.assert_not_called()

if __name__ == '__main__':
    unittest.main()
