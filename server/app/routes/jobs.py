"""Upload a recording and watch it become an SOP."""

from __future__ import annotations

import json
from typing import Any

from fastapi import (APIRouter, BackgroundTasks, File, Form, HTTPException,
                     UploadFile)

from .. import db
from ..config import load_config
from ..models import new_id
from ..pipeline.base import JobPaths, read_stage
from ..pipeline.ingest import place_upload
from ..pipeline.runner import run_pipeline

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.post("")
async def create_job(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    document_id: str | None = Form(None),
    offline: bool = Form(False),
    config_overrides: str | None = Form(None),
) -> dict[str, Any]:
    """Accept a recording and start the pipeline.

    `document_id` is what makes this recording the *next version* of an
    existing procedure rather than a new one — it is the whole re-record flow.

    `config_overrides` is a partial config JSON merged over config.yaml for
    this job alone, so a single upload can be tuned (finer step granularity, a
    different tone, a stricter threshold) without editing the file.
    """
    cfg = load_config()

    if config_overrides:
        try:
            cfg = cfg.merged_with(json.loads(config_overrides))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(400, f"config_overrides is not valid: {exc}")

    suffix = "." + (file.filename or "upload.mp4").rsplit(".", 1)[-1].lower()
    if suffix not in cfg.ingest.allowed_extensions:
        raise HTTPException(
            400,
            f"Unsupported file type '{suffix}'. "
            f"Allowed: {', '.join(cfg.ingest.allowed_extensions)}",
        )

    if document_id and not db.get_document(cfg, document_id):
        raise HTTPException(404, f"No document '{document_id}'")

    job_id = new_id("job")
    job = JobPaths(cfg, job_id).ensure()
    dest = job.root / f"source{suffix}"

    # Streamed in chunks: these are screen recordings, and reading a 2GB upload
    # into memory to write it straight back out would be the one thing in this
    # pipeline that could not survive a real file.
    size = 0
    limit = cfg.ingest.max_upload_mb * 1024 * 1024
    with dest.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(
                    413, f"Upload exceeds {cfg.ingest.max_upload_mb} MB "
                         f"(ingest.max_upload_mb)")
            out.write(chunk)

    db.create_job(cfg, job_id, document_id=document_id,
                  source_name=file.filename)
    # Record the status we are about to report. Without this the row stayed
    # 'created' until the pipeline's first stage, so a job whose process died
    # in that window was indistinguishable from one the CLI had made and never
    # run — and startup reconciliation could not safely fail it.
    db.set_job_status(cfg, job_id, "queued")
    background.add_task(_run, cfg, job_id, offline, document_id)

    return {"job_id": job_id, "document_id": document_id,
            "status": "queued", "source_name": file.filename,
            "size_bytes": size}


def _run(cfg, job_id: str, offline: bool, document_id: str | None) -> None:
    """Background task. Failures are recorded on the job, never raised into
    a request that has already returned."""
    try:
        run_pipeline(cfg, job_id, offline=offline, document_id=document_id)
    except Exception as exc:  # pragma: no cover - background path
        print(f"[api] job {job_id} failed: {type(exc).__name__}: {exc}")
        # run_pipeline records its own failures, but it can only do that once
        # it has started. Anything raised before then — a bad config override,
        # an unreadable upload — would otherwise leave the job on 'queued'
        # with no explanation anywhere the user can see it.
        try:
            db.set_job_status(cfg, job_id, "failed",
                              error=f"{type(exc).__name__}: {exc}")
        except Exception:
            pass


@router.get("")
def list_jobs() -> list[dict[str, Any]]:
    return db.list_jobs(load_config())


@router.get("/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    cfg = load_config()
    record = db.get_job(cfg, job_id)
    if record is None:
        raise HTTPException(404, f"No job '{job_id}'")

    job = JobPaths(cfg, job_id)
    stages: dict[str, Any] = {}
    for name in ("ingest", "transcribe", "frames", "detect_changes",
                 "select_candidates", "detect_steps", "structure", "export"):
        data = read_stage(job, name)
        stages[name] = None if data is None else {
            "elapsed_sec": data.get("_elapsed_sec"),
            "count": data.get("count"),
            "mode": data.get("mode"),
        }

    record["stages"] = stages
    record["spend_usd"] = round(_spent(job), 6)
    return record


@router.get("/{job_id}/sop")
def get_job_sop(job_id: str) -> dict[str, Any]:
    """The raw generated SOP for a job, before any editing."""
    cfg = load_config()
    data = read_stage(JobPaths(cfg, job_id), "structure")
    if not data or not data.get("sop"):
        raise HTTPException(404, f"Job '{job_id}' has no SOP yet")
    return data["sop"]


@router.get("/{job_id}/candidates")
def get_candidates(job_id: str) -> dict[str, Any]:
    """The frames that were chosen, for the contact-sheet view.

    Useful well beyond debugging: when a step looks wrong, the first question
    is always whether the frame behind it was the right one.
    """
    cfg = load_config()
    job = JobPaths(cfg, job_id)
    data = read_stage(job, "select_candidates")
    if not data:
        raise HTTPException(404, f"Job '{job_id}' has no candidates yet")
    return {
        "count": data.get("count"),
        "candidates": [
            {**c, "url": f"/files/{job_id}/{c['frame_path']}"}
            for c in data.get("candidates", [])
        ],
    }


def _spent(job: JobPaths) -> float:
    if not job.cost_log.exists():
        return 0.0
    total = 0.0
    for line in job.cost_log.read_text().splitlines():
        try:
            total += json.loads(line).get("usd", 0.0)
        except json.JSONDecodeError:
            continue
    return total
