"""Cloud billing boundaries, input validation, and durable result recovery."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from services import minimax_api as api
from services import llm_service as llm


class MiniMaxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = {"minimax_api_key": "payg-test", "minimax_subscription_api_key": "subscription-test"}
        self.manager = api.MiniMaxJobs(lambda: self.config, lambda: self.root / "outputs",
                                       lambda: self.root, self.root / "jobs.json")
        self.body = {"client_id": "01234567-89ab-cdef", "prompt": "A sunrise", "duration": 4}

    def test_keys_never_fall_back(self):
        self.assertEqual(api.payg_key(self.config), "payg-test")
        self.assertEqual(llm.provider_api_key("minimax_subscription", self.config), "subscription-test")
        self.assertEqual(llm.provider_api_key("minimax", self.config), "payg-test")
        self.config.pop("minimax_api_key")
        with self.assertRaises(ValueError):
            api.payg_key(self.config)
        self.assertEqual(llm.provider_api_key("minimax", self.config), "")

    def test_model_matrix_and_loras_rejected_before_submission(self):
        for extra in ({"model": "MiniMax-H3-Max"}, {"resolution": "480P"},
                      {"duration": True}, {"loras": ["custom.safetensors"]}):
            with self.subTest(extra=extra), patch.object(api, "api_request") as request:
                with self.assertRaises(ValueError):
                    self.manager.start(self.body | extra)
                request.assert_not_called()

    def test_uploaded_image_and_path_boundary(self):
        from PIL import Image
        path = self.root / "ref.png"
        Image.new("RGB", (256, 256)).save(path)
        payload = api.build_video_payload(self.body | {"references": [{"role": "first_frame", "path": str(path)}]}, self.root)
        self.assertTrue(payload["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        with self.assertRaises(ValueError):
            api.build_video_payload(self.body | {"references": [{"role": "first_frame", "path": __file__}]}, self.root)
        Image.new("RGB", (32, 32)).save(path)
        with self.assertRaises(ValueError):
            api.build_video_payload(self.body | {"references": [{"role": "first_frame", "path": str(path)}]}, self.root)

    def test_clip_duration_error_is_distinct_from_missing_ffprobe(self):
        from types import SimpleNamespace
        probe = {'format': {'duration': '19.8'}, 'streams': [{'codec_type': 'video'}]}
        with patch.object(api.subprocess, 'run', return_value=SimpleNamespace(stdout=json.dumps(probe))):
            with self.assertRaisesRegex(ValueError, '19.80 seconds'):
                api.validate_media(self.root / 'reference.mp4', 'video')
        with patch.object(api.subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaisesRegex(ValueError, 'FFprobe is not installed'):
                api.validate_media(self.root / 'reference.mp4', 'video')

    def test_retry_is_idempotent_and_secrets_are_not_persisted(self):
        with patch.object(api, "api_request", return_value={"task_id": "task-123"}) as request, patch.object(self.manager, "monitor"):
            first = self.manager.start(self.body)
            self.assertEqual(self.manager.start(self.body), first)
            request.assert_called_once()
            self.assertEqual(request.call_args.args[2], "payg-test")
            with self.assertRaises(ValueError):
                self.manager.start(self.body | {"prompt": "different"})
        saved = self.manager.state_path.read_text()
        self.assertNotIn("payg-test", saved)
        self.assertNotIn("subscription-test", saved)
        self.assertNotIn("_output_dir", first)

    def test_uncertain_submission_is_not_retried(self):
        with patch.object(api, "api_request", side_effect=RuntimeError("connection interrupted")) as request:
            self.assertEqual(self.manager.start(self.body)["status"], "unknown")
            self.assertEqual(self.manager.start(self.body)["status"], "unknown")
            request.assert_called_once()

    def test_completed_task_saves_to_original_workspace_after_restart(self):
        with patch.object(api, "api_request", return_value={"task_id": "task-123"}), patch.object(self.manager, "monitor"):
            self.manager.start(self.body)
        manager = api.MiniMaxJobs(lambda: self.config, lambda: self.root / "different", lambda: self.root, self.manager.state_path)
        with patch.object(api, "api_request", return_value={"task": {"status": "succeeded", "content": {"url": "https://example.com/video.mp4"}}}), patch.object(api, "save_video", side_effect=lambda url, path: path.write_bytes(b"video")):
            manager._run(self.body["client_id"])
        job = manager.snapshot(self.body["client_id"])
        self.assertEqual(job["status"], "completed")
        output = self.root / "outputs" / job["output"]
        self.assertTrue(output.exists())
        self.assertEqual(json.loads(output.with_suffix(".meta.json").read_text())["params"]["prompt"], "A sunrise")

    def test_provider_failure_reason_is_preserved(self):
        with patch.object(api, 'api_request', return_value={'task_id': 'task-123'}), patch.object(self.manager, 'monitor'):
            self.manager.start(self.body)
        failure = {'task': {'status': 'failed', 'error': {
            'code': '2013', 'message': 'audio format ".x-wav" not allowed'}}}
        with patch.object(api, 'api_request', return_value=failure):
            self.manager._run(self.body['client_id'])
        job = self.manager.snapshot(self.body['client_id'])
        self.assertEqual(job['status'], 'failed')
        self.assertIn('2013', job['error'])
        self.assertIn('.x-wav', job['error'])

    def test_audio_mime_does_not_depend_on_os(self):
        path = self.root / 'reference.wav'
        path.write_bytes(b'test audio')
        with patch.object(api, 'validate_media', return_value=4), \
             patch.object(api.mimetypes, 'guess_type', return_value=('audio/x-wav', None)):
            payload = api.build_video_payload(self.body | {'references': [
                {'role': 'reference_audio', 'path': str(path)}]}, self.root)
        self.assertTrue(payload['content'][1]['audio_url']['url'].startswith('data:audio/wav;base64,'))

    def test_minimax_payload_omits_local_sampler_and_separates_reasoning(self):
        with patch.object(llm, "_provider", "minimax_subscription"), patch.object(llm, "_model_id", "MiniMax-M3"):
            result = llm._finalize_payload({"messages": [], "max_tokens": 128, "top_k": 50, "stop": ["<think>"], "cache_prompt": True})
            self.assertEqual(result, {"messages": [], "max_tokens": 128, "model": "MiniMax-M3", "reasoning_split": True})
            self.assertEqual(llm._server_url(), "https://api.minimax.io")


if __name__ == "__main__":
    unittest.main()
