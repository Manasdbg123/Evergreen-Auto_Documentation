"""Google Gemini.

Structured output here is constrained decoding (`response_schema`) rather than
a forced tool call. The guarantee is the same — the response validates against
the schema — but three vendor differences have to be absorbed so that no stage
ever learns about them:

**The schema dialect is narrower.** Gemini rejects `additionalProperties`
outright (400, "Cannot find field"). `_to_gemini_schema` strips it. That is
safe in only one direction: Gemini's constrained decoding cannot emit a key the
schema does not name, so the closure `additionalProperties: false` buys on
Anthropic is already implicit here.

**Thought tokens are billed as output.** Gemini 2.5+ reasons by default and
`usage_metadata` reports thoughts separately from the answer. Measured on a
trivial classification: 434 thought tokens for a 14-token reply — the reasoning
cost 30x the response. Left uncounted, `cost.jsonl` would under-report spend by
more than the budget it exists to protect, so thoughts are added to output
tokens, and `models.thinking_budget` can switch them off.

**Images are priced completely differently.** 1280x720 costs 259 tokens on
gemini-2.5-flash and 1101 on gemini-3.5-flash, against ~1229 under Anthropic's
w*h/750. Since images are ~88% of this pipeline's spend, that single number
moves the per-document cost by an order of magnitude.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Config, provider_key
from .client import Block, LLMProvider, ToolSpec, missing_key_reason
from .cost import CostLog

#: Input tokens per image, measured with `count_tokens` on a 1280x720 frame.
#: Gemini bills images per tile rather than per pixel, so this is flat across
#: the sizes this pipeline produces (everything is capped at 1568px).
#: Overridable via `cost.image_tokens` in config.yaml.
DEFAULT_IMAGE_TOKENS = 259


class GeminiUsage:
    """Gemini's usage numbers in the shape `CostLog.record` expects.

    Exists so that cost accounting has exactly one code path. The important
    line is `output_tokens`: thoughts are billed as output, so they are counted
    as output.
    """

    def __init__(self, raw: Any):
        self.input_tokens = getattr(raw, "prompt_token_count", 0) or 0
        answer = getattr(raw, "candidates_token_count", 0) or 0
        self.thought_tokens = getattr(raw, "thoughts_token_count", 0) or 0
        self.output_tokens = answer + self.thought_tokens
        self.cache_read_input_tokens = getattr(raw, "cached_content_token_count", 0) or 0


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, cfg: Config, cost_log: CostLog | None = None):
        super().__init__(cfg, cost_log)
        self._client = None

        self._reason = missing_key_reason(cfg)
        if self._reason:
            return
        try:
            from google import genai
        except ImportError:
            self._reason = "the `google-genai` package is not installed"
            return
        self._client = genai.Client(api_key=provider_key(cfg))

    # ------------------------------------------------------------------

    def structured(
        self, *, stage: str, model: str, system: str, content: list[Block],
        tool: ToolSpec, max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.require(stage)
        from google.genai import types

        config: dict[str, Any] = {
            "system_instruction": system,
            "response_mime_type": "application/json",
            "response_schema": _to_gemini_schema(tool.input_schema),
            "temperature": self.cfg.models.temperature,
            "max_output_tokens": max_tokens or self.cfg.models.max_tokens,
        }
        budget = self.cfg.models.thinking_budget
        if budget is not None:
            config["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)

        parts = [_encode(types, b) for b in content]
        response = self._call(stage, model, parts, config)

        text = (response.text or "").strip()
        if not text:
            raise RuntimeError(
                f"[{stage}] {model} returned no content "
                f"(finish_reason={_finish_reason(response)}). If this says "
                f"MAX_TOKENS, raise models.max_tokens or lower "
                f"models.thinking_budget."
            )
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"[{stage}] {model} returned malformed JSON despite a response "
                f"schema (finish_reason={_finish_reason(response)}): {text[:200]}"
            ) from exc

        if not isinstance(parsed, dict):
            raise RuntimeError(f"[{stage}] expected a JSON object, got {type(parsed)}")
        return parsed

    @staticmethod
    def image_tokens(cfg: Config, width: int, height: int) -> int:
        """Flat per-image cost — Gemini tiles rather than scaling by pixel."""
        table = cfg.cost.image_tokens or {}
        return int(table.get(cfg.models.structure, DEFAULT_IMAGE_TOKENS))

    # ------------------------------------------------------------------

    def _call(self, stage: str, model: str, parts, config: dict[str, Any]):
        from google.genai import errors

        try:
            response = self._client.models.generate_content(
                model=model, contents=parts, config=config)
        except errors.ClientError as exc:
            raise RuntimeError(_client_error_message(stage, model, self.cfg, exc)) from exc

        if self.cost_log is not None:
            usage = GeminiUsage(response.usage_metadata)
            entry = self.cost_log.record(stage, model, usage)
            if usage.thought_tokens:
                print(f"[cost] {stage:<14} of which {usage.thought_tokens:,} were "
                      f"thought tokens, billed as output "
                      f"(models.thinking_budget: 0 disables them)")
            del entry
        return response


def _client_error_message(stage: str, model: str, cfg: Config, exc) -> str:
    """Turn Google's 400/404/429 wall of JSON into one actionable line."""
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return (f"[{stage}] {model} is out of quota. Free-tier keys have low "
                f"limits and no access to the Pro models — pick a Flash model "
                f"in llm.providers.gemini, or enable billing.")
    if "no longer available" in text or "404" in text:
        return (f"[{stage}] model '{model}' is not available to this key. "
                f"Check llm.providers.gemini in config.yaml.")
    if "API_KEY_INVALID" in text or "401" in text or "403" in text:
        return f"[{stage}] {cfg.key_env_var} was rejected by Google."
    return f"[{stage}] Gemini rejected the request: {text[:300]}"


def _finish_reason(response) -> str:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return "none"
    return str(getattr(candidates[0], "finish_reason", "unknown"))


def _encode(types, block: Block):
    if block.kind == "text":
        return types.Part.from_text(text=block.text)
    return types.Part.from_bytes(
        data=block.path.read_bytes(), mime_type=block.media_type)


def _to_gemini_schema(schema: Any) -> Any:
    """Narrow an Anthropic-dialect JSON Schema to what Gemini accepts.

    Only `additionalProperties` is dropped, and only because Gemini 400s on it.
    Nothing that constrains the *shape* of the answer is relaxed: `required`,
    `enum`, `type` and nesting all survive, so the object a stage receives is
    the same object either provider would have produced.
    """
    if isinstance(schema, list):
        return [_to_gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        key: _to_gemini_schema(value)
        for key, value in schema.items()
        if key != "additionalProperties"
    }
