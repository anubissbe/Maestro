"""Network-free checks for exact, bounded CivitAI stream resumption."""
import importlib.util
import json
import struct
import sys
from pathlib import Path
import unittest

import requests

spec = importlib.util.spec_from_file_location("resumable_download", Path(__file__).resolve().parents[1] / "app/services/resumable_download.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Response:
    def __init__(self, chunks=(), *, status=200, headers=None):
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed = True


class TestResumableDownload(unittest.TestCase):
    def download(self, responses, **kwargs):
        calls = []
        def get(url, **options):
            calls.append((url, options))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return module.ResumableDownload("https://civitai.com/api/download/models/123", {}, get=get, sleep=lambda _: None, **kwargs), calls

    def test_timeout_resumes_exact_bytes_and_refreshes_original_url(self):
        first = Response([b"abc", requests.exceptions.ConnectionError("timeout")], headers={"Content-Length": "6", "ETag": '"v1"'})
        second = Response([b"def"], status=206, headers={"Content-Range": "bytes 3-5/6", "ETag": '"v1"'})
        download, calls = self.download([first, second])
        try:
            self.assertEqual(b"".join(download.iter_content()), b"abcdef")
            self.assertTrue(first.closed)
            self.assertEqual(calls[1][0], calls[0][0])
            self.assertEqual(calls[1][1]["headers"]["Range"], "bytes=3-")
            self.assertEqual(calls[1][1]["headers"]["If-Range"], '"v1"')
            self.assertEqual(calls[0][1]["headers"]["Accept-Encoding"], "identity")
        finally:
            download.close()
        self.assertTrue(second.closed)

    def test_incomplete_clean_eof_also_resumes(self):
        first = Response([b"abc"], headers={"Content-Length": "6", "Last-Modified": "date"})
        second = Response([b"def"], status=206, headers={"Content-Range": "bytes 3-5/6", "Last-Modified": "date"})
        download, _ = self.download([first, second])
        self.assertEqual(b"".join(download.iter_content()), b"abcdef")
        download.close()

    def test_invalid_resume_is_rejected_without_appending(self):
        for status, extra in ((200, {}), (206, {"ETag": '"changed"'}), (206, {"Content-Range": "bytes 0-5/6"}), (206, {"Content-Range": "bytes 3-8/9"})):
            with self.subTest(status=status, extra=extra):
                first = Response([b"abc", requests.exceptions.ReadTimeout()], headers={"Content-Length": "6", "ETag": '"v1"'})
                second = Response([b"WRONG"], status=status, headers={"Content-Range": "bytes 3-5/6", "ETag": '"v1"', **extra})
                download, _ = self.download([first, second])
                stream = download.iter_content()
                self.assertEqual(next(stream), b"abc")
                with self.assertRaisesRegex(RuntimeError, "safely resume"):
                    next(stream)
                self.assertTrue(second.closed)

    def test_retries_are_bounded_and_errors_do_not_expose_url(self):
        with self.assertRaisesRegex(RuntimeError, "2 automatic retries") as caught:
            self.download([requests.exceptions.ReadTimeout("SECRET")] * 3, max_retries=2)
        self.assertNotIn("SECRET", str(caught.exception))

    def test_transient_status_retries_but_auth_failure_does_not(self):
        unavailable = Response(status=503)
        download, calls = self.download([unavailable, Response([b"ok"], headers={"Content-Length": "2"})])
        self.assertEqual(b"".join(download.iter_content()), b"ok")
        self.assertEqual(len(calls), 2)
        self.assertTrue(unavailable.closed)
        download.close()
        with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
            self.download([Response(status=403)])

    def test_missing_validator_and_weak_etag_do_not_mix_files(self):
        for headers in ({"Content-Length": "6"}, {"Content-Length": "6", "ETag": 'W/"v1"'}):
            download, calls = self.download([Response([b"abc", requests.exceptions.ReadTimeout()], headers=headers)])
            with self.assertRaisesRegex(RuntimeError, "safe resumption"):
                list(download.iter_content())
            self.assertEqual(len(calls), 1)

    def test_failure_before_first_byte_can_retry_without_range(self):
        first = Response([requests.exceptions.ReadTimeout()])
        second = Response([b"ok"], headers={"Content-Length": "2"})
        download, calls = self.download([first, second])
        self.assertEqual(b"".join(download.iter_content()), b"ok")
        self.assertNotIn("Range", calls[1][1]["headers"])
        download.close()

    def test_header_preflight_survives_interruption_mid_header(self):
        compatibility_spec = importlib.util.spec_from_file_location("checkpoint_compatibility", Path(__file__).resolve().parents[1] / "app/services/checkpoint_compatibility.py")
        compatibility = importlib.util.module_from_spec(compatibility_spec)
        sys.modules[compatibility_spec.name] = compatibility
        compatibility_spec.loader.exec_module(compatibility)
        header = json.dumps({key: {"shape": shape, "dtype": "F16", "data_offsets": [0, 0]} for key, shape in {
            "img_in.weight": [3072, 64], "txt_in.weight": [3072, 4096], "double_blocks.0.img_attn.qkv.weight": [9216, 3072],
        }.items()}).encode()
        data = struct.pack("<Q", len(header)) + header + b"payload"
        split = 25
        first = Response([data[:split], requests.exceptions.ReadTimeout()], headers={"Content-Length": str(len(data)), "ETag": '"v1"'})
        second = Response([data[split:]], status=206, headers={"Content-Range": f"bytes {split}-{len(data)-1}/{len(data)}", "ETag": '"v1"'})
        download, _ = self.download([first, second])
        try:
            checked = compatibility.verified_checkpoint_chunks(download.iter_content(), "Flux.1 D", "flux", filename="model.safetensors")
            self.assertEqual(b"".join(checked), data)
        finally:
            download.close()

    def test_encoding_and_oversized_response_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "compressed"):
            self.download([Response(headers={"Content-Encoding": "gzip"})])
        download, _ = self.download([Response([b"too long"], headers={"Content-Length": "2"})])
        try:
            with self.assertRaisesRegex(RuntimeError, "more bytes"):
                list(download.iter_content())
        finally:
            download.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
