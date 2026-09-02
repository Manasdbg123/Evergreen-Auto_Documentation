"""Low-level video and image primitives shared by the frame stages."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cv2
import imagehash
import numpy as np
from PIL import Image

from ..config import Config, resolve_ffmpeg


def probe_duration(video_path: Path, cfg: Config) -> float:
    """Duration in seconds.

    OpenCV first (no subprocess, and it agrees with the frames we actually
    read). Falls back to parsing ffmpeg's stderr, since ffprobe is not
    guaranteed to be present when ffmpeg comes from the pip wheel.
    """
    cap = cv2.VideoCapture(str(video_path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        if fps > 0 and count > 0:
            return float(count / fps)
    finally:
        cap.release()

    proc = subprocess.run(
        [resolve_ffmpeg(cfg), "-i", str(video_path)],
        capture_output=True, text=True,
    )
    for line in proc.stderr.splitlines():
        if "Duration:" in line:
            stamp = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = stamp.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def probe_video(video_path: Path, cfg: Config) -> dict:
    cap = cv2.VideoCapture(str(video_path))
    try:
        info = {
            "fps": round(cap.get(cv2.CAP_PROP_FPS) or 0.0, 3),
            "frame_count": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
        }
    finally:
        cap.release()
    info["duration"] = probe_duration(video_path, cfg)
    return info


def has_audio_stream(video_path: Path, cfg: Config) -> bool:
    """The no-audio path is the default path, so this must never throw."""
    proc = subprocess.run(
        [resolve_ffmpeg(cfg), "-i", str(video_path)],
        capture_output=True, text=True,
    )
    return "Audio:" in proc.stderr


def extract_audio(video_path: Path, out_path: Path, cfg: Config) -> bool:
    """16kHz mono wav, which is what whisper wants. False if there is no audio."""
    if not has_audio_stream(video_path, cfg):
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            resolve_ffmpeg(cfg), "-y", "-i", str(video_path),
            "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", str(out_path),
        ],
        capture_output=True, text=True,
    )
    return proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 44


# --------------------------------------------------------------------------
# Image comparison
# --------------------------------------------------------------------------


def to_analysis_gray(frame_bgr: np.ndarray, cfg: Config) -> np.ndarray:
    """Downscale + grayscale + crop the ignored bands, once, for comparison.

    Cropping matters more than it looks: an OS clock ticking over in a taskbar
    will otherwise register as a change event every single minute.
    """
    cd = cfg.change_detection
    h, w = frame_bgr.shape[:2]
    top = int(h * cd.ignore_top_pct)
    bottom = h - int(h * cd.ignore_bottom_pct)
    if bottom - top < 16:
        top, bottom = 0, h
    cropped = frame_bgr[top:bottom, :]

    target_w = cfg.frames.analysis_width
    scale = target_w / max(cropped.shape[1], 1)
    target_h = max(int(cropped.shape[0] * scale), 16)
    small = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Structural similarity on two equal-sized grayscale arrays."""
    from skimage.metrics import structural_similarity

    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    win = min(7, a.shape[0], a.shape[1])
    if win % 2 == 0:
        win -= 1
    if win < 3:
        return 1.0 if np.array_equal(a, b) else 0.0
    return float(structural_similarity(a, b, win_size=win, data_range=255))


def phash_of(frame_bgr: np.ndarray) -> imagehash.ImageHash:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    return imagehash.phash(Image.fromarray(rgb))


def phash_from_str(value: str) -> imagehash.ImageHash:
    return imagehash.hex_to_hash(value)


def save_frame(frame_bgr: np.ndarray, path: Path, quality: int = 88) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".jpg", ".jpeg"}:
        cv2.imwrite(str(path), frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    else:
        cv2.imwrite(str(path), frame_bgr)
    return path


def resize_for_llm(src: Path, dst: Path, max_edge: int) -> Path:
    """Cap the long edge before any image goes near a vision call.

    Anthropic downscales anything larger anyway, so sending full-res
    screenshots is pure wasted spend.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = im.convert("RGB")
        long_edge = max(im.size)
        if long_edge > max_edge:
            scale = max_edge / long_edge
            im = im.resize(
                (max(int(im.width * scale), 1), max(int(im.height * scale), 1)),
                Image.LANCZOS,
            )
        im.save(dst, format="JPEG", quality=82, optimize=True)
    return dst
