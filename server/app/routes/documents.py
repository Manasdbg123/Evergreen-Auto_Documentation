"""Documents, versions, editing and export.

A document is the diffable unit: v1 and v2 of a workflow are two jobs pointing
at one document. Every save appends a version, so regeneration is exactly as
recoverable as a bad manual edit.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel

from .. import db
from ..config import load_config
from ..editing import apply_edits
from ..models import SOP
from ..pipeline.base import JobPaths
from ..pipeline.export import to_html, to_markdown

router = APIRouter(prefix="/api/documents", tags=["documents"])


class CreateDocument(BaseModel):
    title: str = db.PLACEHOLDER_TITLE
    #: Adopt this job's generated SOP as v1.
    job_id: str | None = None
    #: The product this procedure belongs to — "LeetCode", "Salesforce".
    app: str = ""


class SaveDocument(BaseModel):
    sop: SOP


class RenameDocument(BaseModel):
    """Both optional: send one to rename, the other to move, or both."""
    title: str | None = None
    app: str | None = None


@router.get("")
def list_documents() -> list[dict[str, Any]]:
    return db.list_documents(load_config())


@router.post("")
def create_document(body: CreateDocument) -> dict[str, Any]:
    cfg = load_config()
    sop = None
    if body.job_id:
        from ..pipeline.runner import current_sop

        sop = current_sop(cfg, body.job_id)
        if sop is None:
            raise HTTPException(404, f"Job '{body.job_id}' has no SOP yet")

    document_id = db.create_document(cfg, sop.title if sop else body.title,
                                     app=body.app)
    version = 0
    if sop:
        db.attach_job_to_document(cfg, body.job_id, document_id)
        version = db.save_version(cfg, document_id, sop, job_id=body.job_id)
    return {"document_id": document_id, "version": version}


@router.patch("/{document_id}")
def rename_document(document_id: str, body: RenameDocument) -> dict[str, Any]:
    """Rename a document, or move it under a different app."""
    cfg = load_config()
    if not db.update_document(cfg, document_id, title=body.title, app=body.app):
        raise HTTPException(404, f"No document '{document_id}'")
    return db.get_document(cfg, document_id) or {}


@router.get("/{document_id}")
def get_document(document_id: str, version: int | None = None) -> dict[str, Any]:
    cfg = load_config()
    doc = db.get_document(cfg, document_id)
    if doc is None:
        raise HTTPException(404, f"No document '{document_id}'")

    sop = db.get_version(cfg, document_id, version)
    if sop is None:
        raise HTTPException(404, f"Document '{document_id}' has no version "
                                 f"{version if version else 'saved yet'}")
    return {
        "document": doc,
        "versions": db.list_versions(cfg, document_id),
        "sop": _with_urls(cfg, sop),
    }


@router.put("/{document_id}")
def save_document(document_id: str, body: SaveDocument) -> dict[str, Any]:
    """Save an edited SOP as a new version.

    Which fields the human changed is derived here by comparing against the
    stored version, never taken from the client — see `editing.apply_edits`.
    Those marks are what stop the next regeneration from overwriting the user's
    writing, so a client that could set them itself could also silently opt the
    user out of the guarantee.
    """
    cfg = load_config()
    if db.get_document(cfg, document_id) is None:
        raise HTTPException(404, f"No document '{document_id}'")

    previous = db.get_version(cfg, document_id)
    incoming = body.sop
    marked = apply_edits(previous, incoming) if previous else incoming

    version = db.save_version(cfg, document_id, marked, source="edited")
    edited = sorted({f for s in marked.steps for f in s.meta.edited_fields})
    return {"document_id": document_id, "version": version,
            "edited_fields": edited}


@router.get("/{document_id}/versions")
def list_versions(document_id: str) -> list[dict[str, Any]]:
    cfg = load_config()
    if db.get_document(cfg, document_id) is None:
        raise HTTPException(404, f"No document '{document_id}'")
    return db.list_versions(cfg, document_id)


@router.get("/{document_id}/export")
def export_document(document_id: str, format: str = "markdown",
                    version: int | None = None):
    """Markdown or HTML, rendered from the version the user is actually looking
    at — not from the raw generated text, which may be several edits stale."""
    cfg = load_config()
    sop = db.get_version(cfg, document_id, version)
    if sop is None:
        raise HTTPException(404, f"No such document or version")

    job = JobPaths(cfg, sop.job_id) if sop.job_id else None
    if format in ("markdown", "md"):
        return PlainTextResponse(to_markdown(sop, job),
                                 media_type="text/markdown; charset=utf-8")
    if format == "html":
        return HTMLResponse(to_html(sop, job))
    raise HTTPException(400, "format must be 'markdown' or 'html'")


def _with_urls(cfg, sop: SOP) -> dict[str, Any]:
    """Rewrite screenshot refs to URLs the browser can actually fetch.

    Stored refs are relative to the job directory so the data folder stays
    portable; the client needs something it can put in an <img src>. Done here
    rather than at write time so moving the data directory does not invalidate
    every stored document.
    """
    data = sop.model_dump()
    for step in data.get("steps", []):
        ref = step.get("screenshot_ref")
        if ref and sop.job_id:
            step["screenshot_url"] = f"/files/{sop.job_id}/{ref}"
    return data
