"""Stage 9 — Markdown and HTML.

The last stage, and deliberately the dumbest one. Export is a pure function of
`models.SOP` with no model call, no heuristics and no state: everything
interesting already happened, and anything clever here would be logic living
outside the schema the diff engine reads.

Screenshots are referenced relatively so the exports directory can be zipped
and sent to somebody, and the images still resolve.
"""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any

from ..models import SOP, Confidence, Step
from .base import JobPaths, Stage


class ExportStage(Stage):
    name = "export"
    depends_on = ["structure"]

    def __init__(self, cfg=None, sop: SOP | None = None):
        super().__init__(cfg)
        #: Export this SOP instead of the raw `structure` output. The API
        #: passes the stored, hand-edited version — exporting the generated
        #: text when an edited version exists would quietly ship the wrong
        #: document.
        self.sop = sop

    def config_slice(self) -> dict[str, Any]:
        return {"writing": self.cfg.writing.model_dump(),
                "override": self.sop.model_dump() if self.sop else None}

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        sop = self.sop or SOP.model_validate(inputs["structure"]["sop"])

        md_path = job.exports / "sop.md"
        html_path = job.exports / "sop.html"
        md_path.write_text(to_markdown(sop, job), encoding="utf-8")
        html_path.write_text(to_html(sop, job), encoding="utf-8")

        print(f"[export] {len(sop.steps)} steps -> {md_path.name}, {html_path.name}")
        return {
            "count": len(sop.steps),
            "markdown": job.rel(md_path),
            "html": job.rel(html_path),
        }


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------


def to_markdown(sop: SOP, job: JobPaths | None = None) -> str:
    out: list[str] = [f"# {sop.title}", ""]
    if sop.summary:
        out += [sop.summary, ""]

    for step in sop.steps:
        out.append(f"## {step.order}. {step.title}")
        out.append("")
        if step.prerequisites:
            out.append("**Before you start:**")
            out += [f"- {p}" for p in step.prerequisites]
            out.append("")
        if step.instruction:
            out += [step.instruction, ""]

        el = step.ui_element
        if el.label:
            where = f" ({el.location_hint})" if el.location_hint else ""
            out += [f"**{el.type.capitalize()}:** `{el.label}`{where}", ""]
        if step.expected_result:
            out += [f"**Result:** {step.expected_result}", ""]

        shot = _screenshot_rel(step, job)
        if shot:
            out += [f"![{_escape_md(step.title)}]({shot})", ""]

        # Surfaced in the export, not just the UI: a low-confidence step is one
        # a human still needs to check, and that fact must not be lost the
        # moment the document leaves the tool.
        if step.confidence == Confidence.low:
            note = step.meta.conflict or "the model was unsure about this step"
            out += [f"> **Check this step.** {note}", ""]

    return "\n".join(out).rstrip() + "\n"


def _escape_md(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

STYLE = """
:root { color-scheme: light dark; }
body { font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 46rem; margin: 3rem auto; padding: 0 1.25rem; }
h1 { font-size: 1.9rem; margin-bottom: .25rem; }
.summary { color: #666; margin-bottom: 2.5rem; }
.step { margin: 0 0 2.75rem; padding-top: 1.5rem; border-top: 1px solid #e3e3e3; }
.step h2 { font-size: 1.15rem; margin: 0 0 .5rem; }
.step h2 .n { color: #999; margin-right: .4rem; }
.element { display: inline-block; background: #f4f4f5; border-radius: 5px;
           padding: .2rem .55rem; font-size: .87rem; margin: .35rem 0; }
.element code { font-weight: 600; }
.result { color: #444; font-size: .94rem; }
.prereq { font-size: .9rem; color: #555; }
.check { border-left: 3px solid #e0a800; background: #fdf7e3; padding: .6rem .9rem;
         font-size: .9rem; margin-top: .75rem; border-radius: 0 4px 4px 0; }
img { max-width: 100%; border: 1px solid #ddd; border-radius: 6px; margin-top: .9rem; }
@media (prefers-color-scheme: dark) {
  body { background: #16171a; color: #e6e6e6; }
  .summary, .result, .prereq { color: #a5a5a5; }
  .step { border-top-color: #2c2d31; }
  .element { background: #26272b; }
  .check { background: #2a2415; border-left-color: #c99a00; }
  img { border-color: #303136; }
}
"""


def to_html(sop: SOP, job: JobPaths | None = None) -> str:
    e = html.escape
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(sop.title)}</title><style>{STYLE}</style>",
        "</head><body>",
        f"<h1>{e(sop.title)}</h1>",
    ]
    if sop.summary:
        parts.append(f'<p class="summary">{e(sop.summary)}</p>')

    for step in sop.steps:
        parts.append('<section class="step">')
        parts.append(
            f'<h2><span class="n">{step.order}.</span>{e(step.title)}</h2>'
        )
        if step.prerequisites:
            items = "".join(f"<li>{e(p)}</li>" for p in step.prerequisites)
            parts.append(f'<div class="prereq">Before you start:<ul>{items}</ul></div>')
        if step.instruction:
            parts.append(f"<p>{e(step.instruction)}</p>")

        el = step.ui_element
        if el.label:
            where = f" — {e(el.location_hint)}" if el.location_hint else ""
            parts.append(
                f'<div class="element">{e(el.type)}: <code>{e(el.label)}</code>{where}</div>'
            )
        if step.expected_result:
            parts.append(f'<p class="result"><strong>Result:</strong> '
                         f'{e(step.expected_result)}</p>')

        shot = _screenshot_rel(step, job)
        if shot:
            parts.append(f'<img src="{e(shot)}" alt="{e(step.title)}">')

        if step.confidence == Confidence.low:
            note = step.meta.conflict or "the model was unsure about this step"
            parts.append(f'<div class="check"><strong>Check this step.</strong> '
                         f'{e(note)}</div>')
        parts.append("</section>")

    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------


def _screenshot_rel(step: Step, job: JobPaths | None) -> str | None:
    """Path from the exports directory to the screenshot.

    Relative, so `exports/` plus `screenshots/` can be zipped together and the
    images still resolve on the other machine. An absolute path here would
    produce a document full of broken images the moment it was shared, which is
    the only thing anyone ever does with an export.
    """
    if not step.screenshot_ref:
        return None
    if job is None:
        return step.screenshot_ref
    try:
        return os.path.relpath(job.abs(step.screenshot_ref), job.exports).replace(
            os.sep, "/"
        )
    except ValueError:  # pragma: no cover - different drives on Windows
        return str(Path(step.screenshot_ref).as_posix())
