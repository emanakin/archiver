"""Bounded local inspection and preview operations for Reel originals."""

import hashlib
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_OID, uuid5

from .execution_errors import OperationBlocked

MAX_VIDEO_BYTES = 32 * 1024 * 1024
MAX_VIDEO_SECONDS = 90


def reel_session_id(actor, upload_id):
    return str(uuid5(NAMESPACE_OID, f"archive-reel:{actor}:{upload_id}"))


def run_video_tool(args, timeout=25):
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        raise OperationBlocked("The video could not be inspected or decoded") from None


def probe_video(path):
    payload = run_video_tool(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-show_entries",
            "format=duration,format_name:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        timeout=10,
    )
    try:
        result = json.loads(payload)
        streams = result["streams"]
        video = [s for s in streams if s.get("codec_type") == "video"]
        audio = [s for s in streams if s.get("codec_type") == "audio"]
        duration = float(result["format"]["duration"])
        width, height = video[0]["width"], video[0]["height"]
        valid = (
            len(video) == 1
            and len(audio) <= 1
            and len(streams) == len(video) + len(audio)
            and video[0]["codec_name"] == "h264"
            and all(s["codec_name"] == "aac" for s in audio)
            and "mp4" in result["format"]["format_name"].split(",")
            and 3 <= duration <= MAX_VIDEO_SECONDS
            and 240 <= width <= 1920
            and 240 <= height <= 1920
        )
    except (ValueError, TypeError, IndexError, KeyError):
        valid = False
    if not valid:
        raise OperationBlocked(
            "Use an H.264 MP4 with optional AAC audio, 3–90 seconds, up to 1920 pixels per side"
        )
    return {"width": width, "height": height, "duration": duration, "has_audio": bool(audio)}


def validate_video(data):
    if not 8 <= len(data) <= MAX_VIDEO_BYTES or data[4:8] != b"ftyp":
        raise OperationBlocked("Use an MP4 video no larger than 32 MiB")
    with TemporaryDirectory(prefix="archive-video-") as directory:
        path = Path(directory) / "original.mp4"
        path.write_bytes(data)
        info = probe_video(path)
        video_frame(path, info["duration"] * 0.2)
    return {
        "mime": "video/mp4",
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_size": len(data),
        "width": info["width"],
        "height": info["height"],
    }


def video_frame(path, seconds):
    frame = run_video_tool(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            "scale=960:960:force_original_aspect_ratio=decrease",
            "-threads",
            "1",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "pipe:1",
        ]
    )
    if not frame:
        raise OperationBlocked("The video did not contain a readable frame")
    return frame
