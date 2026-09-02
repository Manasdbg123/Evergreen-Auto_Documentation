"""Stages 6 and 7 — the two that can cost money.

These run offline. Nothing here calls Anthropic: the request is asserted
against a stub client, which is the point — the shape of a paid call is worth
testing precisely *because* exercising it for real costs money every time.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import Config, load_config  # noqa: E402
from app.llm import offline as offline_impl  # noqa: E402
from app.llm.anthropic_provider import (_NO_SAMPLING_PREFIXES,  # noqa: E402
                                        AnthropicProvider)
from app.llm.client import (ToolSpec, get_provider, image_block,  # noqa: E402
                            text_block)
from app.llm.gemini_provider import (GeminiUsage, _to_gemini_schema,  # noqa: E402
                                     GeminiProvider)
from app.llm.prompts import (detect_steps_tool_schema,  # noqa: E402
                             structure_tool_schema)
from app.models import (CandidateFrame, Confidence, Transcript,  # noqa: E402
                        TranscriptSegment)
from app.pipeline.detect_steps import DetectStepsStage  # noqa: E402
from app.pipeline.structure import StructureStage, _confidence, _ui_type  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


def candidate(order: int, ts: float, score: float, narration: str = "") -> CandidateFrame:
    return CandidateFrame(
        event_id=f"ev{order}", order=order, timestamp=ts,
        frame_path=f"frames/stable/s{order}.jpg",
        llm_image_path=f"frames/llm/c{order}.jpg",
        phash="0" * 16, magnitude=0.5, stability=0.99, score=score,
        transcript_text=narration,
    )


# --------------------------------------------------------------------------
# Schemas
#
# `strict: true` only validates what the schema actually constrains. A missing
# `additionalProperties: false` or an incomplete `required` list silently
# reopens the door to free-form output, which the diff engine cannot compare.
# --------------------------------------------------------------------------


def walk_objects(schema: dict):
    if schema.get("type") == "object":
        yield schema
    for value in schema.get("properties", {}).values():
        yield from walk_objects(value)
    if "items" in schema:
        yield from walk_objects(schema["items"])


@pytest.mark.parametrize("schema", [detect_steps_tool_schema(), None])
def test_every_object_is_closed_and_fully_required(schema, cfg):
    schema = schema or structure_tool_schema(cfg)
    for obj in walk_objects(schema):
        assert obj.get("additionalProperties") is False, obj
        assert set(obj.get("required", [])) == set(obj["properties"]), obj


def test_ui_element_enum_tracks_the_model(cfg):
    """The prompt's control types come off models.UiElement, not a copy.

    A hand-maintained duplicate drifts, and the failure is silent: the model
    returns a type the Step model then coerces to "other", so every diff sees
    a changed ui_element.type on steps nobody touched.
    """
    from app.models import UiElement

    schema = structure_tool_schema(cfg)
    enum = schema["properties"]["steps"]["items"]["properties"]["ui_element"] \
        ["properties"]["type"]["enum"]
    assert enum == list(UiElement.model_fields["type"].annotation.__args__)


# --------------------------------------------------------------------------
# Request assembly
# --------------------------------------------------------------------------


class StubMessages:
    def __init__(self, reply):
        self.reply = reply
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return self.reply


def stub_client(cfg, payload: dict) -> tuple[AnthropicProvider, StubMessages]:
    reply = SimpleNamespace(
        stop_reason="tool_use",
        usage=SimpleNamespace(input_tokens=100, output_tokens=50,
                              cache_read_input_tokens=0),
        content=[SimpleNamespace(type="tool_use", name="t", input=payload)],
    )
    messages = StubMessages(reply)
    client = AnthropicProvider.__new__(AnthropicProvider)
    client.cfg = cfg
    client.cost_log = None
    client._reason = None
    client._client = SimpleNamespace(messages=messages)
    return client, messages


def anthropic_cfg(cfg: Config) -> Config:
    """The same config with Anthropic active — the provider tests below assert
    Anthropic-specific request shape regardless of which vendor is configured."""
    return cfg.merged_with({"llm": {"provider": "anthropic"}})


TOOL = ToolSpec(name="t", description="d", input_schema={
    "type": "object", "additionalProperties": False,
    "required": ["ok"], "properties": {"ok": {"type": "boolean"}}})


def test_response_is_forced_through_the_tool(cfg):
    client, messages = stub_client(cfg, {"ok": True})
    out = client.structured(stage="s", model="claude-haiku-4-5-20251001",
                            system="sys", content=[], tool=TOOL)

    assert out == {"ok": True}
    assert messages.kwargs["tool_choice"] == {"type": "tool", "name": "t"}
    assert messages.kwargs["tools"][0]["strict"] is True


def test_system_prompt_is_marked_cacheable(cfg):
    """It is byte-identical across jobs run with the same config, so it is the
    one part of these requests worth caching."""
    client, messages = stub_client(cfg, {"ok": True})
    client.structured(stage="s", model="claude-haiku-4-5-20251001",
                      system="sys", content=[], tool=TOOL)
    assert messages.kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_temperature_is_omitted_for_models_that_reject_it(cfg):
    """Sonnet 5 returns 400 on `temperature` rather than ignoring it, so
    sending the configured value would fail every structure call."""
    assert "claude-sonnet-5".startswith(_NO_SAMPLING_PREFIXES)
    assert not "claude-haiku-4-5-20251001".startswith(_NO_SAMPLING_PREFIXES)

    client, messages = stub_client(cfg, {"ok": True})
    client.structured(stage="s", model="claude-sonnet-5", system="sys",
                      content=[], tool=TOOL)
    assert "temperature" not in messages.kwargs

    client, messages = stub_client(cfg, {"ok": True})
    client.structured(stage="s", model="claude-haiku-4-5-20251001", system="sys",
                      content=[], tool=TOOL)
    assert messages.kwargs["temperature"] == cfg.models.temperature


def test_missing_key_is_reported_not_raised_at_construction(cfg, monkeypatch):
    """Construction must never raise: the caller inspects `.available` and
    chooses the offline path, which is what keeps the pipeline runnable."""
    for env in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env, raising=False)

    for provider, expected in [("anthropic", "ANTHROPIC_API_KEY"),
                               ("gemini", "GEMINI_API_KEY")]:
        client = get_provider(cfg.merged_with({"llm": {"provider": provider}}))
        assert not client.available
        assert expected in client.unavailable_reason


# --------------------------------------------------------------------------
# Provider parity
# --------------------------------------------------------------------------


def test_the_active_provider_decides_the_model_ids(cfg):
    """Switching vendor is a config line. No stage reads `llm.provider`, so
    this resolution is the only thing that has to be right."""
    anthropic = cfg.merged_with({"llm": {"provider": "anthropic"}})
    gemini = cfg.merged_with({"llm": {"provider": "gemini"}})

    assert anthropic.models.classify.startswith("claude-")
    assert gemini.models.classify.startswith("gemini-")
    assert anthropic.key_env_var == "ANTHROPIC_API_KEY"
    assert gemini.key_env_var == "GEMINI_API_KEY"


def test_every_model_in_play_has_a_price(cfg):
    """An unpriced model logs $0.00 per call, so the budget cap silently stops
    protecting anything — the failure is invisible until the bill arrives."""
    for provider, block in cfg.llm.providers.items():
        for role in ("classify", "structure", "judge"):
            model = getattr(block, role)
            assert model in cfg.cost.pricing, f"{provider}.{role} = {model}"


def test_gemini_schema_drops_only_additional_properties(cfg):
    """Gemini 400s on `additionalProperties`. Narrowing is safe only because
    its constrained decoding cannot emit a key the schema does not name —
    nothing that constrains the shape of the answer may be relaxed."""
    original = structure_tool_schema(cfg)
    narrowed = _to_gemini_schema(original)

    for obj in walk_objects(narrowed):
        assert "additionalProperties" not in obj
    # Everything that shapes the answer survives.
    old_steps = original["properties"]["steps"]["items"]
    new_steps = narrowed["properties"]["steps"]["items"]
    assert new_steps["required"] == old_steps["required"]
    assert (new_steps["properties"]["ui_element"]["properties"]["type"]["enum"]
            == old_steps["properties"]["ui_element"]["properties"]["type"]["enum"])


def test_gemini_thought_tokens_are_billed_as_output():
    """Measured: 434 thought tokens to produce a 14-token classification. Left
    uncounted, cost.jsonl under-reports by more than the budget it guards."""
    usage = GeminiUsage(SimpleNamespace(
        prompt_token_count=1000, candidates_token_count=50,
        thoughts_token_count=434, cached_content_token_count=0))

    assert usage.input_tokens == 1000
    assert usage.output_tokens == 484
    assert usage.thought_tokens == 434


def test_image_pricing_is_provider_specific(cfg):
    """The two vendors differ by ~5x on the same image, and images are ~88% of
    this pipeline's spend — one shared formula would misprice a run by more
    than the whole budget."""
    from app.llm.cost import capped_image_tokens

    anthropic = capped_image_tokens(cfg.merged_with({"llm": {"provider": "anthropic"}}))
    gemini = capped_image_tokens(cfg.merged_with({"llm": {"provider": "gemini"}}))

    assert anthropic > 1500          # 1568x882 / 750
    assert gemini < 400              # flat per-tile, measured at 259
    assert AnthropicProvider.image_tokens(cfg, 1280, 720) == 1229


def test_content_blocks_are_vendor_neutral(tmp_path):
    """Stages build these; each provider encodes them. An image stays a path
    so it is never base64-encoded for a vendor that wants raw bytes."""
    png = tmp_path / "shot.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    assert text_block("hi").kind == "text"
    block = image_block(png)
    assert block.kind == "image"
    assert block.path == png
    assert block.media_type == "image/png"


# --------------------------------------------------------------------------
# Reconciliation — what the model returns vs. what we sent
# --------------------------------------------------------------------------


def test_a_candidate_with_no_decision_is_rejected_not_fatal(cfg):
    """Failing the stage would discard a paid call over one omitted entry."""
    candidates = [candidate(1, 1.0, 0.9), candidate(2, 2.0, 0.8)]
    stage = DetectStepsStage(cfg, offline=True)
    out = stage._to_models([{"candidate_id": candidates[0].candidate_id,
                             "is_step": True, "reason": "new screen"}], candidates)

    assert [d.is_step for d in out] == [True, False]
    assert "no decision returned" in out[1].reason


def test_orders_are_assigned_over_accepted_steps_only(cfg):
    candidates = [candidate(i, float(i), 0.9) for i in range(1, 4)]
    stage = DetectStepsStage(cfg, offline=True)
    out = stage._to_models([
        {"candidate_id": candidates[0].candidate_id, "is_step": True, "reason": ""},
        {"candidate_id": candidates[1].candidate_id, "is_step": False, "reason": ""},
        {"candidate_id": candidates[2].candidate_id, "is_step": True, "reason": ""},
    ], candidates)
    assert [d.order for d in out] == [1, None, 2]


# --------------------------------------------------------------------------
# The conflict rule
# --------------------------------------------------------------------------


def test_conflict_forces_low_confidence_whatever_the_model_claimed():
    """The rule is enforced in code, not left to the prompt.

    A step where narration and frame disagreed is precisely the step a
    reviewer must check, so its confidence cannot be the model's opinion.
    """
    assert _confidence("high", "narration said Save, button reads Submit") \
        == Confidence.low
    assert _confidence("high", "") == Confidence.high
    assert _confidence("nonsense", "") == Confidence.medium


def test_unknown_ui_type_degrades_to_other():
    assert _ui_type("button") == "button"
    assert _ui_type("carousel") == "other"
    assert _ui_type(None) == "other"


def test_conflict_is_carried_into_step_meta(cfg, tmp_path):
    stage = StructureStage(cfg, offline=True)
    job = _fake_job(cfg, tmp_path)
    confirmed = [candidate(1, 1.0, 0.9)]
    raw = {"title": "T", "summary": "S", "steps": [{
        "candidate_id": confirmed[0].candidate_id,
        "title": "Save the form", "instruction": "Click Submit.",
        "ui_element": {"type": "button", "label": "Submit", "location_hint": ""},
        "expected_result": "Saved.", "prerequisites": [], "confidence": "high",
        "conflict": "narration said Save; the button reads Submit",
    }]}

    sop = stage._to_sop(job, raw, confirmed, {}, Transcript())
    assert sop.steps[0].meta.conflict
    assert sop.steps[0].confidence == Confidence.low


def test_provenance_comes_from_our_records_not_the_response(cfg, tmp_path):
    """A step must always be traceable to the second of video that produced
    it, even when the model garbles the candidate_id it was given."""
    stage = StructureStage(cfg, offline=True)
    job = _fake_job(cfg, tmp_path)
    confirmed = [candidate(1, 12.5, 0.9)]
    raw = {"title": "T", "summary": "", "steps": [{
        "candidate_id": "cand_hallucinated",
        "title": "Do the thing", "instruction": "Click it.",
        "ui_element": {"type": "button", "label": "Go", "location_hint": ""},
        "expected_result": "", "prerequisites": [], "confidence": "high",
        "conflict": "",
    }]}

    sop = stage._to_sop(job, raw, confirmed, {}, Transcript())
    assert sop.steps[0].meta.source_frame_ts == 12.5
    assert sop.steps[0].meta.candidate_id == confirmed[0].candidate_id


def _fake_job(cfg, tmp_path):
    from app.pipeline.base import JobPaths

    job = JobPaths(cfg, "tmp_job")
    job.root = tmp_path
    job.screenshots = tmp_path / "screenshots"
    return job


# --------------------------------------------------------------------------
# The offline path
# --------------------------------------------------------------------------


def test_offline_never_invents_a_ui_label(cfg):
    """It cannot read a screenshot, and a guessed label would reach the diff
    engine indistinguishable from a real one and be reported as a UI change."""
    confirmed = [candidate(1, 1.0, 0.9, "Now click the big blue Save button.")]
    out = offline_impl.structure(cfg, confirmed, Transcript())

    assert out["steps"][0]["ui_element"]["label"] == ""
    assert out["steps"][0]["confidence"] == "low"
    assert out["steps"][0]["instruction"].startswith("[offline]")


def test_offline_output_validates_against_the_real_schema(cfg, tmp_path):
    """Whatever works on the offline path must work unchanged with a key."""
    confirmed = [candidate(i, float(i), 0.9) for i in range(1, 4)]
    raw = offline_impl.structure(cfg, confirmed, Transcript())
    sop = StructureStage(cfg, offline=True)._to_sop(
        _fake_job(cfg, tmp_path), raw, confirmed, {}, Transcript())

    assert len(sop.steps) == 3
    assert [s.order for s in sop.steps] == [1, 2, 3]
    assert json.loads(sop.model_dump_json())["steps"][0]["confidence"] == "low"


def test_offline_titles_come_from_narration_when_there_is_any(cfg):
    transcript = Transcript(available=True, segments=[
        TranscriptSegment(start=0, end=2, text="Submitting an expense claim.")])
    confirmed = [candidate(1, 1.0, 0.9, "First we open the dashboard. Then we wait.")]
    out = offline_impl.structure(cfg, confirmed, transcript)

    assert out["steps"][0]["title"] == "First we open the dashboard."
    assert out["title"] == "Submitting an expense claim"
