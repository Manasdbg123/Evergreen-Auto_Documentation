"""Synthesise a fake screen recording, so the pipeline can be tested without
hunting for a real one — and so v1/v2 pairs exist for the diff engine.

    python tools/make_test_video.py --variant v1 --out /tmp/v1.mp4
    python tools/make_test_video.py --variant v2 --out /tmp/v2.mp4

v2 is the same workflow after a UI change: one button relabelled, one screen
added, one removed, two swapped. That gives the diff engine a known-correct
answer to be graded against.

Each screen is held still for a while and separated by deliberately blurred
transition frames plus a loading spinner — the exact thing stable-frame
selection is supposed to skip over.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import load_config, resolve_ffmpeg  # noqa: E402

W, H = 1280, 720
FPS = 15
HOLD_SEC = 2.6
TRANSITION_FRAMES = 5

BG = (248, 249, 251)
PANEL = (255, 255, 255)
BORDER = (222, 226, 232)
TEXT = (32, 38, 48)
MUTED = (128, 138, 152)
ACCENT = (206, 122, 38)   # BGR


class Screen:
    def __init__(self, key, header, fields, button, note=""):
        self.key = key
        self.header = header
        self.fields = fields
        self.button = button
        self.note = note


V1 = [
    Screen("login",    "Sign in to Admin",      ["Email", "Password"],            "Log in"),
    Screen("dash",     "Dashboard",             ["Search records"],               "New request"),
    Screen("form",     "New expense request",   ["Amount", "Category", "Notes"],  "Save"),
    Screen("attach",   "Attach receipt",        ["File"],                         "Upload"),
    Screen("review",   "Review and confirm",    ["Approver"],                     "Confirm"),
    Screen("done",     "Request submitted",     [],                               "Back to dashboard"),
]

# The UI change: "Save" became "Submit", a 2FA screen appeared after login,
# the attachment step was dropped, and review/confirm moved before the form.
V2 = [
    Screen("login",    "Sign in to Admin",      ["Email", "Password"],            "Log in"),
    Screen("twofa",    "Two-factor verification", ["6-digit code"],               "Verify"),
    Screen("dash",     "Dashboard",             ["Search records"],               "New request"),
    Screen("review",   "Review and confirm",    ["Approver"],                     "Confirm"),
    Screen("form",     "New expense request",   ["Amount", "Category", "Notes"],  "Submit"),
    Screen("done",     "Request submitted",     [],                               "Back to dashboard"),
]


def draw_screen(s: Screen) -> np.ndarray:
    img = np.full((H, W, 3), BG, dtype=np.uint8)

    # chrome
    cv2.rectangle(img, (0, 0), (W, 56), (255, 255, 255), -1)
    cv2.line(img, (0, 56), (W, 56), BORDER, 1)
    cv2.putText(img, "Acme Admin", (28, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.75, TEXT, 2)
    cv2.putText(img, "help  settings  account", (W - 340, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, MUTED, 1)

    # panel
    x0, y0, x1, y1 = 180, 120, W - 180, H - 90
    cv2.rectangle(img, (x0, y0), (x1, y1), PANEL, -1)
    cv2.rectangle(img, (x0, y0), (x1, y1), BORDER, 1)
    cv2.putText(img, s.header, (x0 + 40, y0 + 62), cv2.FONT_HERSHEY_SIMPLEX, 0.95, TEXT, 2)

    y = y0 + 120
    for label in s.fields:
        cv2.putText(img, label, (x0 + 40, y), cv2.FONT_HERSHEY_SIMPLEX, 0.52, MUTED, 1)
        cv2.rectangle(img, (x0 + 40, y + 14), (x0 + 480, y + 58), (252, 252, 253), -1)
        cv2.rectangle(img, (x0 + 40, y + 14), (x0 + 480, y + 58), BORDER, 1)
        y += 92

    if s.note:
        cv2.putText(img, s.note, (x0 + 40, y + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, MUTED, 1)

    # primary button — the thing the diff engine should notice changing
    bw = 40 + 16 * len(s.button)
    bx, by = x1 - 40 - bw, y1 - 90
    cv2.rectangle(img, (bx, by), (bx + bw, by + 52), ACCENT, -1)
    cv2.putText(img, s.button, (bx + 20, by + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    return img


def spinner(frame: np.ndarray, phase: int) -> np.ndarray:
    out = cv2.GaussianBlur(frame, (31, 31), 0)
    out = (out * 0.75 + 60).astype(np.uint8)
    cx, cy, r = W // 2, H // 2, 34
    for k in range(12):
        ang = np.deg2rad(k * 30 + phase * 30)
        shade = int(90 + 140 * (k / 12))
        p1 = (int(cx + r * np.cos(ang)), int(cy + r * np.sin(ang)))
        p2 = (int(cx + (r + 14) * np.cos(ang)), int(cy + (r + 14) * np.sin(ang)))
        cv2.line(out, p1, p2, (shade, shade, shade), 4)
    return out


def build(screens: list[Screen], out_path: Path) -> None:
    raw = out_path.with_suffix(".raw.avi")
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not open a VideoWriter")

    prev = None
    for s in screens:
        img = draw_screen(s)
        if prev is not None:
            # cross-fade + spinner: the mid-transition mush we must not screenshot
            for i in range(TRANSITION_FRAMES):
                a = (i + 1) / (TRANSITION_FRAMES + 1)
                blend = cv2.addWeighted(prev, 1 - a, img, a, 0)
                writer.write(spinner(blend, i))
        for i in range(int(HOLD_SEC * FPS)):
            # a little noise so nothing is bit-identical, like a real recording
            noise = np.random.randint(-2, 3, img.shape, dtype=np.int16)
            writer.write(np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8))
        prev = img
    writer.release()

    subprocess.run(
        [resolve_ffmpeg(load_config()), "-y", "-i", str(raw),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", str(out_path)],
        check=True, capture_output=True,
    )
    raw.unlink(missing_ok=True)
    print(f"wrote {out_path}  ({len(screens)} screens, ~{len(screens) * HOLD_SEC:.0f}s)")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--variant", choices=["v1", "v2"], default="v1")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    build(V1 if a.variant == "v1" else V2, Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
