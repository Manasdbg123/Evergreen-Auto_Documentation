"""Token accounting and cost estimation.

Two jobs:
  * `estimate_job` predicts what a video of a given length will cost *before*
    you spend anything, so budget decisions are made up front.
  * `CostLog` records what each call actually cost, to cost.jsonl and stdout.

Image tokens follow Anthropic's documented approximation, tokens = w*h/750.
Every image is capped at frames.llm_max_edge_px before sending, so a 16:9
frame costs at most 1568*882/750 = 1,845 tokens no matter the source
resolution. Images dominate this pipeline's spend, which is why the candidate
cap in stage 5 is the single most important cost control in the project.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import Config


def image_tokens(width: int, height: int) -> int:
    """Anthropic's documented approximation for vision input."""
    return int(round(width * height / 750))


def capped_image_tokens(cfg: Config, aspect: float = 16 / 9) -> int:
    """Tokens for one candidate frame after the long-edge cap is applied."""
    long_edge = cfg.frames.llm_max_edge_px
    if aspect >= 1:
        w, h = long_edge, int(long_edge / aspect)
    else:
        w, h = int(long_edge * aspect), long_edge
    return image_tokens(w, h)


def price(cfg: Config, model: str, tokens_in: int, tokens_out: int) -> float:
    p = cfg.cost.pricing.get(model)
    if p is None:
        return 0.0
    return tokens_in / 1_000_000 * p.input + tokens_out / 1_000_000 * p.output


# --------------------------------------------------------------------------
# Up-front estimation
# --------------------------------------------------------------------------


@dataclass
class StageEstimate:
    stage: str
    model: str
    images: int
    tokens_in: int
    tokens_out: int
    usd: float


def estimate_job(
    cfg: Config,
    minutes: float,
    *,
    candidates: int | None = None,
    steps: int | None = None,
    has_transcript: bool = True,
    aspect: float = 16 / 9,
) -> list[StageEstimate]:
    """Predicted spend for one video, stage by stage.

    Frame sampling, change detection and transcription are local and free;
    they do not appear here. Only the three LLM stages cost money.
    """
    img = capped_image_tokens(cfg, aspect)

    # Candidate count is capped by config regardless of video length — that is
    # the whole point of stage 5. A longer video costs more only until it hits
    # the cap, then it costs a flat rate.
    n_cand = candidates if candidates is not None else cfg.candidates.max_frames
    n_cand = min(n_cand, cfg.candidates.max_frames)

    # Roughly 55% of candidates survive classification as genuine steps;
    # the rest are alt-tabs, transitions and revisits.
    n_steps = steps if steps is not None else max(int(round(n_cand * 0.55)), 1)

    # ~150 spoken words per minute, ~1.33 tokens per word.
    transcript_tokens = int(minutes * 150 * 1.33) if has_transcript else 0

    out: list[StageEstimate] = []

    # Stage 6 — step detection / noise rejection. Every candidate is seen.
    detect_in = n_cand * img + 900 + transcript_tokens + n_cand * 20
    detect_out = n_cand * DETECT_TOKENS_PER_CANDIDATE
    out.append(StageEstimate(
        "detect_steps", cfg.models.classify, n_cand,
        detect_in, detect_out,
        price(cfg, cfg.models.classify, detect_in, detect_out),
    ))

    # Stage 7 — writing the SOP. Only confirmed steps are re-sent, with vision.
    struct_in = n_steps * img + 1400 + transcript_tokens
    struct_out = n_steps * step_output_tokens(cfg) + SOP_HEADER_TOKENS
    out.append(StageEstimate(
        "structure", cfg.models.structure, n_steps,
        struct_in, struct_out,
        price(cfg, cfg.models.structure, struct_in, struct_out),
    ))

    return out


#: Per candidate: is_step, order, a one-line reason, provisional_title.
DETECT_TOKENS_PER_CANDIDATE = 55

#: sop title + summary + JSON envelope.
SOP_HEADER_TOKENS = 120


def step_output_tokens(cfg: Config) -> int:
    """Output tokens for one Step object, derived from the actual schema.

    Output is billed at 5x input on both models, so this is worth deriving
    rather than guessing. Field by field, at ~1.33 tokens per word:

        title            ~6 words                        8
        instruction      capped by writing config        varies
        ui_element       type + label + location_hint   25
        expected_result  ~18 words                      24
        prerequisites    ~10 words                      13
        confidence, step_id, order, screenshot_ref,
        meta, and JSON punctuation                      40
    """
    instruction = int(cfg.writing.max_instruction_words * 1.33)
    return 8 + instruction + 25 + 24 + 13 + 40


def estimate_diff(cfg: Config, steps: int, *, ambiguous: int | None = None,
                  visual: int | None = None, aspect: float = 16 / 9) -> list[StageEstimate]:
    """Predicted spend for one diff.

    Tiers 1 and 2 — lexical prefilter and local MiniLM embeddings — run
    offline and cost nothing. Only the ambiguous band reaches the LLM.
    """
    img = capped_image_tokens(cfg, aspect)
    out: list[StageEstimate] = []

    # Text adjudication of pairs the offline tiers could not settle.
    n_amb = ambiguous if ambiguous is not None else max(int(round(steps * 0.35)), 1)
    judge_in = n_amb * (2 * 140 + 250)
    judge_out = n_amb * 60
    out.append(StageEstimate(
        "diff_judge", cfg.models.judge, 0, judge_in, judge_out,
        price(cfg, cfg.models.judge, judge_in, judge_out),
    ))

    # Visual fallback, hard-capped. Only fires when text stays ambiguous.
    n_vis = visual if visual is not None else 0
    n_vis = min(n_vis, cfg.diff.max_visual_comparisons)
    if n_vis:
        vis_in = n_vis * (2 * img + 300)
        vis_out = n_vis * 80
        out.append(StageEstimate(
            "diff_visual", cfg.models.structure, n_vis * 2, vis_in, vis_out,
            price(cfg, cfg.models.structure, vis_in, vis_out),
        ))
    return out


def format_estimate(cfg: Config, rows: list[StageEstimate], title: str) -> str:
    """Table with input and output priced separately.

    Output is 5x input per token on both models, so the split is worth
    seeing: it is what tells you whether to tune image count or verbosity.
    """
    lines = [
        f"  {title}",
        f"  {'stage':<14}{'model':<26}{'imgs':>5}{'tok in':>9}{'$ in':>8}"
        f"{'tok out':>9}{'$ out':>8}{'$ total':>9}",
        f"  {'-' * 88}",
    ]
    t_in = t_out = 0.0
    for r in rows:
        p = cfg.cost.pricing.get(r.model)
        in_usd = r.tokens_in / 1_000_000 * p.input if p else 0.0
        out_usd = r.tokens_out / 1_000_000 * p.output if p else 0.0
        t_in += in_usd
        t_out += out_usd
        lines.append(
            f"  {r.stage:<14}{r.model:<26}{r.images:>5}{r.tokens_in:>9,}{in_usd:>8.4f}"
            f"{r.tokens_out:>9,}{out_usd:>8.4f}{r.usd:>9.4f}"
        )
    total = t_in + t_out
    share = f"{t_in / total * 100:.0f}% in / {t_out / total * 100:.0f}% out" if total else ""
    lines.append(f"  {'-' * 88}")
    lines.append(f"  {'TOTAL':<45}{'':>9}{t_in:>8.4f}{'':>9}{t_out:>8.4f}{total:>9.4f}   {share}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Actual spend, recorded per call
# --------------------------------------------------------------------------


@dataclass
class CallCost:
    stage: str
    model: str
    tokens_in: int
    tokens_out: int
    cache_read: int
    usd: float
    at: float


class CostLog:
    """Appends every call to cost.jsonl and enforces the per-job budget."""

    def __init__(self, cfg: Config, path: Path):
        self.cfg = cfg
        self.path = path
        self.calls: list[CallCost] = []

    @property
    def total_usd(self) -> float:
        return sum(c.usd for c in self.calls)

    def record(self, stage: str, model: str, usage) -> CallCost:
        tin = getattr(usage, "input_tokens", 0) or 0
        tout = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        entry = CallCost(
            stage=stage, model=model, tokens_in=tin, tokens_out=tout,
            cache_read=cache_read, usd=price(self.cfg, model, tin, tout),
            at=time.time(),
        )
        self.calls.append(entry)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(asdict(entry)) + "\n")

        print(
            f"[cost] {stage:<14} {model:<28} in={tin:,} out={tout:,} "
            f"${entry.usd:.4f}  (job total ${self.total_usd:.4f})"
        )
        self._check_budget()
        return entry

    def _check_budget(self) -> None:
        total = self.total_usd
        if total >= self.cfg.cost.max_usd_per_job:
            raise BudgetExceeded(
                f"Job spend ${total:.4f} hit the cap of "
                f"${self.cfg.cost.max_usd_per_job:.2f} (cost.max_usd_per_job). "
                f"Aborting rather than overrunning the budget."
            )
        if total >= self.cfg.cost.warn_usd_per_job:
            print(f"[cost] WARNING: ${total:.4f} of ${self.cfg.cost.max_usd_per_job:.2f} budget used")


class BudgetExceeded(RuntimeError):
    pass
