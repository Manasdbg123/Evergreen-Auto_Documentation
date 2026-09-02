"""Stage plumbing: every stage reads from disk, writes to disk, and caches.

The contract is deliberately blunt. A stage is a pure-ish function over
`data/jobs/{job_id}/stages/*.json`, so any stage can be re-run in isolation
from the CLI without touching the ones before it. That is what keeps the LLM
stages cheap to iterate on: transcription and frame extraction are computed
once and never again.
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from ..config import Config, load_config

STAGE_ORDER = [
    "ingest",
    "transcribe",
    "frames",
    "detect_changes",
    "select_candidates",
    "detect_steps",
    "structure",
    "diff",
    "export",
]


class JobPaths:
    """Every path a job owns, in one place."""

    def __init__(self, cfg: Config, job_id: str):
        self.cfg = cfg
        self.job_id = job_id
        self.root = cfg.job_dir(job_id)
        self.stages = self.root / "stages"
        self.frames = self.root / "frames"
        self.sampled = self.frames / "sampled"
        self.stable = self.frames / "stable"
        self.llm_frames = self.frames / "llm"
        self.screenshots = self.root / "screenshots"
        self.exports = self.root / "exports"
        self.cost_log = self.root / "cost.jsonl"

    def ensure(self) -> "JobPaths":
        for d in (
            self.root, self.stages, self.frames, self.sampled,
            self.stable, self.llm_frames, self.screenshots, self.exports,
        ):
            d.mkdir(parents=True, exist_ok=True)
        return self

    def stage_file(self, name: str) -> Path:
        idx = STAGE_ORDER.index(name) + 1 if name in STAGE_ORDER else 99
        return self.stages / f"{idx:02d}_{name}.json"

    def source_video(self) -> Path | None:
        for p in sorted(self.root.glob("source.*")):
            return p
        return None

    def rel(self, path: Path | str) -> str:
        """Store paths relative to the job root so the data dir stays portable."""
        p = Path(path)
        try:
            return str(p.resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(p)

    def abs(self, rel_path: str) -> Path:
        p = Path(rel_path)
        return p if p.is_absolute() else self.root / p


class StageResult(dict):
    """Plain dict, but carries whether the value came from cache."""

    from_cache: bool = False


class Stage(ABC):
    """Base class. Subclasses implement `compute` and `input_fingerprint`."""

    name: str = "unnamed"
    #: Stages whose output must exist before this one can run.
    depends_on: list[str] = []

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or load_config()

    # ---- subclass surface -------------------------------------------------

    @abstractmethod
    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        """Do the real work and return the JSON-serialisable stage output."""

    def input_fingerprint(self, job: JobPaths, inputs: dict[str, Any]) -> str:
        """Hash of everything that would change this stage's output.

        Includes the config slice the stage cares about, so nudging a
        threshold in config.yaml correctly invalidates the cache while
        leaving unrelated stages alone.
        """
        payload = {
            "config": self.config_slice(),
            "deps": {k: v.get("_fingerprint") for k, v in inputs.items()},
        }
        return _hash(payload)

    def config_slice(self) -> dict[str, Any]:
        """Override to narrow which config keys invalidate this stage."""
        return self.cfg.model_dump()

    # ---- runner -----------------------------------------------------------

    def run(self, job_id: str, force: bool = False) -> dict[str, Any]:
        job = JobPaths(self.cfg, job_id).ensure()
        inputs = {dep: self.load_output(job, dep, required=True) for dep in self.depends_on}

        fingerprint = self.input_fingerprint(job, inputs)
        cached = self._read_cache(job)
        if (
            not force
            and self.cfg.cache.enabled
            and cached is not None
            and cached.get("_fingerprint") == fingerprint
        ):
            print(f"[{self.name}] cache hit — skipping ({job.stage_file(self.name).name})")
            out = StageResult(cached)
            out.from_cache = True
            return out

        started = time.time()
        print(f"[{self.name}] running…")
        data = self.compute(job, inputs)
        elapsed = time.time() - started

        data["_stage"] = self.name
        data["_fingerprint"] = fingerprint
        data["_elapsed_sec"] = round(elapsed, 3)
        data["_generated_at"] = time.time()

        path = job.stage_file(self.name)
        path.write_text(json.dumps(data, indent=2, default=str))
        print(f"[{self.name}] done in {elapsed:.2f}s -> {path.name}")

        out = StageResult(data)
        out.from_cache = False
        return out

    # ---- io ---------------------------------------------------------------

    def _read_cache(self, job: JobPaths) -> dict[str, Any] | None:
        return read_stage(job, self.name)

    @staticmethod
    def load_output(job: JobPaths, name: str, required: bool = False) -> dict[str, Any]:
        data = read_stage(job, name)
        if data is None:
            if required:
                raise MissingStageError(
                    f"Stage '{name}' has not been run for job {job.job_id}. "
                    f"Run it first: python -m app.cli {name} {job.job_id}"
                )
            return {}
        return data


class MissingStageError(RuntimeError):
    pass


def read_stage(job: JobPaths, name: str) -> dict[str, Any] | None:
    path = job.stage_file(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    """Hash the first and last MB plus the size — fast enough for 2GB videos."""
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(chunk))
        if size > chunk * 2:
            f.seek(-chunk, 2)
            h.update(f.read(chunk))
    return h.hexdigest()[:16]
