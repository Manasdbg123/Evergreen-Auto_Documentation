"""End-to-end API tests over the demo flow.

These exist because the hard requirement — hand edits survive regeneration —
is only really proven across the HTTP boundary. The unit tests prove
`DiffEngine` preserves edits when handed two SOP objects; these prove the
server actually marks, stores and carries them, which is where a real user's
notes would be lost.

No API key needed: the diff runs with `offline=True`, so the offline
similarity tiers decide everything.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.config import load_config  # noqa: E402
from app.pipeline.base import JobPaths, read_stage  # noqa: E402

DEMO_V1, DEMO_V2 = "demo_v1", "demo_v2"


def _has_demo_jobs(cfg) -> bool:
    return all(
        read_stage(JobPaths(cfg, j), "structure") is not None
        for j in (DEMO_V1, DEMO_V2)
    )


@pytest.fixture(scope="module", autouse=True)
def isolated_db(tmp_path_factory):
    """Point every component at a scratch database.

    Not hygiene for its own sake: without this the tests wrote documents and
    versions into the real demo database, and a later `cli diff` reported a
    stale test edit as a preserved edit. `EVERGREEN_DB` is read through
    `Config.db_file`, so this reaches the API, the CLI and the pipeline alike
    without any of them needing to know they are under test.
    """
    import os

    path = tmp_path_factory.mktemp("db") / "test.db"
    previous = os.environ.get("EVERGREEN_DB")
    os.environ["EVERGREEN_DB"] = str(path)
    yield path
    if previous is None:
        os.environ.pop("EVERGREEN_DB", None)
    else:
        os.environ["EVERGREEN_DB"] = previous


@pytest.fixture(scope="module")
def cfg(isolated_db):
    return load_config()


@pytest.fixture(scope="module")
def client(cfg):
    from app.main import app

    if not _has_demo_jobs(cfg):
        pytest.skip("demo_v1/demo_v2 have not been generated in this checkout")
    with TestClient(app) as c:
        yield c


@pytest.fixture
def document(client, cfg):
    """A document whose v1 is the demo_v1 SOP."""
    db.create_job(cfg, DEMO_V1)
    resp = client.post("/api/documents", json={"job_id": DEMO_V1})
    assert resp.status_code == 200, resp.text
    return resp.json()["document_id"]


def test_health_reports_the_active_provider(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["provider"] in {"anthropic", "gemini"}


def test_config_is_exposed_whole(client):
    """The tunable surface is part of the pitch, so it must be inspectable."""
    body = client.get("/api/config").json()
    for section in ("change_detection", "candidates", "diff", "writing", "models"):
        assert section in body


def test_document_round_trips_with_screenshot_urls(client, document):
    body = client.get(f"/api/documents/{document}").json()
    assert body["sop"]["steps"], "the document has no steps"
    # The browser cannot fetch a job-relative path.
    assert any(s.get("screenshot_url", "").startswith("/files/")
               for s in body["sop"]["steps"])


def test_editing_marks_only_the_field_that_changed(client, document):
    body = client.get(f"/api/documents/{document}").json()
    sop = body["sop"]
    sop["steps"][1]["instruction"] = "Click New request. NB: admins only."

    resp = client.put(f"/api/documents/{document}", json={"sop": sop})
    assert resp.status_code == 200, resp.text
    assert resp.json()["edited_fields"] == ["instruction"]

    stored = client.get(f"/api/documents/{document}").json()["sop"]
    meta = stored["steps"][1]["meta"]
    assert meta["edited_by_human"] is True
    assert meta["edited_fields"] == ["instruction"]
    # The model's original must be kept, or identity matching would compare
    # the user's prose against generated prose on the next regeneration.
    assert meta["generated_values"]["instruction"]
    assert meta["generated_values"]["instruction"] != sop["steps"][1]["instruction"]


def test_edits_are_not_cleared_by_an_unrelated_later_save(client, document):
    """Editing is cumulative. A save that touches step 3 must not un-mark
    step 2, or the next regeneration would overwrite step 2's notes."""
    sop = client.get(f"/api/documents/{document}").json()["sop"]
    sop["steps"][1]["instruction"] = "First edit."
    client.put(f"/api/documents/{document}", json={"sop": sop})

    sop = client.get(f"/api/documents/{document}").json()["sop"]
    sop["steps"][2]["title"] = "A different step title"
    client.put(f"/api/documents/{document}", json={"sop": sop})

    stored = client.get(f"/api/documents/{document}").json()["sop"]
    assert stored["steps"][1]["meta"]["edited_fields"] == ["instruction"]
    assert stored["steps"][2]["meta"]["edited_fields"] == ["title"]


def test_the_whole_demo_flow_preserves_a_hand_edit(client, cfg, document):
    """THE hard requirement, across HTTP.

    Edit v1, regenerate from a second recording, and the hand-written note must
    still be in the document that comes back.
    """
    note = "Click New request. NB: only visible to admins — see ticket OPS-412."
    sop = client.get(f"/api/documents/{document}").json()["sop"]
    lineage = sop["steps"][1]["meta"]["lineage_id"]
    sop["steps"][1]["instruction"] = note
    client.put(f"/api/documents/{document}", json={"sop": sop})

    db.create_job(cfg, DEMO_V2)
    resp = client.post(f"/api/documents/{document}/diff",
                       json={"job_id": DEMO_V2, "offline": True})
    assert resp.status_code == 200, resp.text
    diff = resp.json()["diff"]

    survivors = [
        s for s in client.get(f"/api/documents/{document}").json()["sop"]["steps"]
        if s["meta"]["lineage_id"] == lineage
    ]
    assert survivors, "the edited step lost its lineage across regeneration"
    assert survivors[0]["instruction"] == note, "regeneration destroyed a hand edit"

    preserved = [e for e in diff["entries"] if e["preserved_edits"]]
    assert preserved, "the diff did not report the edit as preserved"


def test_diff_finds_the_rename_rather_than_remove_plus_add(client, cfg, document):
    """The demo's headline: Save -> Submit must be one modified step."""
    db.create_job(cfg, DEMO_V2)
    resp = client.post(f"/api/documents/{document}/diff",
                       json={"job_id": DEMO_V2, "offline": True})
    entries = resp.json()["diff"]["entries"]

    renames = [
        c for e in entries for c in e["field_changes"]
        if c["field"] == "ui_element.label"
        and c["old"] == "Save" and c["new"] == "Submit"
    ]
    assert renames, "the Save -> Submit rename was not reported as a field change"
    step = next(e for e in entries
                if any(c["field"] == "ui_element.label" for c in e["field_changes"]))
    assert step["status"] == "modified", (
        f"the renamed step was reported {step['status']}, not modified — "
        f"a rename reported as remove+add hides the change"
    )


def test_rejecting_a_change_restores_the_previous_text(client, cfg, document):
    db.create_job(cfg, DEMO_V2)
    resp = client.post(f"/api/documents/{document}/diff",
                       json={"job_id": DEMO_V2, "offline": True})
    payload = resp.json()
    diff_id = payload["diff_id"]

    target = next(e for e in payload["diff"]["entries"]
                  if e["status"] == "modified" and e["field_changes"])
    field = next(c for c in target["field_changes"] if c["field"] == "expected_result")

    review = client.post(f"/api/diffs/{diff_id}/review",
                         json={"diff_id": diff_id,
                               "decisions": {target["diff_id"]: "rejected"}})
    assert review.status_code == 200, review.text
    assert review.json()["rejected"] == 1

    sop = client.get(f"/api/documents/{document}").json()["sop"]
    step = next(s for s in sop["steps"]
                if s["meta"]["lineage_id"] == target["lineage_id"])
    assert step["expected_result"] == field["old"], (
        "rejecting a change must put the previous text back, not just dismiss "
        "the row"
    )


def test_exports_render_from_the_edited_version(client, document):
    note = "Hand-written note that must appear in the export."
    sop = client.get(f"/api/documents/{document}").json()["sop"]
    sop["steps"][0]["instruction"] = note
    client.put(f"/api/documents/{document}", json={"sop": sop})

    md = client.get(f"/api/documents/{document}/export?format=markdown")
    html = client.get(f"/api/documents/{document}/export?format=html")
    assert md.status_code == 200 and html.status_code == 200
    assert note in md.text, "the export shipped generated text over an edit"
    assert note in html.text


def test_unknown_ids_are_404_not_500(client):
    assert client.get("/api/documents/doc_nope").status_code == 404
    assert client.get("/api/jobs/job_nope").status_code == 404
    assert client.get("/api/diffs/diff_nope").status_code == 404
