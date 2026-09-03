"""Stage 8 wrapper — the only thing that actually runs the diff engine.

`DiffEngine` compares two SOPs; every other stage transforms one job. This
bridges the two worlds: the diff is a stage *of the new job*, carrying a
reference to the old one, so it gets the cache, the fingerprint and the
`stages/08_diff.json` path for free while still spanning two recordings.

Where the old SOP comes from matters more than it looks. It is read from the
stored document version when there is one, and only from the previous job's
raw `structure` output when there is not. That ordering is the edit-
preservation requirement in practice: diffing against the raw generated v1
would compare against text the user never saw, and every hand edit would show
up as a change to accept or reject on every regeneration.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..llm.cost import CostLog
from ..llm.judge import build_judge
from ..models import SOP, DiffResult
from .base import JobPaths, Stage, read_stage
from .diff import DiffEngine
from .visual_compare import VisualComparator


class DiffStage(Stage):
    """Diff this job's SOP against a previous job's."""

    name = "diff"
    depends_on = ["structure"]

    def __init__(self, cfg: Config | None = None, old_job_id: str | None = None,
                 offline: bool = False):
        super().__init__(cfg)
        self.old_job_id = old_job_id
        self.offline = offline

    def config_slice(self) -> dict[str, Any]:
        return {
            "diff": self.cfg.diff.model_dump(),
            "similarity": self.cfg.similarity.model_dump(),
            "judge_model": self.cfg.models.judge,
            "old_job_id": self.old_job_id,
            "offline": self.offline,
        }

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        if not self.old_job_id:
            raise ValueError(
                "DiffStage needs an old_job_id — the diff compares two "
                "recordings. Run: python -m app.cli diff <old_job> <new_job>"
            )

        old_job = JobPaths(self.cfg, self.old_job_id)
        old_sop = load_sop(self.cfg, self.old_job_id)
        if old_sop is None:
            raise ValueError(
                f"Job '{self.old_job_id}' has no SOP. Run "
                f"`python -m app.cli run {self.old_job_id}` first."
            )
        new_sop = SOP.model_validate(inputs["structure"]["sop"])

        old_sop.version = old_sop.version or 1
        new_sop.version = old_sop.version + 1

        result, merged = self.build_engine(job, old_job).run(old_sop, new_sop)

        print(f"\n[diff] {self.old_job_id} (v{old_sop.version}) -> "
              f"{job.job_id} (v{new_sop.version})")
        for entry in result.entries:
            _print_entry(entry)

        preserved = sum(len(e.preserved_edits) for e in result.entries)
        if preserved:
            print(f"[diff] {preserved} hand-edited field(s) carried forward intact")

        return {
            "old_job_id": self.old_job_id,
            "new_job_id": job.job_id,
            "count": len(result.entries),
            "summary": result.summary,
            "diff": result.model_dump(),
            "merged_sop": merged.model_dump(),
        }

    def build_engine(self, new_job: JobPaths, old_job: JobPaths) -> DiffEngine:
        """Assemble the engine with every tier it is allowed to use.

        Both escalation tiers are optional by construction. A missing key
        removes the judge, a missing screenshot removes the visual fallback,
        and the diff still runs on the free offline tiers — blunter, never
        broken.
        """
        judge = None
        if not self.offline:
            cost_log = CostLog(self.cfg, new_job.cost_log)
            judge = build_judge(self.cfg, cost_log)

        visual = None
        if self.cfg.diff.use_visual_fallback:
            visual = VisualComparator(self.cfg, old_job, new_job)

        return DiffEngine(
            self.cfg,
            judge=judge.identity if judge else None,
            prose_judge=judge.prose if judge else None,
            visual=visual,
        )


def load_sop(cfg: Config, job_id: str) -> SOP | None:
    """The current SOP for a job: the stored version if there is one, else raw.

    Order matters — see the module docstring. The DB holds what the user has
    actually edited; `structure` holds what the model first wrote.
    """
    from ..db import latest_sop_for_job

    stored = latest_sop_for_job(cfg, job_id)
    if stored is not None:
        return stored

    data = read_stage(JobPaths(cfg, job_id), "structure")
    if not data or not data.get("sop"):
        return None
    return SOP.model_validate(data["sop"])


def _print_entry(entry) -> None:
    marker = {
        "unchanged": "  ", "modified": " ~", "added": " +",
        "removed": " -", "reordered": "->",
    }.get(entry.status.value, "  ")

    order = entry.new_order if entry.new_order is not None else entry.old_order
    line = f"{marker} {order or '?':>2}  {entry.status.value:<10}"
    if entry.similarity is not None:
        line += f" sim={entry.similarity:.3f}"
    line += f"  [{entry.decided_by}]"
    # A step that both moved and changed reports as `modified`, because the
    # content change is what the reviewer must act on. Without this the move
    # would be invisible in the output even though the engine found it.
    if entry.also_reordered and entry.status.value != "reordered":
        line += f"  (also moved {entry.old_order}->{entry.new_order})"
    print(line)
    if entry.rationale:
        print(f"      {entry.rationale}")
    for change in entry.field_changes:
        print(f"      {change.field}: {_short(change.old)} -> {_short(change.new)}")
    for field in entry.preserved_edits:
        print(f"      KEPT your edit to '{field}'")


def _short(value: Any, limit: int = 60) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
