"""Official MiniMax H3 API integration. Credentials stay server-side."""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from urllib.parse import urlparse, urljoin

import requests
from fastapi import APIRouter, HTTPException

API_ROOT = "https://api.minimax.io"
TERMINAL = {"completed", "failed", "cancelled", "unknown", "paused"}


def validate_media(path, kind):
    """Return clip duration after checking the provider's media limits."""
    if kind == "image":
        from PIL import Image
        try:
            with Image.open(path) as image:
                width, height = image.size
                image.verify()
        except Exception:
            raise ValueError("Cannot read this image. Try exporting it as PNG or JPEG.") from None
        duration = 0
    else:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                capture_output=True, timeout=20, check=True,
            )
            info = json.loads(result.stdout)
            duration = float(info["format"]["duration"])
            stream = next(s for s in info["streams"] if s.get("codec_type") == kind)
        except FileNotFoundError:
            raise ValueError("FFprobe is not installed; it is required to inspect reference clips.") from None
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
            raise ValueError(f"Cannot read the {kind} reference {Path(path).name}. Try exporting it again.") from None
        if not 2 <= duration <= 15:
            raise ValueError(f"Reference {Path(path).name} is {duration:.2f} seconds; the API requires 2–15 seconds.")
        if kind == "audio":
            return duration
        width, height = stream.get("width", 0), stream.get("height", 0)
        try:
            numerator, denominator = stream.get("avg_frame_rate", "0/1").split("/")
            fps = float(numerator) / float(denominator)
        except (ValueError, ZeroDivisionError):
            fps = 0
        if stream.get("codec_name") not in {"h264", "hevc"} or not 23.976 <= fps <= 60:
            raise ValueError("Reference video must use H.264/H.265 at 23.976–60 fps.")
        if any(s.get("codec_type") == "audio" and s.get("codec_name") not in {"aac", "mp3"} for s in info["streams"]):
            raise ValueError("Reference video audio must use AAC or MP3.")
    if not (256 <= width <= 5760 and 256 <= height <= 5760 and 0.4 <= width / height <= 2.5):
        raise ValueError("Reference dimensions must be 256–5760 pixels, with aspect ratio 0.4–2.5.")
    return duration


def payg_key(config):
    key = str(config.get("minimax_api_key") or "").strip()
    if not key:
        raise ValueError("Add a MiniMax pay-as-you-go API key in Settings → Services.")
    if key.startswith("sk-cp-"):
        raise ValueError("H3 requires a pay-as-you-go key, not a Subscription / Token Plan key.")
    return key


def build_video_payload(body, uploads_root):
    """Validate the documented H3 input contract before submitting a paid task."""
    model = body.get("model", "MiniMax-H3")
    if model not in {"MiniMax-H3", "MiniMax-H3-Max"}:
        raise ValueError("Unsupported MiniMax video model.")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt or len(prompt) > 20000:
        raise ValueError("Enter a prompt of 1–20,000 characters.")
    duration = body.get("duration", 5)
    if type(duration) is not int or not (5 if model.endswith("Max") else 4) <= duration <= 15:
        raise ValueError("H3 supports 4–15 seconds; H3 Max supports 5–15 seconds.")
    resolution = body.get("resolution", "768P")
    if resolution not in ({"480P", "768P"} if model.endswith("Max") else {"768P", "2K"}):
        raise ValueError("Unsupported resolution for the selected H3 model.")
    ratio = body.get("ratio", "16:9")
    if ratio not in {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
        raise ValueError("Unsupported aspect ratio.")
    if body.get("loras") or body.get("activated_loras"):
        raise ValueError("The official H3 API does not accept local LoRAs.")
    content = [{"type": "text", "text": prompt}]
    refs = body.get("references") or []
    if not isinstance(refs, list) or len(refs) > 15:
        raise ValueError("Invalid reference list.")
    counts = {}
    durations = {"image": 0, "video": 0, "audio": 0}
    encoded_size = len(prompt.encode()) + 4096
    limits = {"first_frame": ("image", 1, 30), "last_frame": ("image", 1, 30),
              "reference_image": ("image", 9, 30), "reference_video": ("video", 3, 50),
              "reference_audio": ("audio", 3, 15)}
    root = Path(uploads_root).resolve()
    extensions = {"image": {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"},
                  "video": {".mp4", ".mov"}, "audio": {".wav", ".mp3"}}
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("role") not in limits:
            raise ValueError("Invalid reference role.")
        role = ref["role"]
        kind, count, max_mb = limits[role]
        counts[role] = counts.get(role, 0) + 1
        if counts[role] > count:
            raise ValueError(f"Too many {role} references (maximum {count}).")
        path = Path(str(ref.get("path") or "")).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Reference must be a file uploaded through Maestro.")
        if path.suffix.lower() not in extensions[kind] or path.stat().st_size > max_mb * 1024 * 1024:
            raise ValueError(f"Unsupported {kind} file or file exceeds {max_mb} MB.")
        encoded_size += 4 * ((path.stat().st_size + 2) // 3) + 512
        if encoded_size > 64 * 1024 * 1024:
            raise ValueError("Combined reference uploads exceed the API's 64 MB request limit. Use smaller files.")
        durations[kind] += validate_media(path, kind)
        if durations[kind] > 15:
            raise ValueError(f"Reference {kind} clips must total at most 15 seconds.")
        # OS MIME databases often return audio/x-wav, which MiniMax treats
        # as the unsupported extension .x-wav. Use the canonical API labels.
        mime = {".wav": "audio/wav", ".mp3": "audio/mpeg"}.get(path.suffix.lower())
        mime = mime or mimetypes.guess_type(path.name)[0] or f"{kind}/octet-stream"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": f"{kind}_url", f"{kind}_url": {"url": f"data:{mime};base64,{data}"}, "role": role})
    if any(role.startswith("reference_") for role in counts):
        if model.endswith("Max"):
            raise ValueError("H3 Max does not support reference-to-video.")
        if "first_frame" in counts or "last_frame" in counts:
            raise ValueError("First/last frames cannot be combined with reference-to-video inputs.")
    payload = {"model": model, "content": content, "duration": duration, "resolution": resolution, "ratio": ratio}
    if len(json.dumps(payload).encode()) > 64 * 1024 * 1024:
        raise ValueError("Combined reference uploads exceed the API's 64 MB request limit. Use smaller files.")
    return payload


def api_request(method, path, key, payload=None):
    try:
        with requests.request(method, API_ROOT + path, headers={"Authorization": f"Bearer {key}"},
                              json=payload, timeout=(15, 90), allow_redirects=False) as response:
            if not 200 <= response.status_code < 300:
                if response.status_code >= 500:
                    raise RuntimeError("MiniMax server error. Submission status is uncertain; check the provider console before submitting again.")
                raise ValueError(f"MiniMax returned HTTP {response.status_code}. Check your key, balance, quota, and request in the MiniMax console.")
            data = response.json()
            if not isinstance(data, dict):
                raise RuntimeError("Unexpected MiniMax response. Check the provider console before submitting again.")
            if data.get("error"):
                raise ValueError("MiniMax rejected the request. Check the task in your MiniMax console.")
            return data
    except requests.RequestException:
        raise RuntimeError("MiniMax connection interrupted. A submitted task may still exist; check the console before submitting again.") from None


def _public_download_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("MiniMax returned an invalid result URL.")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("MiniMax returned a non-public result URL.")
    return url


def save_video(url, path):
    """Fetch the provider result without forwarding API credentials to the CDN."""
    partial = path.with_suffix(".part")
    response = None
    try:
        for _ in range(6):
            response = requests.get(_public_download_url(url), stream=True, timeout=(15, 60), allow_redirects=False)
            if response.is_redirect:
                url = urljoin(url, response.headers.get("Location", ""))
                response.close()
                continue
            break
        else:
            raise ValueError("Too many result-download redirects.")
        if response.status_code != 200:
            raise ValueError(f"Result download returned HTTP {response.status_code}.")
        size = 0
        prefix = bytearray()
        with partial.open("wb") as handle:
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > 4 * 1024 ** 3:
                    raise ValueError("Result exceeds the 4 GB download limit.")
                if len(prefix) < 32:
                    prefix.extend(chunk[:32 - len(prefix)])
                handle.write(chunk)
        expected = response.headers.get("Content-Length")
        if not size or (expected and int(expected) != size) or b"ftyp" not in prefix:
            raise ValueError("MiniMax result is incomplete or is not an MP4 video.")
        os.replace(partial, path)
    except requests.RequestException:
        raise RuntimeError("Result download interrupted. Resume this task to retrieve the video without generating again.") from None
    finally:
        if response is not None:
            response.close()
        partial.unlink(missing_ok=True)


class MiniMaxJobs:
    def __init__(self, config, workspace, uploads, state_path):
        self.config, self.workspace, self.uploads = config, workspace, uploads
        self.state_path = Path(state_path)
        self.lock = threading.RLock()
        self.running = set()
        try:
            self.jobs = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            self.jobs = {}
        for job in self.jobs.values():
            if job["status"] == "submitting" and not job.get("task_id"):
                job.update(status="unknown", error="Submission was interrupted. Check MiniMax's task list before submitting again.")

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.jobs, indent=2))
        temporary.replace(self.state_path)

    def _update(self, ident, **changes):
        with self.lock:
            self.jobs[ident].update(changes, updated_at=time.time())
            self._save()

    def snapshot(self, ident):
        with self.lock:
            return {k: v for k, v in self.jobs[ident].items() if not k.startswith("_")}

    def start(self, body, *, output_dir=None):
        key = payg_key(self.config())
        payload = build_video_payload(body, self.uploads())
        ident = str(body.get("client_id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", ident):
            raise ValueError("A valid client submission ID is required.")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if ident in self.jobs:
                if self.jobs[ident]["_digest"] != digest:
                    raise ValueError("Submission ID already belongs to a different request.")
                return self.snapshot(ident)
            output = Path(output_dir or self.workspace()).resolve()
            output.mkdir(parents=True, exist_ok=True)
            self.jobs[ident] = {"id": ident, "task_id": None, "status": "submitting", "error": None,
                "model": payload["model"], "prompt": body["prompt"], "duration": payload["duration"],
                "resolution": payload["resolution"], "ratio": payload["ratio"], "created_at": time.time(),
                "output": None, "_output_dir": str(output), "_digest": digest}
            self._save()
        # Deliberately never retry a POST: the provider may have accepted it.
        try:
            data = api_request("POST", "/v2/video_generation", key, payload)
            task = str(data.get("task_id") or "")
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", task):
                raise RuntimeError("No task ID returned; check MiniMax before submitting again.")
            self._update(ident, task_id=task, status="queued")
            self.monitor(ident)
        except Exception as exc:
            self._update(ident, status="unknown" if isinstance(exc, RuntimeError) else "failed", error=str(exc))
        return self.snapshot(ident)

    def monitor(self, ident):
        with self.lock:
            if ident in self.running:
                return
            self.running.add(ident)
        threading.Thread(target=self._run, args=(ident,), daemon=True).start()

    def _run(self, ident):
        try:
            job = self.jobs[ident]
            key = payg_key(self.config())
            for _ in range(1440):
                if job["status"] in {"cancelled", "completed"}:
                    return
                try:
                    task = api_request("GET", "/v2/query/video_generation/" + job["task_id"], key).get("task", {})
                except RuntimeError:
                    time.sleep(5)
                    continue
                status = task.get("status", "queued")
                if status == "succeeded":
                    self._update(ident, status="saving", error=None)
                    filename = f"minimax_h3_{ident}.mp4"
                    path = Path(job["_output_dir"]) / filename
                    if not path.exists():
                        save_video((task.get("content") or {}).get("url", ""), path)
                    meta = {"generation_mode": "video", "model_type": "minimax_h3_api",
                            "params": {"prompt": job["prompt"], "model_type": "minimax_h3_api", "duration_seconds": job["duration"], "resolution": job["resolution"]},
                            "provider": "minimax", "task_id": job["task_id"]}
                    path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
                    self._update(ident, status="completed", output=filename, error=None)
                    return
                if status in {"failed", "cancelled"}:
                    detail = task.get("error") or {}
                    if isinstance(detail, dict):
                        message = str(detail.get("message") or "No failure reason supplied.")[:1000]
                        code = str(detail.get("code") or "unknown")[:80]
                        error = f"MiniMax error {code}: {message}"
                    else:
                        error = f"MiniMax task failed: {str(detail)[:1000]}"
                    self._update(ident, status=status, error=error if status == "failed" else None)
                    return
                self._update(ident, status="running" if status in {"running", "processing"} else "queued", error=None)
                time.sleep(5)
            self._update(ident, status="paused", error="Monitoring paused after two hours. Resume to check the existing task.")
        except Exception as exc:
            self._update(ident, status="paused", error=str(exc))
        finally:
            with self.lock:
                self.running.discard(ident)

    def resume_all(self):
        for ident, job in list(self.jobs.items()):
            if job.get("task_id") and job["status"] not in TERMINAL:
                self.monitor(ident)


def create_router(manager):
    router = APIRouter(prefix="/api/v1/minimax")

    @router.get("/jobs")
    def jobs():
        with manager.lock:
            return {"jobs": [manager.snapshot(k) for k in reversed(manager.jobs)]}

    @router.post("/video")
    def generate(body: dict):
        try:
            return manager.start(body)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post("/jobs/{ident}/resume")
    def resume(ident: str):
        if ident not in manager.jobs or not manager.jobs[ident].get("task_id"):
            raise HTTPException(404, "No existing MiniMax task to resume.")
        if manager.jobs[ident]["status"] not in {"paused", "queued", "running", "saving"}:
            raise HTTPException(409, "This task cannot be resumed.")
        manager._update(ident, status="queued", error=None)
        manager.monitor(ident)
        return manager.snapshot(ident)

    return router
