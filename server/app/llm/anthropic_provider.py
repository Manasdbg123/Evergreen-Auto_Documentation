"""Anthropic. The provider the brief specifies, and the documented default.

Structured output is a forced tool call with `strict: true`: the arguments
validate against the schema or the request fails, so no stage has to defend
against a half-parsed step.
"""

from __future__ import annotations

import base64
from typing import Any

from ..config import Config, provider_key
from .client import Block, LLMProvider, ToolSpec, missing_key_reason
from .cost import CostLog

#: Models that reject sampling parameters outright (HTTP 400) rather than
#: ignoring them. Sending `temperature` to Sonnet 5 fails the whole request,
#: so the config value is applied only where it is accepted. Checked by prefix
#: because these are families, not single ids.
_NO_SAMPLING_PREFIXES = ("claude-sonnet-5", "claude-opus-5", "claude-opus-4-7",
                         "claude-opus-4-8", "claude-fable-5", "claude-mythos-5")


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, cfg: Config, cost_log: CostLog | None = None):
        super().__init__(cfg, cost_log)
        self._client = None

        self._reason = missing_key_reason(cfg)
        if self._reason:
            return
        try:
            import anthropic
        except ImportError:
            self._reason = "the `anthropic` package is not installed"
            return
        self._client = anthropic.Anthropic(api_key=provider_key(cfg))

    # ------------------------------------------------------------------

    def structured(
        self, *, stage: str, model: str, system: str, content: list[Block],
        tool: ToolSpec, max_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.require(stage)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens or self.cfg.models.max_tokens,
            # Marked cacheable: identical for every job run with the same
            # config, so a second video within the cache window pays ~10% for
            # this prefix instead of full price.
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": [_encode(b) for b in content]}],
            "tools": [{
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "strict": True,
            }],
            "tool_choice": {"type": "tool", "name": tool.name},
        }
        if not model.startswith(_NO_SAMPLING_PREFIXES):
            kwargs["temperature"] = self.cfg.models.temperature

        response = self._call(stage, model, kwargs)

        for block in response.content:
            if block.type == "tool_use" and block.name == tool.name:
                # Always dict-ify rather than string-matching: escaping inside
                # tool arguments varies by model and is not stable to parse.
                return dict(block.input)

        raise RuntimeError(
            f"[{stage}] {model} returned no '{tool.name}' tool call "
            f"(stop_reason={response.stop_reason})"
        )

    @staticmethod
    def image_tokens(cfg: Config, width: int, height: int) -> int:
        """Anthropic's documented approximation, tokens = w*h/750."""
        return int(round(width * height / 750))

    # ------------------------------------------------------------------

    def _call(self, stage: str, model: str, kwargs: dict[str, Any]):
        import anthropic

        try:
            response = self._client.messages.create(**kwargs)
        except anthropic.NotFoundError as exc:
            raise RuntimeError(
                f"[{stage}] model '{model}' was rejected as unknown. Check "
                f"llm.providers.anthropic in config.yaml — current ids carry no "
                f"date suffix (e.g. 'claude-haiku-4-5', not "
                f"'claude-haiku-4-5-20251001')."
            ) from exc
        except anthropic.AuthenticationError as exc:
            raise RuntimeError(f"[{stage}] {self.cfg.key_env_var} was rejected.") from exc
        except anthropic.RateLimitError as exc:
            raise RuntimeError(
                f"[{stage}] rate limited after the SDK's own retries."
            ) from exc

        if response.stop_reason == "refusal":
            raise RuntimeError(f"[{stage}] {model} declined the request.")

        if self.cost_log is not None:
            self.cost_log.record(stage, model, response.usage)
        return response


def _encode(block: Block) -> dict[str, Any]:
    if block.kind == "text":
        return {"type": "text", "text": block.text}
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": block.media_type,
            "data": base64.standard_b64encode(block.path.read_bytes()).decode(),
        },
    }
