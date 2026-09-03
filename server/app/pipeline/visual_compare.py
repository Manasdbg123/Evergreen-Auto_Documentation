"""The diff's last resort: compare the screenshots themselves.

`DiffEngine` accepted a `visual` callable and nothing ever built one, so
`diff.use_visual_fallback`, `visual_same_screen_iou` and
`max_visual_comparisons` were all dead config. This builds it.

**Why ink IoU and not pHash.** Measured on the fixture, pHash rates two
genuinely different screens at distance 2 while a true duplicate scores 0 — so
any pHash threshold loose enough to catch duplicates also merges unrelated
steps. Screens built from one UI template are nearly identical to both pHash
and SSIM; what separates them is where the text sits. `video.ink_mask` masks to
that and the same measurement pushes apart to 0.990 vs 0.756. See
`video.py:ink_mask`.

**Why this is free.** No image reaches a model here. The comparison is local
OpenCV over two PNGs already on disk, so the `max_visual_comparisons` cap is
about latency and about not letting a blunt pixel signal overrule text, not
about spend. The cap stays enforced in `DiffEngine` regardless.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..models import Step
from .base import JobPaths
from .video import ink_iou, ink_mask_from_path


class VisualComparator:
    """Callable(old_step, new_step) -> (iou, rationale), as DiffEngine expects.

    Steps from two versions live in two different job directories, so the
    comparator is constructed with both and resolves each side against its own
    job root. Getting this wrong silently compares a step against itself.
    """

    def __init__(self, cfg: Config, old_job: JobPaths, new_job: JobPaths):
        self.cfg = cfg
        self.old_job = old_job
        self.new_job = new_job
        self._cache: dict[str, object] = {}

    def __call__(self, old: Step, new: Step) -> tuple[float, str]:
        a = self._mask(self.old_job, old.screenshot_ref)
        b = self._mask(self.new_job, new.screenshot_ref)
        if a is None or b is None:
            raise VisualUnavailable(
                "one of the steps has no readable screenshot on disk"
            )
        iou = ink_iou(a, b)

        d = self.cfg.diff
        if iou >= d.visual_same_screen_iou:
            verdict = "same screen"
        elif iou <= d.visual_different_screen_iou:
            verdict = "different screen"
        else:
            verdict = "inconclusive"
        return iou, f"screenshot ink IoU {iou:.3f} — {verdict}"

    def _mask(self, job: JobPaths, ref: str | None):
        if not ref:
            return None
        path = job.abs(ref)
        key = str(path)
        if key not in self._cache:
            self._cache[key] = (
                ink_mask_from_path(
                    Path(path), self.cfg.visual.ink_width, self.cfg.visual.ink_delta
                )
                if path.exists()
                else None
            )
        return self._cache[key]


class VisualUnavailable(RuntimeError):
    """No screenshot to compare. The caller keeps its text-derived score."""
