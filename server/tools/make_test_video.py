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
    def __init__(self, key, header, fields, button, note="", rows=0):
        self.key = key
        self.header = header
        self.fields = fields
        self.button = button
        self.note = note
        #: Number of table rows, used by the scroll noise scenario.
        self.rows = rows


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


def draw_screen(
    s: Screen,
    clock: str = "09:41",
    scroll: int = 0,
    hover: bool = False,
    cursor: tuple[int, int] | None = None,
) -> np.ndarray:
    img = np.full((H, W, 3), BG, dtype=np.uint8)

    # chrome — the taskbar clock ticks over independently of the workflow,
    # which is precisely the kind of change that must not become a step.
    cv2.rectangle(img, (0, 0), (W, 56), (255, 255, 255), -1)
    cv2.line(img, (0, 56), (W, 56), BORDER, 1)
    cv2.putText(img, "Acme Admin", (28, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.75, TEXT, 2)
    cv2.putText(img, "help  settings  account", (W - 340, 35),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, MUTED, 1)
    cv2.rectangle(img, (0, H - 34), (W, H), (238, 240, 244), -1)
    cv2.putText(img, clock, (W - 88, H - 11), cv2.FONT_HERSHEY_SIMPLEX, 0.55, TEXT, 1)

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

    # a long table, drawn offset by `scroll`, clipped to the panel
    if s.rows:
        top = y0 + 110
        clip = img[top:y1 - 70, x0 + 1:x1 - 1].copy()
        clip[:] = PANEL
        for r in range(s.rows):
            ry = r * 44 - scroll
            if -44 < ry < clip.shape[0]:
                shade = (250, 250, 252) if r % 2 else (255, 255, 255)
                cv2.rectangle(clip, (0, ry), (clip.shape[1], ry + 42), shade, -1)
                cv2.putText(clip, f"INV-{4100 + r}   Vendor {chr(65 + r % 26)}   $ {120 + r * 37}",
                            (24, ry + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.5, TEXT, 1)
        img[top:y1 - 70, x0 + 1:x1 - 1] = clip

    if s.note:
        cv2.putText(img, s.note, (x0 + 40, y + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, MUTED, 1)

    # primary button — the thing the diff engine should notice changing
    bw = 40 + 16 * len(s.button)
    bx, by = x1 - 40 - bw, y1 - 90
    shade = tuple(int(c * 0.82) for c in ACCENT) if hover else ACCENT
    cv2.rectangle(img, (bx, by), (bx + bw, by + 52), shade, -1)
    cv2.putText(img, s.button, (bx + 20, by + 34), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    if cursor:
        draw_cursor(img, *cursor)
    return img


def draw_cursor(img: np.ndarray, x: int, y: int) -> None:
    pts = np.array([[x, y], [x, y + 17], [x + 5, y + 13],
                    [x + 9, y + 20], [x + 12, y + 18],
                    [x + 8, y + 11], [x + 13, y + 11]], np.int32)
    cv2.fillPoly(img, [pts], (255, 255, 255))
    cv2.polylines(img, [pts], True, (20, 20, 20), 1)


def draw_alt_tab(base: np.ndarray) -> np.ndarray:
    """An unrelated app briefly on top. Not a step in this procedure."""
    out = base.copy()
    cv2.rectangle(out, (90, 90), (W - 90, H - 90), (44, 46, 52), -1)
    cv2.rectangle(out, (90, 90), (W - 90, H - 90), (20, 20, 24), 2)
    cv2.putText(out, "Mail - Inbox (12)", (130, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (235, 235, 240), 2)
    for r in range(7):
        cv2.putText(out, f"Re: quarterly figures  -  sender {r + 1}",
                    (130, 220 + r * 52), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (190, 192, 200), 1)
    return out


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


# Same six real steps as V1, but the dashboard is a long scrollable table and
# the recording is padded with the behaviours that must be rejected as noise.
NOISY = [
    Screen("login",  "Sign in to Admin",    ["Email", "Password"],           "Log in"),
    Screen("dash",   "Dashboard",           [],                             "New request", rows=26),
    Screen("form",   "New expense request", ["Amount", "Category", "Notes"], "Save"),
    Screen("attach", "Attach receipt",      ["File"],                       "Upload"),
    Screen("review", "Review and confirm",  ["Approver"],                   "Confirm"),
    Screen("done",   "Request submitted",   [],                             "Back to dashboard"),
]


class Recorder:
    """Writes frames and keeps the wall clock ticking across the recording."""

    def __init__(self, writer):
        self.writer = writer
        self.n = 0

    def clock(self) -> str:
        # One minute of wall clock per ~4s of video, so the taskbar digits
        # change several times during the recording.
        total = 9 * 60 + 41 + self.n // (FPS * 4)
        return f"{total // 60:02d}:{total % 60:02d}"

    def write(self, img: np.ndarray, grain: int = 3) -> None:
        noise = np.random.randint(-grain, grain + 1, img.shape, dtype=np.int16)
        self.writer.write(np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8))
        self.n += 1

    def hold(self, s: Screen, seconds: float, **kw) -> None:
        for _ in range(int(seconds * FPS)):
            self.write(draw_screen(s, clock=self.clock(), **kw))

    def transition(self, prev_img: np.ndarray, s: Screen, **kw) -> None:
        target = draw_screen(s, clock=self.clock(), **kw)
        for i in range(TRANSITION_FRAMES):
            a = (i + 1) / (TRANSITION_FRAMES + 1)
            self.write(spinner(cv2.addWeighted(prev_img, 1 - a, target, a, 0), i))


def build_noisy(out_path: Path) -> None:
    """A recording where 6 real steps are buried in five kinds of noise.

    Ground truth: 6 steps. Everything else — scrolling a table, hovering a
    button, drifting the cursor, the taskbar clock ticking, and alt-tabbing to
    a mail client and back — must be rejected.
    """
    raw = out_path.with_suffix(".raw.avi")
    writer = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    if not writer.isOpened():
        raise RuntimeError("OpenCV could not open a VideoWriter")
    rec = Recorder(writer)

    login, dash, form, attach, review, done = NOISY

    # 1. login — cursor drifts across the screen while nothing else happens
    rec.hold(login, 1.8)
    for i in range(int(1.6 * FPS)):
        rec.write(draw_screen(login, clock=rec.clock(),
                              cursor=(180 + i * 9, 620 - i * 6)))
    # hover the button before clicking: colour shifts, but it is not a step
    rec.hold(login, 1.0, hover=True, cursor=(900, 560))

    # 2. dashboard — then scroll the table up and back down
    rec.transition(draw_screen(login, clock=rec.clock()), dash)
    rec.hold(dash, 2.0)
    for i in range(int(2.2 * FPS)):
        rec.write(draw_screen(dash, clock=rec.clock(), scroll=int(i * 14)))
    for i in range(int(1.4 * FPS)):
        rec.write(draw_screen(dash, clock=rec.clock(), scroll=max(0, 460 - int(i * 24))))
    rec.hold(dash, 1.2)

    # 3. form
    rec.transition(draw_screen(dash, clock=rec.clock()), form)
    rec.hold(form, 2.4)

    # alt-tab to a mail client and back — a whole different application
    base = draw_screen(form, clock=rec.clock())
    for i in range(3):
        rec.write(cv2.addWeighted(base, 1 - (i + 1) / 4, draw_alt_tab(base), (i + 1) / 4, 0))
    for _ in range(int(1.8 * FPS)):
        rec.write(draw_alt_tab(draw_screen(form, clock=rec.clock())))
    for i in range(3):
        cur = draw_screen(form, clock=rec.clock())
        rec.write(cv2.addWeighted(draw_alt_tab(cur), 1 - (i + 1) / 4, cur, (i + 1) / 4, 0))
    rec.hold(form, 1.6)

    # 4-6. the remaining steps, plus one more hover
    rec.transition(draw_screen(form, clock=rec.clock()), attach)
    rec.hold(attach, 2.6)
    rec.transition(draw_screen(attach, clock=rec.clock()), review)
    rec.hold(review, 1.4)
    rec.hold(review, 0.8, hover=True)
    rec.hold(review, 1.0)
    rec.transition(draw_screen(review, clock=rec.clock()), done)
    rec.hold(done, 2.6)

    writer.release()
    _encode(raw, out_path)
    print(f"wrote {out_path}  (6 real steps + scroll/hover/cursor/clock/alt-tab noise)")


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
    _encode(raw, out_path)
    print(f"wrote {out_path}  ({len(screens)} screens, ~{len(screens) * HOLD_SEC:.0f}s)")


def _encode(raw: Path, out_path: Path) -> None:
    subprocess.run(
        [resolve_ffmpeg(load_config()), "-y", "-i", str(raw),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", str(out_path)],
        check=True, capture_output=True,
    )
    raw.unlink(missing_ok=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--variant", choices=["v1", "v2", "noisy"], default="v1")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    if a.variant == "noisy":
        build_noisy(Path(a.out))
    else:
        build(V1 if a.variant == "v1" else V2, Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
