"""Shared plumbing for the two stages that can cost money.

Pulls three concerns out of both stages so they cannot drift apart:

* one `CostLog` per job, so `cost.jsonl` and the budget cap see every call
  regardless of which stage made it;
* one decision about whether this run talks to Anthropic at all;
* one place where that decision is announced, loudly, because the difference
  between a real SOP and an offline placeholder must never be a surprise.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..llm.client import LLMClient
from ..llm.cost import CostLog
from .base import JobPaths, Stage


class LLMStage(Stage):
    """A stage that may call Anthropic, and must behave when it cannot."""

    def __init__(self, cfg: Config | None = None, offline: bool | None = None):
        super().__init__(cfg)
        #: Explicit --offline beats config; config `llm.offline` decides the rest.
        self.offline = (
            offline if offline is not None else self.cfg.llm.offline == "always"
        )
        self._client: LLMClient | None = None
        self._cost: CostLog | None = None

    # ------------------------------------------------------------------

    def client(self, job: JobPaths) -> LLMClient:
        if self._client is None:
            self._cost = CostLog(self.cfg, job.cost_log)
            self._client = LLMClient(self.cfg, self._cost)
        return self._client

    def resolve_mode(self, job: JobPaths) -> str:
        """'llm' or 'offline', decided once and said out loud.

        `llm.offline: never` turns a missing key into a hard failure. That is
        the right default for a client running this in anger — silently
        producing placeholder text where a real SOP was expected is a worse
        outcome than a stopped pipeline.
        """
        if self.offline:
            print(f"[{self.name}] offline mode — no API calls will be made")
            return "offline"

        client = self.client(job)
        if client.available:
            return "llm"

        if self.cfg.llm.offline == "never":
            raise RuntimeError(
                f"[{self.name}] {client.unavailable_reason}, and llm.offline is "
                f"'never'. Set ANTHROPIC_API_KEY, or set llm.offline: auto in "
                f"config.yaml to allow the placeholder path."
            )

        print(
            f"[{self.name}] {client.unavailable_reason} — falling back to the "
            f"offline path.\n"
            f"[{self.name}] Output will be schema-valid PLACEHOLDER text, not a "
            f"real SOP: no model reads the screenshots on this path."
        )
        return "offline"

    def config_slice(self) -> dict[str, Any]:  # pragma: no cover - overridden
        return {"offline": self.offline}
