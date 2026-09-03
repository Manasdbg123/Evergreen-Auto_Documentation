"""Stage 3 — sample the video and measure frame-to-frame similarity.

This stage does no interpretation. It walks the video once at the configured
rate, saves each sampled frame, and records pHash + SSIM against the previous
sample. Everything downstream reads that timeline rather than the video, which
is what makes the later stages cheap to re-run.
"""

from __future__ import annotations

from typing import Any

import cv2

from ..models import SampledFrame
from .base import JobPaths, Stage
from .video import phash_of, save_frame, ssim, to_analysis_gray


#: Frame rates outside this range are metadata errors rather than real videos.
#: A VFR WebM commonly reports 1000 (a placeholder) or 0 (unknown).
_MIN_FPS, _MAX_FPS = 1.0, 240.0


class _FrameClock:
    """Answers "what time is this frame at?" for containers that will not say.

    Three sources, in descending order of trust:

    1. The container's own presentation timestamp, read per frame during
       sequential decode. Correct even when the frame rate varies.
    2. The declared frame rate, when it is plausible.
    3. A counted frame rate — decode the file once to count frames, divide by
       the known duration. Slow, and the only thing left when a recorder writes
       neither a frame rate nor usable timestamps.

    The presentation timestamp is checked rather than trusted: some builds
    return 0 for every frame, which would put every sample at t=0 and collapse
    the whole recording into one candidate.
    """

    def __init__(self, cap, src, duration: float):
        self._cap = cap
        self._duration = duration
        self._last_pts = -1.0
        self._pts_ok = True

        declared = cap.get(cv2.CAP_PROP_FPS) or 0.0
        if _MIN_FPS < declared <= _MAX_FPS:
            self._fps = declared
            self.basis = f"declared {declared:.1f}fps"
        else:
            self._fps = _count_fps(src, duration)
            self.basis = f"counted {self._fps:.1f}fps (declared {declared:.0f})"

    def time_of(self, decoded_index: int) -> float:
        """Seconds into the recording for the frame just read."""
        if self._pts_ok:
            pts = self._cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0
            # Strictly increasing and inside the file: a real timestamp.
            if pts > self._last_pts and pts <= (self._duration + 1.0) * 1000.0:
                self._last_pts = pts
                return pts / 1000.0
            # One stall is a quirk; at the very start it means the backend is
            # not reporting timestamps at all, so stop asking.
            if decoded_index > 1:
                self._pts_ok = False
        return decoded_index / self._fps


def _count_fps(src, duration: float) -> float:
    """Decode once to count frames, when nothing else can be believed."""
    if duration <= 0:
        return 30.0
    cap = cv2.VideoCapture(str(src))
    count = 0
    try:
        while cap.grab():  # grab() skips decoding the pixels — much cheaper
            count += 1
    finally:
        cap.release()
    return (count / duration) if count else 30.0


class FramesStage(Stage):
    name = "frames"
    depends_on = ["ingest"]

    def config_slice(self) -> dict[str, Any]:
        return {
            "frames": self.cfg.frames.model_dump(),
            # Cropping affects the recorded SSIM, so it belongs to this stage's key.
            "ignore_top_pct": self.cfg.change_detection.ignore_top_pct,
            "ignore_bottom_pct": self.cfg.change_detection.ignore_bottom_pct,
        }

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        meta = inputs["ingest"]
        src = job.abs(meta["source_path"])
        duration = float(meta["duration"])
        step = 1.0 / self.cfg.frames.sample_fps

        for stale in job.sampled.glob("*"):
            stale.unlink()

        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            raise RuntimeError(f"OpenCV could not open {src}")

        clock = _FrameClock(cap, src, duration)

        frames: list[SampledFrame] = []
        prev_gray = None
        prev_hash = None
        index = 0
        next_target = 0.0
        decoded = 0

        try:
            # Sequential decode, not seeking.
            #
            # This used to seek to each sample time with CAP_PROP_POS_MSEC. That
            # works on a constant-frame-rate mp4 and fails on variable-frame-rate
            # WebM, which is what every GNOME/Wayland screen recorder produces:
            # the container carries no usable frame rate (r_frame_rate=1000/1,
            # avg_frame_rate=0/0), OpenCV's second seek returns false, the loop
            # exits, and a 43-second recording yields THREE frames. Silently —
            # the SOP just comes out with one step. Reading straight through and
            # selecting on timestamp costs a full decode and is correct for every
            # container.
            while True:
                ok, frame = cap.read()
                if not ok or frame is None:
                    break

                t = clock.time_of(decoded)
                decoded += 1
                if t < next_target:
                    continue
                if t >= duration:
                    break
                next_target += step

                gray = to_analysis_gray(frame, self.cfg)
                h = phash_of(frame)

                if prev_gray is None:
                    sim, dist = 1.0, 0
                else:
                    sim = ssim(prev_gray, gray)
                    dist = int(prev_hash - h)

                path = job.sampled / f"f{index:05d}.jpg"
                save_frame(frame, path)

                frames.append(SampledFrame(
                    index=index,
                    timestamp=round(t, 3),
                    path=job.rel(path),
                    phash=str(h),
                    ssim_prev=round(sim, 5),
                    phash_dist_prev=dist,
                ))

                prev_gray, prev_hash = gray, h
                index += 1
        finally:
            cap.release()

        if not frames:
            raise RuntimeError(f"No frames could be decoded from {src.name}")

        sims = [f.ssim_prev for f in frames[1:]] or [1.0]
        print(
            f"[frames] sampled {len(frames)} frames at {self.cfg.frames.sample_fps}fps "
            f"over {duration:.1f}s ({decoded} decoded, {clock.basis}) | "
            f"ssim min={min(sims):.3f} mean={sum(sims)/len(sims):.3f}"
        )

        # Undersampling is the failure that hides. Every later stage keeps
        # working on a short timeline and produces a plausible, wrong SOP, so
        # the only place it can be caught is here, where the expected count is
        # known.
        expected = duration * self.cfg.frames.sample_fps
        if len(frames) < expected * 0.5:
            print(
                f"[frames] WARNING: expected ~{expected:.0f} samples but got "
                f"{len(frames)}. The decoder returned {decoded} frames for a "
                f"{duration:.1f}s file. If the SOP comes out short, re-encode "
                f"to constant frame rate:\n"
                f"           ffmpeg -i {src.name} -r 30 -c:v libx264 fixed.mp4"
            )

        return {
            "sample_fps": self.cfg.frames.sample_fps,
            "duration": duration,
            "count": len(frames),
            "frames": [f.model_dump() for f in frames],
        }
