"""Render Director's reviewed shots through the official MiniMax API."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import time
import tempfile
import wave

from services.minimax_api import MiniMaxJobs, build_video_payload


def _soundtrack_path(params):
    drives = [r.get("path") for r in params.get("minimax_h3_references") or []
              if r.get("type") == "audio" and r.get("audio_intent") == "drive"]
    if len(drives) > 1:
        raise ValueError("Director accepts one music / performance timeline.")
    return (drives[0] if drives else None) or params.get("audio_path")


def _soundtrack_duration(source, root):
    source = Path(source).resolve()
    if not source.is_relative_to(Path(root).resolve()) or not source.is_file():
        raise ValueError("Director soundtrack must be a file saved in this workspace.")
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                 "-of", "json", str(source)], check=True, capture_output=True, timeout=30)
        duration = float(json.loads(result.stdout)["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
        return duration
    except FileNotFoundError:
        raise ValueError("FFprobe is required to inspect the Director soundtrack.") from None
    except (subprocess.SubprocessError, ValueError, KeyError):
        raise ValueError("Cannot read the Director soundtrack duration.") from None


def _shot_audio(source, root, start, seconds, api_seconds):
    """Cache an accurate PCM excerpt; pad only the API's rounded-up tail."""
    source = Path(source).resolve()
    root = Path(root).resolve()
    if not source.is_relative_to(root) or not source.is_file():
        raise ValueError("Director soundtrack must be a file saved in this workspace.")
    stat = source.stat()
    signature = json.dumps([str(source), stat.st_size, stat.st_mtime_ns,
                            start, seconds, api_seconds, "pcm48k-v1"])
    digest = hashlib.sha256(signature.encode()).hexdigest()[:24]
    cache = root / "_director_assets" / "minimax_audio_cache"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (digest + ".wav")
    if target.is_file():
        return str(target)
    with tempfile.NamedTemporaryFile(dir=cache, suffix=".wav", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source),
                        "-ss", str(start), "-t", str(seconds), "-vn",
                        "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", str(temporary)],
                       check=True, capture_output=True, timeout=120)
        with wave.open(str(temporary), "rb") as audio:
            frames = audio.getnframes()
            pcm = audio.readframes(frames)
        expected = round(seconds * 48000)
        if frames < expected - 2400:
            raise ValueError("The soundtrack ends before this Director shot. Shorten or replan the timeline.")
        # Keep the source timing, then fill the unused rounded-up API duration
        # with silence. Director trims this tail off the downloaded video.
        pcm = pcm[:expected * 4]
        pcm += b"\0" * max(0, api_seconds * 48000 * 4 - len(pcm))
        with wave.open(str(temporary), "wb") as audio:
            audio.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            audio.writeframes(pcm)
        temporary.replace(target)
    except FileNotFoundError:
        raise ValueError("FFmpeg is required to prepare the Director soundtrack.") from None
    except (subprocess.SubprocessError, wave.Error):
        raise ValueError("Cannot extract audio from the Director soundtrack. Check the uploaded file.") from None
    finally:
        temporary.unlink(missing_ok=True)
    return str(target)


def requests_for_shots(pid, params, plans, timeline, images, root):
    root = Path(root).resolve()
    soundtrack = _soundtrack_path(params)
    soundtrack = str(Path(soundtrack).resolve()) if soundtrack else None
    song_duration = _soundtrack_duration(soundtrack, root) if soundtrack else None
    references = []
    # Keep the editor's numbering: legacy and generated images must never
    # displace an explicitly selected <Picture N> reference.
    for ref in params.get("minimax_h3_references") or []:
        references.append({"role": "reference_" + ref["type"],
                           "path": str(Path(ref["path"]).resolve())})
    paths = ([params.get("reference_image_path")] + list(params.get("character_ref_paths") or [])
             + list(params.get("location_ref_paths") or []))
    for path in dict.fromkeys(p for p in paths if p):
        item = {"role": "reference_image", "path": str(Path(path).resolve())}
        if item not in references:
            references.append(item)
    ratio = str(params.get("director_aspect_ratio") or "16:9")
    result = []
    next_start = 0.0
    for i, plan in enumerate(plans):
        timing = timeline[i] if i < len(timeline) else {}
        seconds = float(timing.get("duration_sec") or (timing.get("end", 0) - timing.get("start", 0)) or plan.get("duration_sec") or 5)
        if not math.isfinite(seconds) or not 0 < seconds <= 15:
            raise ValueError(f"Director shot {i + 1} exceeds the API's 15-second limit. Split or replan this shot before rendering.")
        start = float(timing.get("start", next_start))
        if not math.isfinite(start) or start < 0:
            raise ValueError("Director shot start time must be a finite, non-negative number.")
        if soundtrack and i and abs(start - next_start) > 0.05:
            raise ValueError("Director soundtrack requires consecutive shots. Replan gaps or overlaps before rendering.")
        if song_duration is not None and start + seconds > song_duration:
            overshoot = start + seconds - song_duration
            # Native H3 timelines round to a 17-frame grid (about 0.71s).
            # Only shorten the final scene, never hide missing music mid-plan.
            if i == len(plans) - 1 and 0 < overshoot <= 0.75 and start < song_duration:
                seconds = song_duration - start
            else:
                raise ValueError(f"Director shot {i + 1} ends at {start + seconds:.2f}s, "
                                 f"but the soundtrack ends at {song_duration:.2f}s. Replan the timeline.")
        next_start = start + seconds
        api_seconds = max(4, math.ceil(seconds))
        shot_refs = list(references)
        prompt = str(plan.get("video_prompt") or "").strip()
        if not prompt:
            raise ValueError(f"Director shot {i + 1} needs a video prompt before rendering.")
        if soundtrack:
            excerpt = _shot_audio(soundtrack, root, start, seconds, api_seconds)
            # Replace a full-song Omni entry in place so <Audio N> stays stable.
            slot = next((j for j, ref in enumerate(shot_refs)
                         if ref["role"] == "reference_audio" and ref["path"] == soundtrack), None)
            song_ref = {"role": "reference_audio", "path": excerpt}
            if slot is None:
                slot = len(shot_refs)
                shot_refs.append(song_ref)
            else:
                shot_refs[slot] = song_ref
            ordinal = sum(r["role"] == "reference_audio" for r in shot_refs[:slot + 1])
            prompt += (f"\nPERFORMANCE TIMING: <Audio {ordinal}> is the exact soundtrack excerpt "
                       f"for this shot, starting at video time zero. Follow its rhythm and vocal timing "
                       f"for the first {seconds:g} seconds. When a subject sings or speaks, align their "
                       "mouth movements with this audio. Preserve the supplied performance; "
                       "do not replace it with new lyrics, speech, or music.")
        if i < len(images) and images[i]:
            path = str((root / images[i]).resolve())
            if not any(r["path"] == path for r in shot_refs):
                shot_refs.append({"role": "reference_image", "path": path})
        body = {"client_id": f"director-{pid}-shot-{i + 1:04d}", "model": "MiniMax-H3",
                "prompt": prompt, "duration": api_seconds,
                "resolution": "768P", "ratio": ratio, "references": shot_refs}
        # Check all shots before submitting the first paid generation.
        build_video_payload(body, root)
        result.append((body, seconds))
    return result


def render_clips(pid, params, plans, timeline, images, out_dir, wgp,
                 update, save, cancelled, outputs_type, *, join=True):
    root = Path(out_dir).resolve()
    requests = requests_for_shots(pid, params, plans, timeline, images, root)
    manager = MiniMaxJobs(lambda: wgp.server_config.get("services", {}), lambda: root,
                         lambda: root, root / f"director_{pid}_minimax_jobs.json")
    manager.resume_all()
    outputs = outputs_type([], {})
    try:
        for index, (body, seconds) in enumerate(requests):
            if cancelled():
                return outputs
            job = manager.start(body)
            if job["status"] == "paused" and job.get("task_id"):
                manager._update(job["id"], status="queued", error=None)
                manager.monitor(job["id"])
                job = manager.snapshot(job["id"])
            while job["status"] not in {"completed", "failed", "unknown", "paused", "cancelled"}:
                if cancelled():
                    return outputs
                update(pid, progress={"current": index, "total": len(requests), "step": 0, "total_steps": 0,
                    "message": f"MiniMax API shot {index + 1}/{len(requests)}: {job['status']} (task {job.get('task_id') or 'submitting'})"})
                time.sleep(2)
                job = manager.snapshot(job["id"])
            if job["status"] != "completed":
                raise RuntimeError(f"MiniMax shot {index + 1}: {job.get('error') or job['status']}. Task {job.get('task_id') or 'unknown'}; check MiniMax before making a new submission.")
            source = root / job["output"]
            filename = f"director_{pid}_api_clip_{index + 1:04d}.mp4"
            target = root / filename
            if not target.exists():
                partial = target.with_suffix(".part.mp4")
                try:
                    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-t", str(seconds),
                                    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", str(partial)],
                                   check=True, timeout=600, capture_output=True)
                    partial.replace(target)
                except (OSError, subprocess.SubprocessError):
                    raise RuntimeError("Could not trim the downloaded shot to Director's timeline. The API result is saved; no regeneration is needed.") from None
                finally:
                    partial.unlink(missing_ok=True)
            target.with_suffix(".meta.json").write_text(json.dumps({"generation_mode": "video", "provider": "minimax",
                "task_id": job["task_id"], "params": {"prompt": body["prompt"], "model_type": "minimax_h3_api"}}))
            outputs.append(filename)
            outputs.clip_output_files[index] = filename
            update(pid, output_files=list(outputs), _clip_video_files=list(outputs))
            save(pid)
        if join and outputs and (len(outputs) > 1 or _soundtrack_path(params)) and not cancelled():
            filename = f"director_{pid}_api_multiclip.mp4"
            offset = float((timeline[0] if timeline else {}).get("start") or 0)
            ok = wgp.concatenate_multi_clip_videos([str(root / name) for name in outputs],
                    str(root / filename), _soundtrack_path(params) or None, audio_start_sec=offset)
            if not ok or not (root / filename).is_file():
                raise RuntimeError("API shots are saved, but joining failed. Use Director Rejoin to retry without generating again.")
            outputs.append(filename)
        return outputs
    except Exception:
        update(pid, output_files=list(outputs), _clip_video_files=list(outputs))
        save(pid)
        raise
