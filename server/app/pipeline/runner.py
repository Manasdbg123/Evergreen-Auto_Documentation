"""One definition of "run the pipeline", shared by the CLI and the API.

Both entry points need the same sequence, the same status transitions and the
same failure behaviour. Two copies of that drift — the API grows a retry the
CLI does not have, the CLI gets a stage the API forgets — and the difference
only ever shows up in a demo.

Status lives in SQLite rather than in each stage, so stages stay pure
disk-in/disk-out and remain independently runnable from the command line with
no database at all.
"""

from __future__ import annotations

from typing import Callable

from .. import db
from ..config import Config
from ..models import SOP
from .base import JobPaths, read_stage
from .detect_changes import DetectChangesStage
from .detect_steps import DetectStepsStage
from .export import ExportStage
from .frames import FramesStage
from .llm_stage import LLMStage
from .select_candidates import SelectCandidatesStage
from .structure import StructureStage
from .transcribe import TranscribeStage

#: The full sequence. `diff` is absent on purpose — it spans two jobs and is
#: driven separately, by `diff_stage.DiffStage`.
PIPELINE = [
    ("ingest", None),  # placeholder, replaced below
    ("transcribe", TranscribeStage),
    ("frames", FramesStage),
    ("detect_changes", DetectChangesStage),
    ("select_candidates", SelectCandidatesStage),
    ("detect_steps", DetectStepsStage),
    ("structure", StructureStage),
    ("export", ExportStage),
]


def _stages():
    from .ingest import IngestStage

    return [("ingest", IngestStage)] + PIPELINE[1:]


def run_pipeline(
    cfg: Config,
    job_id: str,
    *,
    offline: bool = False,
    force: bool = False,
    document_id: str | None = None,
    on_stage: Callable[[str], None] | None = None,
) -> SOP | None:
    """Run every stage for one job, tracking status. Returns the SOP.

    When `document_id` is given the finished SOP is stored as the next version
    of that document. That is what makes the *next* recording diffable: a job
    whose SOP was never versioned has nothing to be the previous version of.
    """
    db.create_job(cfg, job_id, document_id=document_id)
    if document_id:
        db.attach_job_to_document(cfg, job_id, document_id)

    try:
        for name, stage_cls in _stages():
            db.set_job_status(cfg, job_id, "running", stage=name)
            if on_stage:
                on_stage(name)
            stage = (stage_cls(cfg, offline=offline or None)
                     if issubclass(stage_cls, LLMStage) else stage_cls(cfg))
            stage.run(job_id, force=force)
    except Exception as exc:
        # Recorded rather than swallowed: a background task that dies silently
        # leaves a job stuck on "running" forever, which is indistinguishable
        # from a slow one.
        db.set_job_status(cfg, job_id, "failed", error=f"{type(exc).__name__}: {exc}")
        raise

    sop = current_sop(cfg, job_id)
    if sop is not None and document_id:
        version = db.save_version(cfg, document_id, sop, job_id=job_id)
        print(f"[runner] stored {document_id} v{version}")

    db.set_job_status(cfg, job_id, "complete")
    return sop


def current_sop(cfg: Config, job_id: str) -> SOP | None:
    data = read_stage(JobPaths(cfg, job_id), "structure")
    if not data or not data.get("sop"):
        return None
    return SOP.model_validate(data["sop"])
