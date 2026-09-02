"""Stage 1 — accept a recording, park it on disk, record what it is."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .base import JobPaths, Stage, file_hash
from .video import probe_video, has_audio_stream


class IngestStage(Stage):
    name = "ingest"
    depends_on: list[str] = []

    def config_slice(self) -> dict[str, Any]:
        return {"ingest": self.cfg.ingest.model_dump()}

    def input_fingerprint(self, job: JobPaths, inputs: dict[str, Any]) -> str:
        from .base import _hash

        src = job.source_video()
        return _hash({
            "config": self.config_slice(),
            "file": file_hash(src) if src else None,
            "name": src.name if src else None,
        })

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        src = job.source_video()
        if src is None:
            raise FileNotFoundError(
                f"No source video in {job.root}. Add one with "
                f"`python -m app.cli ingest --video path/to/recording.mp4`"
            )

        info = probe_video(src, self.cfg)
        audio = has_audio_stream(src, self.cfg)

        if info["duration"] <= 0:
            raise ValueError(f"Could not read a duration from {src.name}; is it a valid video?")

        print(
            f"[ingest] {src.name}: {info['duration']:.1f}s, "
            f"{info['width']}x{info['height']} @ {info['fps']}fps, "
            f"audio={'yes' if audio else 'no'}"
        )

        return {
            "job_id": job.job_id,
            "source_path": job.rel(src),
            "source_name": src.name,
            "size_bytes": src.stat().st_size,
            "has_audio": audio,
            **info,
        }


def place_upload(cfg, job_id: str, upload_path: Path, original_name: str) -> Path:
    """Copy an uploaded file into its job dir as source.<ext>."""
    job = JobPaths(cfg, job_id).ensure()
    ext = Path(original_name).suffix.lower() or ".mp4"
    if ext not in cfg.ingest.allowed_extensions:
        raise ValueError(
            f"Unsupported file type '{ext}'. Allowed: {', '.join(cfg.ingest.allowed_extensions)}"
        )
    dest = job.root / f"source{ext}"
    for stale in job.root.glob("source.*"):
        stale.unlink()
    shutil.copy2(upload_path, dest)
    return dest
