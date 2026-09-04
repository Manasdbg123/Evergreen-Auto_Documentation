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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sop_fixtures  # noqa: E402

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
    """A document whose v1 is the demo_v1 SOP.

    Deliberately does NOT pre-create the job row. An earlier version of this
    fixture did, and that hid a real bug: `attach_job_to_document` was an
    UPDATE, so for a job processed by the CLI before the database existed it
    silently matched nothing and the diff went on to compare against pre-edit
    text. The fixture must exercise the same path a real user does.
    """
    resp = client.post("/api/documents", json={"job_id": DEMO_V1})
    assert resp.status_code == 200, resp.text
    return resp.json()["document_id"]


def test_adopting_a_job_with_no_job_row_still_links_it(client, cfg, document):
    """A job created by the CLI has no `jobs` row until something makes one.

    If adopting it leaves `jobs.document_id` NULL, `latest_sop_for_job` falls
    back to the last *generated* version and every later diff silently ignores
    the user's edits.
    """
    record = db.get_job(cfg, DEMO_V1)
    assert record is not None, "adopting a job must create its row"
    assert record["document_id"] == document


def test_an_edited_version_is_the_one_a_later_diff_reads(client, cfg, document):
    """`latest_sop_for_job` must return the newest version, edited or not."""
    sop = client.get(f"/api/documents/{document}").json()["sop"]
    sop["steps"][0]["instruction"] = "Edited text that the diff must see."
    client.put(f"/api/documents/{document}", json={"sop": sop})

    latest = db.latest_sop_for_job(cfg, DEMO_V1)
    assert latest is not None
    assert latest.steps[0].instruction == "Edited text that the diff must see."


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


def test_the_same_diff_can_be_run_twice(client, cfg, document):
    """Running the diff must not destroy the comparison it just made.

    Regression. Running a diff saves the merged SOP as the document's newest
    version, attributed to the NEW job. `latest_sop_for_job` then returned that
    merged version for BOTH job ids, so the second run compared the new SOP
    against itself and reported every step unchanged. The headline demo worked
    exactly once per document and then silently went quiet — the worst possible
    failure mode, because nothing errors and the output looks like good news.
    """
    db.create_job(cfg, DEMO_V2)
    payload = {"job_id": DEMO_V2, "offline": True}

    first = client.post(f"/api/documents/{document}/diff", json=payload).json()
    second = client.post(f"/api/documents/{document}/diff", json=payload).json()

    def renamed(body):
        return [e for e in body["diff"]["entries"]
                if any(c["field"] == "ui_element.label" for c in e["field_changes"])]

    assert renamed(first), "first run did not report the rename"
    assert renamed(second), (
        "the rename vanished on the second run — the old side of the diff is "
        "reading the merged version the first run wrote"
    )
    assert second["diff"]["summary"] == first["diff"]["summary"], (
        f"the same two recordings diffed differently on a re-run: "
        f"{first['diff']['summary']} then {second['diff']['summary']}"
    )


def test_the_diff_adds_exactly_one_version(client, cfg, document):
    """Running a diff appends the merged result and nothing else."""
    before = len(client.get(f"/api/documents/{document}/versions").json())

    db.create_job(cfg, DEMO_V2)
    resp = client.post(f"/api/documents/{document}/diff",
                       json={"job_id": DEMO_V2, "offline": True})
    assert resp.status_code == 200, resp.text

    versions = client.get(f"/api/documents/{document}/versions").json()
    assert len(versions) == before + 1, (
        f"the diff added {len(versions) - before} versions: "
        f"{[(v['version'], v['source']) for v in versions]}"
    )
    assert versions[0]["source"] == "merged", (
        "the newest version must be the merged one — the version carrying "
        "preserved edits, not the raw regeneration"
    )


def test_the_upload_path_does_not_version_behind_the_diff(monkeypatch, cfg):
    """The API's background task must tell the runner not to version.

    Regression. `run_pipeline` stored the finished SOP whenever it was given a
    document_id, and the API gives it one on every re-recording — so a single
    upload appended two versions: the raw model output, then the merged result.
    The raw one is the copy with the user's hand edits still missing, so the
    history read as though their notes had vanished for a version.

    This asserts the wiring rather than the outcome. Reaching the line through
    HTTP would mean a real video and a full pipeline run — and the first
    version of this test did exactly that, which called the model for real and
    would have overwritten a hand-restored fixture SOP. A test that costs money
    and damages fixtures is not worth the extra coverage.
    """
    from app.routes import jobs as jobs_route

    passed: dict[str, object] = {}

    def fake_run_pipeline(_cfg, job_id, **kwargs):
        passed.update(kwargs)
        return None

    monkeypatch.setattr(jobs_route, "run_pipeline", fake_run_pipeline)
    jobs_route._run(cfg, "job_probe", False, "doc_probe")

    assert passed.get("store_version") is False, (
        "the upload path must pass store_version=False; the diff endpoint "
        "versions the result, and both doing it appends two per upload"
    )
    assert passed.get("document_id") == "doc_probe", (
        "the document must still be passed through, or the job is never "
        "attached and the next diff has nothing to compare against"
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


def test_a_document_can_be_renamed_and_moved(client, cfg, document):
    resp = client.patch(f"/api/documents/{document}",
                        json={"title": "Approve a refund", "app": "Salesforce"})
    assert resp.status_code == 200, resp.text

    listed = next(d for d in client.get("/api/documents").json()
                  if d["document_id"] == document)
    assert listed["title"] == "Approve a refund"
    assert listed["app"] == "Salesforce"


def test_a_chosen_name_survives_regeneration(client, cfg, document):
    """The sidebar name must not follow whatever the model called the newest
    recording.

    Regression. `save_version` overwrote the document title on every save, so
    a name the user had chosen was replaced the next time a recording came in
    — and with several workflows in one document the sidebar entry silently
    renamed itself as they were added.
    """
    client.patch(f"/api/documents/{document}", json={"title": "Refunds — daily"})

    db.create_job(cfg, DEMO_V2)
    resp = client.post(f"/api/documents/{document}/diff",
                       json={"job_id": DEMO_V2, "offline": True})
    assert resp.status_code == 200, resp.text

    listed = next(d for d in client.get("/api/documents").json()
                  if d["document_id"] == document)
    assert listed["title"] == "Refunds — daily", (
        f"regeneration renamed the document to {listed['title']!r}"
    )


def test_an_unnamed_document_still_takes_its_name_from_the_sop(client, cfg):
    """...but a document nobody has named should adopt the generated title,
    which is what `cli run --save` relies on."""
    doc = db.create_document(cfg)
    assert db.get_document(cfg, doc)["title"] == db.PLACEHOLDER_TITLE

    db.save_version(cfg, doc, sop_fixtures.sop_v1(), job_id=DEMO_V1)
    assert db.get_document(cfg, doc)["title"] == sop_fixtures.sop_v1().title


def test_unknown_ids_are_404_not_500(client):
    assert client.get("/api/documents/doc_nope").status_code == 404
    assert client.get("/api/jobs/job_nope").status_code == 404
    assert client.get("/api/diffs/diff_nope").status_code == 404


def test_a_job_left_running_by_a_restart_is_failed_not_stranded(client, cfg):
    """Jobs run in-process, so nothing survives a restart.

    Without reconciliation these rows stayed `running` forever and the UI
    showed a spinner that could never resolve — with no error anywhere to
    explain why.
    """
    db.create_job(cfg, "job_stranded")
    db.set_job_status(cfg, "job_stranded", "running", stage="structure")

    stranded = db.reconcile_interrupted_jobs(cfg)
    assert "job_stranded" in stranded

    record = client.get("/api/jobs/job_stranded").json()
    assert record["status"] == "failed"
    assert "Interrupted" in (record["error"] or ""), (
        "a stranded job must say why it stopped, not just fail silently"
    )


def test_reconciliation_leaves_finished_and_unstarted_jobs_alone(client, cfg):
    """It must only touch jobs that were mid-flight."""
    db.create_job(cfg, "job_done")
    db.set_job_status(cfg, "job_done", "complete")
    db.create_job(cfg, "job_fresh")  # status 'created' — made, never run

    db.reconcile_interrupted_jobs(cfg)

    assert db.get_job(cfg, "job_done")["status"] == "complete"
    assert db.get_job(cfg, "job_fresh")["status"] == "created"
