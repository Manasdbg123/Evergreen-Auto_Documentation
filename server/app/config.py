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
from typing import Any

import yaml
from pydantic import BaseModel, Field

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
    use_visual_fallback: bool = True
    visual_phash_threshold: int = 10
    max_visual_comparisons: int = 6
    reorder_min_similarity: float = 0.78


class SimilarityConfig(BaseModel):
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    use_embeddings: bool = True
    use_llm_judge: bool = True


class ModelsConfig(BaseModel):
    classify: str = "claude-haiku-4-5-20251001"
    structure: str = "claude-sonnet-5"
    judge: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 8000
    temperature: float = 0.0


class TokenPrice(BaseModel):
    input: float
    output: float


class CostConfig(BaseModel):
    pricing: dict[str, TokenPrice] = {}
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
    diff: DiffConfig = Field(default_factory=DiffConfig)
    similarity: SimilarityConfig = Field(default_factory=SimilarityConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    cost: CostConfig = Field(default_factory=CostConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)

    # ---- derived helpers -------------------------------------------------

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
    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    return Config.model_validate(raw)


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
