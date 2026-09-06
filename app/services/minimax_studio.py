"""Translate the shared Studio input contract to official MiniMax requests."""
import math
import time

from services.minimax_api import build_video_payload


def studio_request(body, uploads, ident):
    if body.get("generation_mode", "video") != "video" or body.get("image_mode", 0) not in (0, None):
        raise ValueError("The MiniMax API supports creating video from text, frames and references. This editing mode requires local generation.")
    if body.get("activated_loras"):
        raise ValueError("The official MiniMax API cannot use LoRAs. Disable the video LoRAs or select Local generation.")
    seconds = float(body.get("_api_duration_seconds") or body.get("_duration_seconds") or body.get("duration_seconds") or float(body.get("video_length", 120)) / 24)
    if not math.isfinite(seconds) or not 4 <= seconds <= 15:
        raise ValueError("Each MiniMax API video must be 4–15 seconds. Adjust Duration before submitting.")
    refs = []
    for key, role in (("image_start", "first_frame"), ("image_end", "last_frame")):
        paths = body.get(key) or []
        if isinstance(paths, str):
            paths = [paths]
        if len(paths) > 1:
            raise ValueError("MiniMax accepts one first and one last frame per shot.")
        refs.extend({"role": role, "path": path} for path in paths)
    if body.get("image_refs") and body.get("frames_positions"):
        raise ValueError("The MiniMax API accepts first/last frames, but not intermediate keyframe positions.")
    for ref in body.get("minimax_h3_references") or []:
        refs.append({"role": "reference_" + ref["type"], "path": ref["path"]})
    if body.get("audio_guide"):
        refs.append({"role": "reference_audio", "path": body["audio_guide"]})
    resolution = str(body.get("resolution") or "1280x768")
    ratio = "16:9"
    if "x" in resolution:
        width, height = (int(value) for value in resolution.split("x"))
        ratios = {"21:9": 21/9, "16:9": 16/9, "4:3": 4/3, "1:1": 1, "3:4": 3/4, "9:16": 9/16}
        ratio = min(ratios, key=lambda r: abs(ratios[r] - width / height))
        resolution = "2K" if max(width, height) >= 2000 else "768P"
    if resolution not in {"768P", "2K"}:
        resolution = "768P"
    payload = {"client_id": ident, "model": "MiniMax-H3", "prompt": body.get("prompt"),
               "duration": math.ceil(seconds), "resolution": resolution, "ratio": ratio, "references": refs}
    build_video_payload(payload, uploads)
    return payload


def run_studio_job(job, manager, start, update, finish):
    if not start(job, message="Submitting to MiniMax API…"):
        return False
    try:
        payload = studio_request(job["params"], manager.uploads(), "studio-" + job["id"] + "-generation")
        cloud = manager.start(payload, output_dir=job["out_dir"])
        while cloud["status"] not in {"completed", "failed", "unknown", "paused", "cancelled"}:
            if job["status"] == "cancelled":
                return False
            update(job, message=f"MiniMax API: {cloud['status']} — task {cloud.get('task_id') or 'submitting'}",
                   phase="Generating via API", progress=10)
            time.sleep(2)
            cloud = manager.snapshot(cloud["id"])
        if cloud["status"] != "completed":
            raise RuntimeError(cloud.get("error") or "MiniMax task did not complete.")
        finish(job, "completed", output_files=[cloud["output"]], progress=100, message="Done", phase="")
        return True
    except Exception as exc:
        finish(job, "failed", error=str(exc), message=str(exc))
        return False
