"""Stage 4 — turn the similarity timeline into discrete change events.

Three ideas do the work here:

1. **The threshold is adaptive, not fixed.** Frame-to-frame dissimilarity has a
   noise floor set by the recording itself — codec, resolution, how much of the
   screen is uniform chrome. On a real admin panel the floor sat at 0.014 while
   genuine screen changes reached only 0.04, so a hand-picked constant either
   fires on everything or nothing, and the right constant differs per video.
   We estimate the floor with a rolling median + MAD and flag departures from
   it. MAD rather than standard deviation because the spikes we are hunting
   would otherwise inflate the very statistic meant to detect them.

2. **SSIM and pHash are OR'd, not AND'd.** They fail in opposite directions.
   SSIM is diluted when most of the frame is uniform background, so a dialog
   opening over a white page barely registers; pHash is too coarse to notice a
   form's fields being relabelled. Requiring both misses about half of real
   transitions — measured on the fixture, 2 of 5.

3. **The frame we keep is not the frame where the change was detected.** Mid
   transition the screen is a cross-fade, a spinner, or a half-painted dialog.
   We wait out `settle_delay_ms` and take the first frame whose similarity to
   its *successor* has returned to the noise floor — the first moment the UI
   has stopped moving.
"""

from __future__ import annotations

import statistics
from typing import Any

from ..models import ChangeEvent, SampledFrame
from .base import JobPaths, Stage


class DetectChangesStage(Stage):
    name = "detect_changes"
    depends_on = ["frames"]

    def config_slice(self) -> dict[str, Any]:
        return {"change_detection": self.cfg.change_detection.model_dump()}

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        cd = self.cfg.change_detection
        frames = [SampledFrame.model_validate(f) for f in inputs["frames"]["frames"]]
        if len(frames) < 3:
            return {"events": [], "count": 0, "raw_count": 0,
                    "note": "too few frames to establish a noise floor"}

        # Dissimilarity of each frame from its predecessor. Index 0 is a
        # placeholder; there is nothing before the first frame.
        diss = [0.0] + [1.0 - f.ssim_prev for f in frames[1:]]
        floor = self._rolling_floor(diss, cd.adaptive.window)

        flags, reasons = self._flag_changes(frames, diss, floor, cd)
        runs = self._group_runs(flags)

        raw_events: list[ChangeEvent] = []

        # The opening screen is a step, but nothing changed to produce it, so
        # no run covers it. Seed an event on the first settled frame; without
        # this every SOP silently starts at step 2.
        if cd.include_initial_state:
            idx, stab, settled = self._find_stable_frame(frames, diss, floor, 0, cd)
            first = frames[idx]
            raw_events.append(ChangeEvent(
                change_timestamp=0.0,
                stable_timestamp=first.timestamp,
                stable_frame_index=first.index,
                stable_frame_path=first.path,
                phash=first.phash,
                magnitude=1.0,          # the initial state is maximally informative
                stability=round(stab, 5),
                settled=settled,
            ))

        for start, end in runs:
            worst = max(diss[start:end + 1])
            stable_idx, stability, settled = self._find_stable_frame(
                frames, diss, floor, end, cd
            )
            stable = frames[stable_idx]
            raw_events.append(ChangeEvent(
                change_timestamp=frames[start].timestamp,
                stable_timestamp=stable.timestamp,
                stable_frame_index=stable.index,
                stable_frame_path=stable.path,
                phash=stable.phash,
                magnitude=round(worst, 5),
                stability=round(stability, 5),
                settled=settled,
            ))

        events = self._merge_close(raw_events, cd.min_event_gap_ms / 1000.0)

        med = statistics.median(diss[1:]) if len(diss) > 1 else 0.0
        unsettled = sum(1 for e in events if not e.settled)
        print(
            f"[detect_changes] noise floor (median dissimilarity) = {med:.4f} | "
            f"{len(raw_events)} raw -> {len(events)} events"
            + (f" | {unsettled} never fully settled" if unsettled else "")
        )
        for e in events:
            flag = "" if e.settled else "  (unsettled)"
            print(
                f"    change t={e.change_timestamp:6.2f}s -> screenshot t={e.stable_timestamp:6.2f}s "
                f"mag={e.magnitude:.4f} stab={e.stability:.4f}{flag}"
            )

        return {
            "count": len(events),
            "raw_count": len(raw_events),
            "noise_floor": round(med, 6),
            "trigger_reasons": reasons,
            "events": [e.model_dump() for e in events],
        }

    # ----------------------------------------------------------------------

    @staticmethod
    def _rolling_floor(diss: list[float], window: int) -> list[tuple[float, float]]:
        """Per-index (median, MAD) of the surrounding dissimilarities.

        Centred window, so a change near the start of the video is judged
        against the same statistic as one in the middle.
        """
        out: list[tuple[float, float]] = []
        half = max(window // 2, 1)
        body = diss[1:] or [0.0]
        for i in range(len(diss)):
            lo = max(0, (i - 1) - half)
            hi = min(len(body), (i - 1) + half + 1)
            chunk = body[lo:hi] or body
            med = statistics.median(chunk)
            mad = statistics.median([abs(x - med) for x in chunk])
            out.append((med, mad))
        return out

    def _flag_changes(self, frames, diss, floor, cd) -> tuple[list[bool], dict[str, int]]:
        flags = [False] * len(frames)
        reasons = {"adaptive": 0, "phash": 0, "hard": 0}

        for i in range(1, len(frames)):
            med, mad = floor[i]
            d = diss[i]
            scale = 1.4826 * mad

            adaptive_hit = False
            if cd.adaptive.enabled:
                z = (d - med) / scale if scale > 1e-9 else (float("inf") if d > med else 0.0)
                adaptive_hit = (
                    z >= cd.adaptive.k
                    and d >= med * cd.adaptive.min_ratio
                    and d >= cd.adaptive.abs_floor
                )

            phash_hit = frames[i].phash_dist_prev >= cd.phash_distance_threshold
            hard_hit = (
                cd.hard_change_threshold > 0
                and frames[i].ssim_prev < cd.hard_change_threshold
            )

            if adaptive_hit or phash_hit or hard_hit:
                flags[i] = True
                if adaptive_hit:
                    reasons["adaptive"] += 1
                if phash_hit:
                    reasons["phash"] += 1
                if hard_hit:
                    reasons["hard"] += 1
        return flags, reasons

    @staticmethod
    def _group_runs(flags: list[bool]) -> list[tuple[int, int]]:
        """Consecutive flagged frames are one transition, not several."""
        runs: list[tuple[int, int]] = []
        i = 0
        while i < len(flags):
            if not flags[i]:
                i += 1
                continue
            j = i
            while j + 1 < len(flags) and flags[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        return runs

    def _find_stable_frame(self, frames, diss, floor, run_end: int, cd) -> tuple[int, float, bool]:
        """First settled frame at or after the end of a transition.

        Returns (index, similarity-to-next, actually-settled). If nothing
        settles inside the budget we take the calmest frame seen and mark it
        unsettled — a busy animation still yields a screenshot, and surfaces
        as low confidence downstream rather than silently dropping the step.
        """
        change_ts = frames[run_end].timestamp
        settle_after = change_ts + cd.settle_delay_ms / 1000.0
        deadline = change_ts + cd.max_settle_wait_ms / 1000.0

        best_idx, best_sim = run_end, -1.0

        for k in range(run_end, len(frames) - 1):
            sim_to_next = frames[k + 1].ssim_prev
            if sim_to_next > best_sim:
                best_idx, best_sim = k, sim_to_next
            if frames[k].timestamp < settle_after:
                continue
            med, _ = floor[k + 1]
            quiet = (
                diss[k + 1] <= med * cd.stability_ratio
                or sim_to_next >= cd.stability_threshold
            )
            if quiet:
                return k, sim_to_next, True
            if frames[k].timestamp > deadline:
                break

        if best_idx >= len(frames) - 1:
            return len(frames) - 1, 1.0, True
        return best_idx, max(best_sim, 0.0), False

    @staticmethod
    def _merge_close(events: list[ChangeEvent], min_gap_sec: float) -> list[ChangeEvent]:
        """Collapse events whose stable frames land on top of each other.

        Keeps the larger-magnitude one: that is the frame most likely to show
        the meaningful new state rather than the tail of the settle.
        """
        if not events:
            return []
        merged = [events[0]]
        for e in events[1:]:
            last = merged[-1]
            if e.stable_timestamp - last.stable_timestamp < min_gap_sec:
                keeper = e if e.magnitude > last.magnitude else last
                keeper.magnitude = max(e.magnitude, last.magnitude)
                keeper.merged_from = last.merged_from + e.merged_from
                keeper.change_timestamp = min(e.change_timestamp, last.change_timestamp)
                merged[-1] = keeper
            else:
                merged.append(e)
        return merged
