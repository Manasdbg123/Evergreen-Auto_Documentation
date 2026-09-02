"""Hand-authored SOPs matching the video fixtures, with a known-correct diff.

These are what the diff engine is graded against. They mirror exactly what
`tools/make_test_video.py` renders, so the expected answer is not a guess:

    v1: login -> dashboard -> expense form (Save) -> attach receipt
        -> review -> submitted
    v2: login -> 2FA -> dashboard -> review -> expense form (Submit)
        -> submitted

Ground truth for v1 -> v2:
    login        unchanged
    dashboard    unchanged
    2FA          ADDED
    attach       REMOVED
    expense form MODIFIED   (button Save -> Submit)
    review       position swapped with the form
    submitted    unchanged

Note the phrasing is deliberately *not* identical between versions even for
steps that did not change — a regenerated SOP is rewritten from scratch every
time, so an aligner that only survives verbatim text is useless in practice.
"""

from __future__ import annotations

from app.models import SOP, Confidence, Step, StepMeta, UiElement


def _step(order, title, instruction, el_type, label, hint, expected,
          prereqs=None, lineage=None, confidence=Confidence.high) -> Step:
    meta = StepMeta()
    if lineage:
        meta.lineage_id = lineage
    return Step(
        order=order, title=title, instruction=instruction,
        ui_element=UiElement(type=el_type, label=label, location_hint=hint),
        expected_result=expected, prerequisites=prereqs or [],
        confidence=confidence, meta=meta,
    )


def sop_v1() -> SOP:
    return SOP(
        job_id="demo_v1", document_id="doc_expense", version=1,
        title="Submit an expense request",
        steps=[
            _step(1, "Sign in to the admin console",
                  "Enter your email and password, then click Log in.",
                  "button", "Log in", "bottom right of the sign-in panel",
                  "The dashboard loads.", lineage="ln_login"),
            _step(2, "Open a new request from the dashboard",
                  "On the dashboard, click New request to start an expense claim.",
                  "button", "New request", "bottom right of the dashboard panel",
                  "The new expense request form opens.", lineage="ln_dash"),
            _step(3, "Fill in the expense details",
                  "Enter the amount, choose a category, add any notes, then click Save.",
                  "button", "Save", "bottom right of the form",
                  "The request is saved as a draft.",
                  ["You are on the new expense request form"], lineage="ln_form"),
            _step(4, "Attach the receipt",
                  "Choose the receipt file and click Upload.",
                  "button", "Upload", "bottom right of the attachment panel",
                  "The receipt is attached to the request.", lineage="ln_attach"),
            _step(5, "Review and confirm the request",
                  "Check the approver is correct, then click Confirm.",
                  "button", "Confirm", "bottom right of the review panel",
                  "The request is sent for approval.", lineage="ln_review"),
            _step(6, "Finish",
                  "Click Back to dashboard to return to the main screen.",
                  "button", "Back to dashboard", "bottom right of the panel",
                  "You are back on the dashboard.", lineage="ln_done"),
        ],
    )


def sop_v2() -> SOP:
    """The same workflow after the UI change. Deliberately reworded."""
    return SOP(
        job_id="demo_v2", document_id="doc_expense", version=2,
        title="Submit an expense request",
        steps=[
            _step(1, "Log in to the admin console",
                  "Type your email address and password, then press Log in.",
                  "button", "Log in", "lower right of the login card",
                  "You are taken to the dashboard."),
            _step(2, "Complete two-factor verification",
                  "Enter the six-digit code from your authenticator app and click Verify.",
                  "button", "Verify", "bottom right of the verification panel",
                  "Your identity is confirmed and the dashboard loads."),
            _step(3, "Start a new request from the dashboard",
                  "From the dashboard, choose New request to begin an expense claim.",
                  "button", "New request", "lower right of the dashboard",
                  "A new expense request is started."),
            _step(4, "Review and confirm the request",
                  "Confirm the approver is right, then click Confirm.",
                  "button", "Confirm", "lower right of the review panel",
                  "The request moves on for approval."),
            _step(5, "Fill in the expense details",
                  "Provide the amount, pick a category, write any notes, then click Submit.",
                  "button", "Submit", "lower right of the form",
                  "The request is submitted.",
                  ["You are on the new expense request form"]),
            _step(6, "Finish",
                  "Choose Back to dashboard to return to the main screen.",
                  "button", "Back to dashboard", "lower right of the panel",
                  "The dashboard is shown again."),
        ],
    )


#: title fragment -> expected status, for grading.
EXPECTED = {
    "sign in": "unchanged",       # reworded only
    "dashboard": "unchanged",     # reworded only
    "two-factor": "added",
    "attach": "removed",
    "expense details": "modified",  # Save -> Submit
    "finish": "unchanged",
}
