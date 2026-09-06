"""Exercise real audio extraction and the encoded API payload, without billing."""
import base64
import io
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from services.director_minimax import requests_for_shots, _shot_audio
from services.minimax_api import build_video_payload


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/FFprobe required')
class DirectorAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.song = self.root / 'song.wav'
        self.pcm = b'\xe8\x03' * (324000 * 2) + b'\xd0\x07' * (492000 * 2)
        with wave.open(str(self.song), 'wb') as f:
            f.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            f.writeframes(self.pcm)
        self.original = self.song.read_bytes()

    def test_excerpts_keep_timing_and_only_pad_rounded_tail(self):
        shots = requests_for_shots('audio-test', {'audio_path': str(self.song)},
            [{'video_prompt': 'Sing'}] * 2,
            [{'start': 2.25, 'duration_sec': 4.5}, {'start': 6.75, 'duration_sec': 5.25}], [], self.root)
        for (body, seconds), start in zip(shots, [2.25, 6.75]):
            payload = build_video_payload(body, self.root)
            item = payload['content'][1]
            self.assertEqual(item['role'], 'reference_audio')
            self.assertTrue(item['audio_url']['url'].startswith('data:audio/wav;base64,'))
            with wave.open(io.BytesIO(base64.b64decode(item['audio_url']['url'].split(',')[1])), 'rb') as f:
                pcm = f.readframes(f.getnframes())
                self.assertEqual(f.getnframes(), body['duration'] * 48000)
            length = round(seconds * 48000) * 4
            begin = round(start * 48000) * 4
            self.assertEqual(pcm[:length], self.pcm[begin:begin + length])
            self.assertEqual(set(pcm[length:]), {0})
            self.assertIn('<Audio 1>', body['prompt'])
        self.assertEqual(self.song.read_bytes(), self.original)
        path = shots[0][0]['references'][0]['path']
        with patch('services.director_minimax.subprocess.run') as process:
            self.assertEqual(_shot_audio(self.song, self.root, 2.25, 4.5, 5), path)
            process.assert_not_called()

    def test_drive_reference_is_replaced_in_place_and_photo_is_preserved(self):
        from PIL import Image
        photo = self.root / 'identity.png'
        Image.new('RGB', (256, 256), 'red').save(photo)
        params = {'minimax_h3_references': [
            {'type': 'image', 'path': str(photo)},
            {'type': 'audio', 'path': str(self.song), 'audio_intent': 'drive'}]}
        body, _ = requests_for_shots('drive-test', params, [{'video_prompt': 'Sing'}],
            [{'start': 6.75, 'duration_sec': 4}], [], self.root)[0]
        self.assertEqual(len(body['references']), 2)
        self.assertEqual(body['references'][0]['path'], str(photo))
        self.assertNotEqual(body['references'][1]['path'], str(self.song))
        self.assertIn('<Audio 1>', body['prompt'])

    def test_short_song_and_external_path_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ends before'):
            _shot_audio(self.song, self.root, 16, 4, 4)
        with self.assertRaisesRegex(ValueError, 'workspace'):
            _shot_audio(self.song, self.root / 'other', 0, 4, 4)

    def test_missing_timestamps_advance_and_gaps_are_rejected(self):
        params = {'audio_path': str(self.song)}
        with patch('services.director_minimax._shot_audio', wraps=_shot_audio) as extract:
            requests_for_shots('fallback-test', params, [{'video_prompt': 'Sing', 'duration_sec': 4}] * 2,
                [], [], self.root)
            self.assertEqual([c.args[2] for c in extract.call_args_list], [0, 4])
        with self.assertRaisesRegex(ValueError, 'consecutive'):
            requests_for_shots('gap-test', params, [{'video_prompt': 'Sing'}] * 2,
                [{'start': 0, 'duration_sec': 4}, {'start': 5, 'duration_sec': 4}], [], self.root)

    def test_final_frame_rounding_stops_at_song_end(self):
        shots = requests_for_shots('tail-test', {'audio_path': str(self.song)},
            [{'video_prompt': 'Sing'}], [{'start': 12, 'duration_sec': 5.15}], [], self.root)
        body, seconds = shots[0]
        self.assertEqual(seconds, 5)
        self.assertEqual(body['duration'], 5)
        self.assertIn('first 5 seconds', body['prompt'])
        self.assertEqual(self.song.read_bytes(), self.original)

    def test_large_overrun_is_not_silently_shortened(self):
        with self.assertRaisesRegex(ValueError, 'soundtrack ends at 17.00s'):
            requests_for_shots('long-tail-test', {'audio_path': str(self.song)},
                [{'video_prompt': 'Sing'}], [{'start': 12, 'duration_sec': 6}], [], self.root)

    def test_other_audio_exceeding_budget_fails_before_submission(self):
        params = {'audio_path': str(self.song), 'minimax_h3_references': [
            {'type': 'audio', 'path': _shot_audio(self.song, self.root, 0, 10, 10)}]}
        with self.assertRaisesRegex(ValueError, 'total at most 15'):
            requests_for_shots('budget-test', params, [{'video_prompt': 'Sing', 'duration_sec': 6}],
                [], [], self.root)
