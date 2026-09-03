"""Typed access to config.yaml — the single tunable surface for the whole pipeline.

Nothing else in the codebase may hardcode a threshold, a model name, or a rate.
If you find yourself reaching for a magic number, add it here instead.
"""

from __future__ import annotations

import copy
import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"


class PathsConfig(BaseModel):
    data_dir: str = "data"
    db_path: str = "data/evergreen.db"
    ffmpeg_bin: str | None = None


class IngestConfig(BaseModel):
    allowed_extensions: list[str] = [".mp4", ".mov", ".webm", ".mkv"]
    max_upload_mb: int = 2048


class TranscribeConfig(BaseModel):
    enabled: bool = True
    model: str = "base"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str | None = None
    required: bool = False


class FramesConfig(BaseModel):
    sample_fps: float = 2.0
    analysis_width: int = 320
    llm_max_edge_px: int = 1568
    screenshot_format: str = "png"


class AdaptiveConfig(BaseModel):
    enabled: bool = True
    window: int = 15
    k: float = 3.5
    min_ratio: float = 1.6
    abs_floor: float = 0.004


class ChangeDetectionConfig(BaseModel):
    adaptive: AdaptiveConfig = Field(default_factory=AdaptiveConfig)
    hard_change_threshold: float = 0.75
    stability_threshold: float = 0.985
    stability_ratio: float = 2.0
    include_initial_state: bool = True
    settle_delay_ms: int = 350
    max_settle_wait_ms: int = 2500
    phash_distance_threshold: int = 6
    min_event_gap_ms: int = 1000
    ignore_bottom_pct: float = 0.0
    ignore_top_pct: float = 0.0


class RankWeights(BaseModel):
    visual_magnitude: float = 0.5
    stability: float = 0.2
    transcript_overlap: float = 0.2
    temporal_spread: float = 0.1


class CandidatesConfig(BaseModel):
    min_frames: int = 8
    max_frames: int = 25
    rank_weights: RankWeights = Field(default_factory=RankWeights)
    min_spacing_ms: int = 1200
    dedupe_ink_iou: float = 0.92


class VisualConfig(BaseModel):
    ink_width: int = 512
    ink_delta: int = 12


class StepsConfig(BaseModel):
    target_count: int | None = None
    min_count: int = 2
    max_count: int = 30
    granularity: str = "normal"


class WritingConfig(BaseModel):
    tone: str = "neutral"
    audience: str = "an internal team member who has not used this tool before"
    person: str = "second"
    max_instruction_words: int = 45


class DiffConfig(BaseModel):
    match_threshold: float = 0.62
    identical_threshold: float = 0.94
    ambiguous_band: tuple[float, float] = (0.62, 0.94)
    #: Escalation starts HERE, not at `match_threshold`. A pair the assignment
    #: chose but scored below the match threshold is the single most important
    #: case to adjudicate — it is about to be reported as remove+add — so
    #: bounding escalation at the match threshold guaranteed that the pairs
    #: most needing a judge were the only ones that never reached one.
    escalate_floor: float = 0.40
    use_visual_fallback: bool = True
    field_rewrite_threshold: float = 0.72
    #: Below this, a prose difference is a real change and is never escalated.
    #: Between this and `field_rewrite_threshold` the offline score cannot tell
    #: a rewording from a rewrite, and only those fields reach the judge.
    field_ambiguous_floor: float = 0.45
    visual_same_screen_iou: float = 0.92
    visual_different_screen_iou: float = 0.55
    max_visual_comparisons: int = 6
    #: Hard cap on identity adjudications per diff. The prose judge is batched
    #: into a single call and is not counted against this.
    max_llm_judgements: int = 8
    reorder_min_similarity: float = 0.66


class SimilarityConfig(BaseModel):
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    use_embeddings: bool = True
    use_llm_judge: bool = True


Provider = Literal["anthropic", "gemini"]

#: Which environment variable holds each provider's key.
PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


class ProviderModels(BaseModel):
    """Model ids for one vendor. Only the active provider's block is used."""

    classify: str
    structure: str
    judge: str


class LLMConfig(BaseModel):
    """Which vendor to call, and what to do when we cannot call one.

    `offline`:
      `auto`   fall back to the offline placeholder path, loudly.
      `never`  fail the stage instead — the right setting for a real client
               run, where placeholder text silently replacing a real SOP is
               worse than a stopped pipeline.
      `always` never call the API, even when a key is present.
    """

    provider: Provider = "anthropic"
    offline: Literal["auto", "never", "always"] = "auto"
    providers: dict[str, ProviderModels] = {}


class ModelsConfig(BaseModel):
    """The *resolved* model ids, populated from the active provider's block.

    Every stage reads `cfg.models.classify` and friends without knowing which
    vendor is behind them, which is the point: switching provider is a config
    line, not a code change.
    """

    classify: str = "claude-haiku-4-5-20251001"
    structure: str = "claude-sonnet-5"
    judge: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 8000
    temperature: float = 0.0
    #: Gemini only. Its 2.5+ models think by default and bill thought tokens as
    #: output, which on short classification answers cost more than the answer
    #: itself (measured: 434 thought tokens for a 14-token reply). 0 disables.
    #: None leaves the vendor default alone. Ignored by Anthropic.
    thinking_budget: int | None = None


class TokenPrice(BaseModel):
    input: float
    output: float


class CostConfig(BaseModel):
    pricing: dict[str, TokenPrice] = {}
    #: Gemini only, and per model: it bills images per tile, not per pixel, so
    #: the cost is flat across every size this pipeline produces. Measured with
    #: count_tokens rather than assumed. Anthropic's w*h/750 needs no table.
    image_tokens: dict[str, int] = {}
    max_usd_per_job: float = 0.75
    warn_usd_per_job: float = 0.35


class CacheConfig(BaseModel):
    enabled: bool = True


class Config(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    transcribe: TranscribeConfig = Field(default_factory=TranscribeConfig)
    frames: FramesConfig = Field(default_factory=FramesConfig)
    change_detection: ChangeDetectionConfig = Field(default_factory=ChangeDetectionConfig)
    candidates: CandidatesConfig = Field(default_factory=CandidatesConfig)
    steps: StepsConfig = Field(default_factory=StepsConfig)
    writing: WritingConfig = Field(default_factory=WritingConfig)
    visual: VisualConfig = Field(default_factory=VisualConfig)
    diff: DiffConfig = Field(default_factory=DiffConfig)
    similarity: SimilarityConfig = Field(default_factory=SimilarityConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)

    @model_validator(mode="after")
    def _resolve_active_provider(self) -> "Config":
        """Copy the active provider's model ids into `models`.

        Done on every validation, so no stage ever branches on provider to pick
        a model name.

        The provider block is unconditionally authoritative. An earlier version
        let an explicit `models.classify` override it, which cannot work: after
        a `model_dump` round-trip — which is exactly what `merged_with` does,
        and what a per-job config override from the API will do — the resolved
        values look indistinguishable from hand-written ones, so switching
        provider silently kept the previous vendor's models. To override one
        model, edit its provider block.
        """
        block = self.llm.providers.get(self.llm.provider)
        if block is None:
            return self
        for field in ("classify", "structure", "judge"):
            setattr(self.models, field, getattr(block, field))
        return self

    # ---- derived helpers -------------------------------------------------

    @property
    def key_env_var(self) -> str:
        return PROVIDER_KEY_ENV[self.llm.provider]

    @property
    def data_root(self) -> Path:
        p = Path(self.paths.data_dir)
        return p if p.is_absolute() else REPO_ROOT / p

    @property
    def jobs_root(self) -> Path:
        return self.data_root / "jobs"

    @property
    def db_file(self) -> Path:
        p = Path(self.paths.db_path)
        return p if p.is_absolute() else REPO_ROOT / p

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_root / job_id

    def merged_with(self, overrides: dict[str, Any] | None) -> "Config":
        """Return a copy with a partial override dict deep-merged in.

        Lets a single upload tune thresholds without editing config.yaml.
        """
        if not overrides:
            return self
        return Config.model_validate(_deep_merge(self.model_dump(), overrides))


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@lru_cache(maxsize=1)
def load_config() -> Config:
    load_dotenv()
    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return Config.model_validate(raw)


def load_dotenv() -> None:
    """Read `.env` at the repo root into the environment.

    Deliberately hand-rolled and non-overriding: a key already exported in the
    shell wins over the file. Keeps API keys out of shell history and off the
    command line, where they end up in `ps` output and scrollback.
    """
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def resolve_ffmpeg(cfg: Config | None = None) -> str:
    """Find an ffmpeg binary.

    Order: explicit config path, then a system install, then the static binary
    that ships with the imageio-ffmpeg wheel. The last one means the project
    works on a machine where the user cannot sudo apt install anything.
    """
    cfg = cfg or load_config()
    if cfg.paths.ffmpeg_bin:
        return cfg.paths.ffmpeg_bin
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - environment specific
        raise RuntimeError(
            "No ffmpeg binary available. Install ffmpeg, or `pip install imageio-ffmpeg`, "
            "or set paths.ffmpeg_bin in config.yaml."
        ) from exc


def anthropic_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY")


def provider_key(cfg: Config | None = None) -> str | None:
    """The API key for whichever provider is active."""
    cfg = cfg or load_config()
    return os.environ.get(cfg.key_env_var)
