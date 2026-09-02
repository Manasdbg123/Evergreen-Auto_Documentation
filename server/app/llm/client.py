"""The vendor-neutral surface every stage talks to.

Stages never import a vendor SDK. They build neutral content blocks, describe
the JSON they want with a `ToolSpec`, and call `structured`. Which company
answers is `llm.provider` in config.yaml and nothing else — no stage branches
on it, and no prompt is written twice.

That indirection is not architecture for its own sake. Two things it buys:

* The pipeline outlives a vendor decision. Model availability, pricing and
  quota all changed under this project mid-build; none of it reached a stage.
* Cost accounting stays in one place. Whoever answers, the call is priced from
  `cost.pricing`, appended to `cost.jsonl` and checked against the per-job cap.

`structured` is the only entry point. There is no free-text call anywhere in
this project: the diff engine compares named fields, so a stage returning prose
would break the core capability two stages later.
"""

from __future__ import annotations

import mimetypes
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Config, provider_key
from .cost import CostLog


@dataclass
class ToolSpec:
    """The response schema, described once for every vendor.

    Written in the JSON Schema dialect Anthropic accepts — `additionalProperties`
    and complete `required` lists — because that is the stricter of the two.
    `GeminiProvider` narrows it on the way out; going the other direction would
    mean silently relaxing what the schema constrains.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class Block:
    """One piece of user content, before any vendor's encoding.

    Images stay as a path rather than a base64 string: Anthropic wants base64,
    Gemini wants raw bytes, and encoding eagerly would waste a third of the
    memory on whichever one is not in use.
    """

    kind: str  # "text" | "image"
    text: str = ""
    path: Path | None = None
    media_type: str = ""


def text_block(text: str) -> Block:
    return Block(kind="text", text=text)


def image_block(path: Path) -> Block:
    """The file is used as-is.

    `select_candidates` already wrote a copy capped at `frames.llm_max_edge_px`,
    so resizing here would be a second, lossier pass over an image that is
    already within budget.
    """
    return Block(
        kind="image",
        path=path,
        media_type=mimetypes.guess_type(path.name)[0] or "image/jpeg",
    )


class LLMUnavailable(RuntimeError):
    """No API key, or the vendor SDK is not installed."""


class LLMProvider(ABC):
    """What a vendor must supply to be usable by this pipeline."""

    #: Vendor id, matching `llm.provider`.
    name: str = "unnamed"

    def __init__(self, cfg: Config, cost_log: CostLog | None = None):
        self.cfg = cfg
        self.cost_log = cost_log
        self._reason: str | None = None

    @property
    def available(self) -> bool:
        return self._reason is None

    @property
    def unavailable_reason(self) -> str:
        return self._reason or ""

    @abstractmethod
    def structured(
        self,
        *,
        stage: str,
        model: str,
        system: str,
        content: list[Block],
        tool: ToolSpec,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """One call, one JSON object back, validated against `tool`."""

    @staticmethod
    @abstractmethod
    def image_tokens(cfg: Config, width: int, height: int) -> int:
        """Input tokens this vendor charges for one image of this size.

        Part of the provider contract because the two vendors differ by nearly
        5x here, and images are ~88% of this pipeline's spend — an estimator
        that assumed one vendor's formula would misprice the other by more than
        the entire budget.
        """

    def require(self, stage: str) -> None:
        if not self.available:
            raise LLMUnavailable(
                f"Cannot run stage '{stage}': {self._reason}. Set "
                f"{self.cfg.key_env_var} in .env, or run with --offline to "
                f"exercise the pipeline without any API calls."
            )


def get_provider(cfg: Config, cost_log: CostLog | None = None) -> LLMProvider:
    """Build the provider named in config. Never raises on a missing key —
    the caller inspects `.available` and can choose the offline path."""
    from .anthropic_provider import AnthropicProvider
    from .gemini_provider import GeminiProvider

    providers = {"anthropic": AnthropicProvider, "gemini": GeminiProvider}
    impl = providers.get(cfg.llm.provider)
    if impl is None:
        raise ValueError(
            f"Unknown llm.provider '{cfg.llm.provider}'. "
            f"Known: {', '.join(sorted(providers))}."
        )
    return impl(cfg, cost_log)


def missing_key_reason(cfg: Config) -> str | None:
    return None if provider_key(cfg) else f"{cfg.key_env_var} is not set"
