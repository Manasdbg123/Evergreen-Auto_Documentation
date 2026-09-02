"""The one place this project talks to Anthropic.

Every call goes through `LLMClient.structured`, which forces the model to
answer through a tool with a strict JSON schema. Free-text parsing is not used
anywhere: the diff engine can only compare named fields, so a stage that
returned prose would break the product's core capability two stages later.

Three things this module owns:

* **Schema enforcement.** `strict: true` plus a forced `tool_choice` means the
  arguments we get back validate against the schema or the request fails —
  there is no half-parsed step to defend against downstream.
* **Cost accounting.** Nothing calls `messages.create` directly, so every token
  spent in this project lands in `cost.jsonl` and in the per-job budget check.
* **Degradation.** With no API key the client reports `available == False`
  rather than raising at import time, so the offline path (`llm/offline.py`)
  can take over and stages 1-4, the diff, the editor and export all still run.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config, anthropic_key
from .cost import CostLog

#: Models that reject sampling parameters outright (HTTP 400), rather than
#: ignoring them. Sending `temperature` to Sonnet 5 fails the whole request,
#: so the config value is applied only where it is accepted. Checked by prefix
#: because these are families, not single ids.
_NO_SAMPLING_PREFIXES = ("claude-sonnet-5", "claude-opus-5", "claude-opus-4-7",
                         "claude-opus-4-8", "claude-fable-5", "claude-mythos-5")


@dataclass
class ToolSpec:
    """A forced-response schema.

    `input_schema` must set `additionalProperties: false` and list every
    required key — that is what `strict` validates against.
    """

    name: str
    description: str
    input_schema: dict[str, Any]

    def as_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "strict": True,
        }


class LLMUnavailable(RuntimeError):
    """No API key, or the anthropic package is not installed."""


class LLMClient:
    def __init__(self, cfg: Config, cost_log: CostLog | None = None):
        self.cfg = cfg
        self.cost_log = cost_log
        self._client = None
        self._reason: str | None = None

        key = anthropic_key()
        if not key:
            self._reason = "ANTHROPIC_API_KEY is not set"
            return
        try:
            import anthropic
        except ImportError:
            self._reason = "the `anthropic` package is not installed"
            return
        self._client = anthropic.Anthropic(api_key=key)

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def unavailable_reason(self) -> str:
        return self._reason or ""

    # ------------------------------------------------------------------

    def structured(
        self,
        *,
        stage: str,
        model: str,
        system: str,
        content: list[dict[str, Any]],
        tool: ToolSpec,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """One call, one validated JSON object back.

        The system prompt is marked cacheable. It is identical for every job
        run with the same config, so a second video processed within the cache
        window pays ~10% for that prefix instead of full price.
        """
        if not self.available:
            raise LLMUnavailable(
                f"Cannot run stage '{stage}': {self._reason}. "
                f"Set ANTHROPIC_API_KEY, or run with --offline to exercise the "
                f"pipeline without any API calls."
            )

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self.cfg.models.max_tokens,
            "system": [{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            "messages": [{"role": "user", "content": content}],
            "tools": [tool.as_tool()],
            "tool_choice": {"type": "tool", "name": tool.name},
        }
        if not _rejects_sampling(model):
            kwargs["temperature"] = self.cfg.models.temperature

        response = self._call(stage, model, kwargs)

        for block in response.content:
            if block.type == "tool_use" and block.name == tool.name:
                # Always dict-ify rather than string-matching: escaping in tool
                # arguments varies by model and is not stable to parse by hand.
                return dict(block.input)

        raise RuntimeError(
            f"[{stage}] {model} returned no '{tool.name}' tool call "
            f"(stop_reason={response.stop_reason})"
        )

    def _call(self, stage: str, model: str, kwargs: dict[str, Any]):
        import anthropic

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.NotFoundError as exc:
            raise RuntimeError(
                f"[{stage}] model '{model}' was rejected as unknown. Check "
                f"models.* in config.yaml — current ids carry no date suffix "
                f"(e.g. 'claude-haiku-4-5', not 'claude-haiku-4-5-20251001')."
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise RuntimeError(f"[{stage}] ANTHROPIC_API_KEY was rejected.") from exc
        except anthropic.RateLimitError as exc:
            raise RuntimeError(
                f"[{stage}] rate limited after the SDK's own retries."
            ) from exc

        if response.stop_reason == "refusal":
            raise RuntimeError(f"[{stage}] {model} declined the request.")

        if self.cost_log is not None:
            self.cost_log.record(stage, model, response.usage)
        return response


def _rejects_sampling(model: str) -> bool:
    return model.startswith(_NO_SAMPLING_PREFIXES)


# --------------------------------------------------------------------------
# Content block helpers
# --------------------------------------------------------------------------


def image_block(path: Path) -> dict[str, Any]:
    """Base64 image block.

    The file is used as-is: `select_candidates` already wrote a copy capped at
    `frames.llm_max_edge_px`, so resizing here would be a second, lossier pass
    over an image that is already within budget.
    """
    media_type = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data = base64.standard_b64encode(path.read_bytes()).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}
