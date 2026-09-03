"""Stage 1 regression tests, graded against fixtures with known ground truth.

These encode the guarantees that everything downstream depends on. If frame
selection regresses, no amount of LLM quality can recover — so these run
without an API key and without a real recording.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import load_config  # noqa: E402
from app.pipeline.base import JobPaths  # noqa: E402
from app.pipeline.detect_changes import DetectChangesStage  # noqa: E402
from app.pipeline.frames import FramesStage, _FrameClock  # noqa: E402
from app.pipeline.ingest import IngestStage, place_upload  # noqa: E402
from app.pipeline.select_candidates import SelectCandidatesStage  # noqa: E402
from app.pipeline.video import ink_iou, ink_mask  # noqa: E402

sys.path.insert(0, str(ROOT / "tools"))
import make_test_video as fixture  # noqa: E402


def _build(variant: str, tmp: Path) -> Path:
    out = tmp / f"{variant}.mp4"
    if variant == "noisy":
        fixture.build_noisy(out)
    else:
        fixture.build(fixture.V1 if variant == "v1" else fixture.V2, out)
    return out


@pytest.fixture(scope="module", autouse=True)
def isolated_data_dir(tmp_path_factory):
    """Keep the fixture jobs out of the real `data/jobs/`.

    These tests run the full stage-1 pipeline over generated videos. Without
    this they wrote `data/jobs/test_v1`, `test_v2` and `test_noisy` into the
    same directory as the demo, so a test run littered the demo directory and
    two concurrent pytest runs raced on identical job ids. `EVERGREEN_DATA_DIR`
    is read through the `Config.data_root` property on every access, so it
    takes effect on the already-cached config object.
    """
    previous = os.environ.get("EVERGREEN_DATA_DIR")
    os.environ["EVERGREEN_DATA_DIR"] = str(tmp_path_factory.mktemp("data"))
    yield
    if previous is None:
        os.environ.pop("EVERGREEN_DATA_DIR", None)
    else:
        os.environ["EVERGREEN_DATA_DIR"] = previous


@pytest.fixture(scope="module")
def cfg(isolated_data_dir):
    return load_config()


@pytest.fixture(scope="module")
def jobs(cfg, tmp_path_factory):
    """Run the full stage-1 pipeline once per variant."""
    tmp = tmp_path_factory.mktemp("fixtures")
    out = {}
    for variant in ("v1", "v2", "noisy"):
        video = _build(variant, tmp)
        job_id = f"test_{variant}"
        place_upload(cfg, job_id, video, video.name)
        for stage in (IngestStage, FramesStage, DetectChangesStage, SelectCandidatesStage):
            stage(cfg).run(job_id, force=True)
        out[variant] = JobPaths(cfg, job_id)
    return out


def _stage(job: JobPaths, name: str) -> dict:
    import json

    return json.loads(job.stage_file(name).read_text())


# --------------------------------------------------------------------------
# Frame sampling
# --------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["v1", "v2"])
def test_sampling_covers_the_whole_recording(jobs, cfg, variant):
    """The timeline must span the video, at roughly the configured rate.

    Undersampling is the failure that hides: every later stage keeps working
    on a short timeline and produces a plausible, wrong SOP with no error
    anywhere. Only this stage knows how many samples there should have been.
    """
    data = _stage(jobs[variant], "frames")
    expected = data["duration"] * cfg.frames.sample_fps
    assert data["count"] >= expected * 0.8, (
        f"sampled {data['count']} frames from {data['duration']:.1f}s at "
        f"{cfg.frames.sample_fps}fps — expected about {expected:.0f}"
    )
    last = data["frames"][-1]["timestamp"]
    assert last >= data["duration"] * 0.8, (
        f"sampling stopped at {last:.1f}s of {data['duration']:.1f}s"
    )


class _FakeCap:
    """Stands in for cv2.VideoCapture's metadata, which is what lies."""

    def __init__(self, fps, pts=None):
        self._fps, self._pts, self._i = fps, pts, -1

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return self._fps
        if prop == cv2.CAP_PROP_POS_MSEC:
            if self._pts is None:
                return 0.0
            self._i += 1
            return self._pts[min(self._i, len(self._pts) - 1)]
        return 0.0


def test_frame_clock_prefers_real_timestamps():
    clock = _FrameClock(_FakeCap(30.0, pts=[0.0, 100.0, 250.0]), None, 10.0)
    assert clock.time_of(0) == 0.0
    assert clock.time_of(1) == pytest.approx(0.10)
    # 250ms after two frames is not 2/30s — the container's own clock wins.
    assert clock.time_of(2) == pytest.approx(0.25)


def test_frame_clock_falls_back_when_timestamps_are_dead():
    """Some backends return 0 for every frame; that must not stack every
    sample at t=0 and collapse the recording to one candidate."""
    clock = _FrameClock(_FakeCap(30.0, pts=None), None, 10.0)
    times = [clock.time_of(i) for i in range(4)]
    assert times == pytest.approx([0.0, 1 / 30, 2 / 30, 3 / 30])


def test_frame_clock_rejects_an_impossible_declared_rate(monkeypatch):
    """The regression. GNOME's recorder writes r_frame_rate=1000/1 into a
    variable-frame-rate WebM. Believing it made a 43s recording yield three
    samples, and the SOP came out with one step and no error."""
    import app.pipeline.frames as frames_mod

    monkeypatch.setattr(frames_mod, "_count_fps", lambda src, duration: 28.6)
    clock = _FrameClock(_FakeCap(1000.0, pts=None), None, 43.1)
    assert "counted" in clock.basis

    # Consumed in order, as the decode loop does — the clock is sequential.
    times = [clock.time_of(i) for i in range(287)]
    assert times[-1] == pytest.approx(10.0, abs=0.05), (
        "believing the declared 1000fps puts frame 286 at 0.29s, so the whole "
        "recording collapses into the first fraction of a second"
    )


# --------------------------------------------------------------------------
# Change detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize("variant", ["v1", "v2"])
def test_clean_recording_finds_every_screen(jobs, variant):
    """Six screens in, six candidates out — including the opening screen."""
    cands = _stage(jobs[variant], "select_candidates")
    assert cands["count"] == 6, f"expected 6 candidates, got {cands['count']}"


@pytest.mark.parametrize("variant", ["v1", "v2"])
def test_no_screenshot_lands_mid_transition(jobs, variant):
    """Every chosen frame must have settled.

    The fixture puts a spinner and a cross-fade between every screen, so a
    naive "screenshot at the moment of change" implementation fails this.
    """
    events = _stage(jobs[variant], "detect_changes")["events"]
    unsettled = [e for e in events if not e["settled"]]
    assert not unsettled, f"{len(unsettled)} events never settled: {unsettled}"


@pytest.mark.parametrize("variant", ["v1", "v2"])
def test_stable_frame_is_after_the_change(jobs, variant):
    events = _stage(jobs[variant], "detect_changes")["events"]
    for e in events:
        assert e["stable_timestamp"] >= e["change_timestamp"]


def test_opening_screen_is_captured(jobs):
    """Without this the SOP silently starts at step 2."""
    events = _stage(jobs["v1"], "detect_changes")["events"]
    assert min(e["stable_timestamp"] for e in events) < 1.5


# --------------------------------------------------------------------------
# Noise rejection
# --------------------------------------------------------------------------


def test_noise_does_not_explode_the_candidate_set(jobs):
    """The noisy fixture holds 6 real steps plus five kinds of noise.

    Scrolling, hover, cursor drift and a ticking taskbar clock must all be
    rejected. Alt-tab is allowed through: it is a genuine full-screen change
    that only semantics can dismiss, which is the LLM stage's job.
    """
    cands = _stage(jobs["noisy"], "select_candidates")
    assert 6 <= cands["count"] <= 8, (
        f"expected 6-8 candidates (6 real steps + at most the alt-tab pair), "
        f"got {cands['count']}"
    )


def test_scrolling_produces_no_step(jobs):
    """The dashboard table is scrolled for 3.6s around t=7.5-11s."""
    events = _stage(jobs["noisy"], "detect_changes")["events"]
    during_scroll = [e for e in events if 6.8 < e["change_timestamp"] < 11.5]
    assert not during_scroll, f"scrolling produced {len(during_scroll)} spurious events"


def test_revisiting_a_screen_is_deduplicated(jobs):
    """Returning to the form after alt-tab must not cost a second vision call."""
    cands = _stage(jobs["noisy"], "select_candidates")
    assert cands["deduped"] >= 1, "expected the return-to-form candidate to be dropped"


def test_candidate_cap_is_respected(jobs, cfg):
    for job in jobs.values():
        count = _stage(job, "select_candidates")["count"]
        assert count <= cfg.candidates.max_frames


def test_llm_images_are_within_the_size_cap(jobs, cfg):
    """Nothing oversized may reach a vision call."""
    from PIL import Image

    for job in jobs.values():
        for c in _stage(job, "select_candidates")["candidates"]:
            with Image.open(job.abs(c["llm_image_path"])) as im:
                assert max(im.size) <= cfg.frames.llm_max_edge_px


# --------------------------------------------------------------------------
# The ink-mask fingerprint
# --------------------------------------------------------------------------


def _panel(text: str, y: int = 300) -> np.ndarray:
    img = np.full((720, 1280, 3), 250, np.uint8)
    cv2.rectangle(img, (180, 120), (1100, 630), (255, 255, 255), -1)
    cv2.rectangle(img, (180, 120), (1100, 630), (222, 226, 232), 1)
    cv2.putText(img, text, (220, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (32, 38, 48), 2)
    return img


def test_ink_mask_separates_same_template_screens(cfg):
    """The property that made this primitive necessary.

    Two screens sharing a layout but differing in text must score clearly
    below the dedup threshold, so they are never merged. SSIM and pHash both
    fail this: on the fixture they rate two different screens (SSIM 0.979,
    pHash 2) closer than a true duplicate (0.992, 0).

    Measured separation, worst case first:
      panels differing only in a heading   0.80   <- this test, hardest case
      real fixture screens                 0.61 - 0.76
      a different application (alt-tab)    0.03
      the same screen revisited            0.98 - 0.99
    """
    a = _panel("Sign in to Admin")
    b = _panel("Attach receipt")
    same = ink_iou(ink_mask(a), ink_mask(a.copy()))
    different = ink_iou(ink_mask(a), ink_mask(b))

    assert same > 0.95
    # The operative contract: distinct screens must not be deduplicated.
    assert different < cfg.candidates.dedupe_ink_iou - 0.08, (
        f"only {cfg.candidates.dedupe_ink_iou - different:.3f} of margin below the "
        f"dedup threshold — too close to merge steps safely"
    )
    assert same - different > 0.15, "separation margin too small to threshold safely"


def test_ink_mask_survives_noise():
    """Compression grain must not move the signature."""
    a = _panel("Dashboard")
    noisy = np.clip(
        a.astype(np.int16) + np.random.randint(-3, 4, a.shape, dtype=np.int16), 0, 255
    ).astype(np.uint8)
    assert ink_iou(ink_mask(a), ink_mask(noisy)) > 0.9
