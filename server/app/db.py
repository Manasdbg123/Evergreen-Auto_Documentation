"""SQLite: job status, documents, and a version per save.

Three tables and no ORM, because the shapes here are genuinely this simple and
an ORM would add a dependency, a migration story and an import cost for no
gain at this size.

**A document is the diffable unit, not a job.** A job is one recording; a
document is one procedure, and v1 and v2 of a workflow are two jobs pointing
at the same `document_id`. Without that grouping there is no answer to "which
SOP is this the new version of", and the diff has nothing to align against.

**Steps are stored inside the version's JSON, not as rows.** The SOP is always
read and written whole — the editor saves a document, the diff consumes a
document — so a step table would buy joins nobody performs and cost a
reassembly on every read. The cross-version join key is `StepMeta.lineage_id`,
which travels inside the JSON and is carried forward by `preserve_edits`; it,
not `step_id`, is what makes a hand edit survive regeneration. A step table
keyed on `step_id` would actively mislead here, since `step_id` is freshly
minted on every regeneration by design.

**Every save is a new row.** Versions are append-only. Regeneration is exactly
as recoverable as a bad manual edit, which matters when the whole promise is
that re-recording is safe.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import Config, load_config
from .models import SOP, DiffResult, new_id

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT 'Untitled procedure',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    document_id TEXT REFERENCES documents(document_id),
    status      TEXT NOT NULL DEFAULT 'created',
    stage       TEXT,
    error       TEXT,
    source_name TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

-- One row per save. `source` distinguishes a regenerated version from a hand
-- edit, which is what lets the UI say "you edited this" months later.
CREATE TABLE IF NOT EXISTS versions (
    version_id  TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    version     INTEGER NOT NULL,
    job_id      TEXT,
    source      TEXT NOT NULL DEFAULT 'generated',   -- generated | edited | merged
    sop_json    TEXT NOT NULL,
    created_at  REAL NOT NULL,
    UNIQUE (document_id, version)
);

CREATE TABLE IF NOT EXISTS diffs (
    diff_id      TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL REFERENCES documents(document_id),
    old_version  INTEGER NOT NULL,
    new_version  INTEGER NOT NULL,
    diff_json    TEXT NOT NULL,
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_versions_doc ON versions(document_id, version);
CREATE INDEX IF NOT EXISTS idx_versions_job ON versions(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_doc     ON jobs(document_id);
"""


@contextmanager
def connect(cfg: Config | None = None) -> Iterator[sqlite3.Connection]:
    cfg = cfg or load_config()
    path = Path(cfg.db_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # The API and the CLI can both be touching this file; WAL lets a read
    # proceed while a pipeline run is writing.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(cfg: Config | None = None) -> Path:
    cfg = cfg or load_config()
    with connect(cfg):
        pass
    return Path(cfg.db_file)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def create_job(cfg: Config, job_id: str, *, document_id: str | None = None,
               source_name: str | None = None) -> None:
    now = time.time()
    with connect(cfg) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO jobs "
            "(job_id, document_id, status, source_name, created_at, updated_at) "
            "VALUES (?, ?, 'created', ?, ?, ?)",
            (job_id, document_id, source_name, now, now),
        )


def set_job_status(cfg: Config, job_id: str, status: str, *,
                   stage: str | None = None, error: str | None = None) -> None:
    """Status tracking, called from the pipeline runner rather than each stage.

    A stage that updated its own status would have to know it was being run by
    a server rather than the CLI; keeping it here means stages stay pure
    disk-in/disk-out and remain independently runnable.
    """
    with connect(cfg) as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, stage = ?, error = ?, updated_at = ? "
            "WHERE job_id = ?",
            (status, stage, error, time.time(), job_id),
        )


def get_job(cfg: Config, job_id: str) -> dict[str, Any] | None:
    with connect(cfg) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(cfg: Config, limit: int = 100) -> list[dict[str, Any]]:
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def attach_job_to_document(cfg: Config, job_id: str, document_id: str) -> None:
    with connect(cfg) as conn:
        conn.execute(
            "UPDATE jobs SET document_id = ?, updated_at = ? WHERE job_id = ?",
            (document_id, time.time(), job_id),
        )


# --------------------------------------------------------------------------
# Documents and versions
# --------------------------------------------------------------------------


def create_document(cfg: Config, title: str = "Untitled procedure",
                    document_id: str | None = None) -> str:
    document_id = document_id or new_id("doc")
    now = time.time()
    with connect(cfg) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO documents (document_id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (document_id, title, now, now),
        )
    return document_id


def get_document(cfg: Config, document_id: str) -> dict[str, Any] | None:
    with connect(cfg) as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()
    return dict(row) if row else None


def list_documents(cfg: Config, limit: int = 100) -> list[dict[str, Any]]:
    """Documents with their version count, newest activity first."""
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT d.*, "
            "  (SELECT COUNT(*) FROM versions v WHERE v.document_id = d.document_id) "
            "    AS version_count, "
            "  (SELECT MAX(version) FROM versions v WHERE v.document_id = d.document_id) "
            "    AS latest_version "
            "FROM documents d ORDER BY d.updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_version(cfg: Config, document_id: str, sop: SOP, *,
                 source: str = "generated", job_id: str | None = None) -> int:
    """Append a version and return its number.

    The version number is assigned here rather than taken from the SOP, so two
    concurrent saves cannot both claim to be v3 — the UNIQUE constraint on
    (document_id, version) makes that a loud failure rather than a silent
    overwrite of somebody's edit.
    """
    now = time.time()
    with connect(cfg) as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM versions WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        version = int(row["v"]) + 1

        stored = sop.model_copy(deep=True)
        stored.document_id = document_id
        stored.version = version

        conn.execute(
            "INSERT INTO versions "
            "(version_id, document_id, version, job_id, source, sop_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id("ver"), document_id, version, job_id, source,
             stored.model_dump_json(), now),
        )
        conn.execute(
            "UPDATE documents SET updated_at = ?, title = ? WHERE document_id = ?",
            (now, stored.title, document_id),
        )
    return version


def get_version(cfg: Config, document_id: str, version: int | None = None) -> SOP | None:
    """A specific version, or the latest when `version` is None."""
    with connect(cfg) as conn:
        if version is None:
            row = conn.execute(
                "SELECT sop_json FROM versions WHERE document_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (document_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT sop_json FROM versions WHERE document_id = ? AND version = ?",
                (document_id, version),
            ).fetchone()
    return SOP.model_validate_json(row["sop_json"]) if row else None


def list_versions(cfg: Config, document_id: str) -> list[dict[str, Any]]:
    with connect(cfg) as conn:
        rows = conn.execute(
            "SELECT version_id, version, job_id, source, created_at "
            "FROM versions WHERE document_id = ? ORDER BY version DESC",
            (document_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def latest_sop_for_job(cfg: Config, job_id: str) -> SOP | None:
    """The most recent stored SOP produced from, or edited after, this job.

    This is the function that makes edit preservation real rather than
    theoretical: the diff calls it to get what the user is actually looking at,
    not what the model first generated. If a job has a document, the document's
    latest version wins — that version may include hand edits made long after
    the recording was processed.
    """
    with connect(cfg) as conn:
        row = conn.execute(
            "SELECT document_id FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        document_id = row["document_id"] if row else None

        if document_id:
            latest = conn.execute(
                "SELECT sop_json FROM versions WHERE document_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (document_id,),
            ).fetchone()
            if latest:
                return SOP.model_validate_json(latest["sop_json"])

        # No document, but the job may still have been saved directly.
        direct = conn.execute(
            "SELECT sop_json FROM versions WHERE job_id = ? "
            "ORDER BY version DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    return SOP.model_validate_json(direct["sop_json"]) if direct else None


# --------------------------------------------------------------------------
# Diffs
# --------------------------------------------------------------------------


def save_diff(cfg: Config, document_id: str, result: DiffResult) -> str:
    diff_id = new_id("diff")
    with connect(cfg) as conn:
        conn.execute(
            "INSERT INTO diffs "
            "(diff_id, document_id, old_version, new_version, diff_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (diff_id, document_id, result.old_version, result.new_version,
             result.model_dump_json(), time.time()),
        )
    return diff_id


def get_diff(cfg: Config, diff_id: str) -> DiffResult | None:
    with connect(cfg) as conn:
        row = conn.execute(
            "SELECT diff_json FROM diffs WHERE diff_id = ?", (diff_id,)
        ).fetchone()
    return DiffResult.model_validate_json(row["diff_json"]) if row else None


def latest_diff(cfg: Config, document_id: str) -> tuple[str, DiffResult] | None:
    with connect(cfg) as conn:
        row = conn.execute(
            "SELECT diff_id, diff_json FROM diffs WHERE document_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (document_id,),
        ).fetchone()
    if not row:
        return None
    return row["diff_id"], DiffResult.model_validate_json(row["diff_json"])


def update_diff(cfg: Config, diff_id: str, result: DiffResult) -> None:
    """Persist review decisions (accept/reject) back onto a stored diff."""
    with connect(cfg) as conn:
        conn.execute(
            "UPDATE diffs SET diff_json = ? WHERE diff_id = ?",
            (result.model_dump_json(), diff_id),
        )


def json_default(value: Any) -> Any:  # pragma: no cover - serialisation helper
    return json.loads(value) if isinstance(value, (str, bytes)) else value
