"""Tier 3 of the diff — the only part of the diff that costs money.

`similarity.use_llm_judge` was true and `DiffEngine` accepted a `judge`
callable long before anything constructed one, so every ambiguous pair silently
fell through to the offline score. This module is what makes that config line
true.

Two judges, deliberately separate, because the diff asks two questions:

**Identity** — "are these the same step?" — decides which v1 step pairs with
which v2 step. Called at most `diff.max_llm_judgements` times, only for pairs
the offline tiers left inside the ambiguous band.

**Prose** — "do these two sentences say the same thing?" — decides whether a
paired step actually *changed*. One batched call for the whole document,
because encoding the question costs far more than the answers do.

The prose judge is the one that fixes the measured defect. On the two real
generated SOPs, `expected_result` "The Dashboard screen appears." versus "You
are redirected to the Dashboard." scored below `field_rewrite_threshold` and
the step was reported `modified` when nothing had changed. No threshold on the
offline score can fix that: measured on the fixtures, same-meaning rewordings
scored 0.550-0.937 and genuinely different pairs 0.106-0.808, so the bands
overlap and any cut misclassifies something. Only a reader can separate them.

Both judges degrade rather than fail. If the provider is unavailable, a call
errors, or the cap is reached, the caller keeps the offline score — a blunter
diff, never a broken one.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..models import Step
from .client import LLMProvider, ToolSpec, text_block
from .cost import CostLog
from .prompts import (judge_identity_system, judge_identity_tool_schema,
                      judge_identity_user, judge_prose_system,
                      judge_prose_tool_schema, judge_prose_user)


class DiffJudge:
    """Both judges, sharing one provider and one cost log."""

    def __init__(self, cfg: Config, provider: LLMProvider,
                 cost_log: CostLog | None = None):
        self.cfg = cfg
        self.provider = provider
        self.cost_log = cost_log
        self.identity_calls = 0
        self.prose_calls = 0

    # ------------------------------------------------------------------
    # Identity — "same step?"
    # ------------------------------------------------------------------

    def identity(self, old: Step, new: Step) -> tuple[float, str]:
        """Returns (score, rationale) on the same 0-1 scale as the offline tiers.

        The verdict is mapped to a score rather than returned as a boolean so
        that `DiffEngine` needs no special case: a "same" verdict lands above
        the ambiguous band and a "different" verdict below the match threshold,
        and every downstream rule works unchanged.
        """
        if self.identity_calls >= self.cfg.diff.max_llm_judgements:
            raise JudgeUnavailable(
                f"identity judge cap reached "
                f"({self.cfg.diff.max_llm_judgements}, diff.max_llm_judgements)"
            )

        raw = self.provider.structured(
            stage="diff_judge",
            model=self.cfg.models.judge,
            system=judge_identity_system(),
            content=[text_block(judge_identity_user(
                _judgeable(old), _judgeable(new)))],
            tool=ToolSpec(
                name="compare_steps",
                description="Decide whether two steps are the same step of the "
                            "workflow.",
                input_schema=judge_identity_tool_schema(),
            ),
            max_tokens=512,
        )
        self.identity_calls += 1

        verdict = str(raw.get("verdict", "")).lower()
        confidence = _clamp(raw.get("confidence"))
        reason = str(raw.get("reason", "")).strip()

        lo, hi = self.cfg.diff.ambiguous_band
        if verdict == "same":
            # Above the band, so the pair matches and the field comparison
            # decides unchanged vs modified.
            score = hi + (1.0 - hi) * confidence
        else:
            # Below the match threshold, so the pair breaks into removed+added.
            score = max(0.0, (lo - 0.01) * (1.0 - confidence))
        return score, f"judge: {verdict} ({confidence:.2f}) — {reason}"

    # ------------------------------------------------------------------
    # Prose — "same meaning?"
    # ------------------------------------------------------------------

    def prose(self, items: list[dict[str, str]]) -> dict[str, bool]:
        """Batch-adjudicate ambiguous prose fields. Returns {id: same_meaning}.

        One call for every ambiguous field in the document. Batching is not a
        micro-optimisation: the system prompt is several hundred tokens and the
        answers are a dozen each, so per-field calls would cost roughly the
        number-of-fields times as much for identical output.
        """
        if not items:
            return {}

        raw = self.provider.structured(
            stage="diff_judge_prose",
            model=self.cfg.models.judge,
            system=judge_prose_system(),
            content=[text_block(judge_prose_user(items))],
            tool=ToolSpec(
                name="compare_wording",
                description="Decide, for each pair, whether both sentences "
                            "assert the same fact.",
                input_schema=judge_prose_tool_schema(),
            ),
        )
        self.prose_calls += 1

        out: dict[str, bool] = {}
        for entry in raw.get("verdicts") or []:
            if isinstance(entry, dict) and entry.get("id"):
                out[str(entry["id"])] = bool(entry.get("same_meaning"))
        return out


class JudgeUnavailable(RuntimeError):
    """The judge cannot answer. The caller keeps its offline score."""


def build_judge(cfg: Config, cost_log: CostLog | None = None) -> DiffJudge | None:
    """Construct the judge, or return None if it cannot run.

    None is a normal outcome, not an error: no API key, `use_llm_judge` off, or
    an offline run all land here, and the diff is expected to carry on with the
    offline tiers.
    """
    if not cfg.similarity.use_llm_judge:
        print("[diff] LLM judge disabled (similarity.use_llm_judge) — offline tiers only")
        return None
    if cfg.llm.offline == "always":
        print("[diff] llm.offline is 'always' — judge skipped, offline tiers only")
        return None

    from .client import get_provider

    provider = get_provider(cfg, cost_log)
    if not provider.available:
        if cfg.llm.offline == "never":
            raise RuntimeError(
                f"[diff] {provider.unavailable_reason}, and llm.offline is "
                f"'never'. The diff would silently lose its adjudication tier."
            )
        print(f"[diff] {provider.unavailable_reason} — judge unavailable, "
              f"offline tiers only")
        return None

    print(f"[diff] LLM judge active: {provider.name}/{cfg.models.judge}")
    return DiffJudge(cfg, provider, cost_log)


def _judgeable(step: Step) -> dict[str, Any]:
    """What the identity judge is shown — identity fields only.

    `expected_result` is withheld, for the same measured reason it is excluded
    from `Step.similarity_text`: it describes a step's *consequence*, which
    changes when the workflow changes around the step, without the step being a
    different step.

    This is not theoretical. Shown the full `diff_payload`, the judge ruled that
    v1 "Enter expense details / Save" and v2 "Enter expense details / Submit"
    were DIFFERENT steps — identical titles, near-identical instructions — and
    the single most important case in the demo regressed to remove + add. The
    two fields that differed were `ui_element.label` (the rename we exist to
    report) and `expected_result` (changed because the step that follows it
    changed). Both are change signals. Given both at once, the judge weighed
    them as evidence of identity and reached the exact conclusion the product
    must never reach.

    `ui_element.label` is kept, because the prompt names renames explicitly as
    a "same step" case and a label match is real positive evidence when it
    holds. `expected_result` has no such upside: two steps that are genuinely
    the same are not made more recognisable by it.

    Provenance, ids, order and confidence are withheld too — they say where a
    step came from, not what it is, and a judge shown `order` answers with
    position instead of content.
    """
    payload = step.diff_payload()
    payload.pop("expected_result", None)
    return payload


def _clamp(value: Any, default: float = 0.5) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default
