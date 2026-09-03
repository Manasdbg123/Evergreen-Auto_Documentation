"""Stage-by-stage driver. No server needed.

    python -m app.cli new --video ~/rec.mp4        # create a job, run stage 1
    python -m app.cli stage1 <job_id>              # everything free of charge
    python -m app.cli run <job_id>                 # the whole pipeline
    python -m app.cli run <job_id> --offline       # ...with no API calls at all
    python -m app.cli frames <job_id>              # run one stage
    python -m app.cli show <job_id> [--json]       # print the generated SOP
    python -m app.cli inspect <job_id>             # what has run, and what it found
    python -m app.cli contact-sheet <job_id>       # visual check of chosen frames

Stages 1-5 cost nothing and need no key. Only `detect_steps` and `structure`
call Anthropic, and both accept --offline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_config
from .models import new_id
from .pipeline.base import JobPaths, read_stage
from .pipeline.detect_changes import DetectChangesStage
from .pipeline.detect_steps import DetectStepsStage
from .pipeline.export import ExportStage
from .pipeline.frames import FramesStage
from .pipeline.ingest import IngestStage, place_upload
from .pipeline.llm_stage import LLMStage
from .pipeline.select_candidates import SelectCandidatesStage
from .pipeline.structure import StructureStage
from .pipeline.transcribe import TranscribeStage

STAGES = {
    "ingest": IngestStage,
    "transcribe": TranscribeStage,
    "frames": FramesStage,
    "detect_changes": DetectChangesStage,
    "select_candidates": SelectCandidatesStage,
    "detect_steps": DetectStepsStage,
    "structure": StructureStage,
    "export": ExportStage,
}

#: Everything that runs without an API key and without spending anything.
STAGE1 = ["ingest", "transcribe", "frames", "detect_changes", "select_candidates"]

#: The two stages that can call Anthropic.
LLM_STAGES = ["detect_steps", "structure"]


def _build(stage_name: str, cfg, offline: bool = False):
    """Construct a stage, passing --offline only to the stages that accept it."""
    stage_cls = STAGES[stage_name]
    if issubclass(stage_cls, LLMStage):
        return stage_cls(cfg, offline=offline or None)
    return stage_cls(cfg)


def cmd_new(args) -> int:
    cfg = load_config()
    video = Path(args.video).expanduser()
    if not video.exists():
        print(f"No such file: {video}", file=sys.stderr)
        return 1
    job_id = args.job_id or new_id("job")
    dest = place_upload(cfg, job_id, video, video.name)
    print(f"job_id: {job_id}")
    print(f"stored: {dest}")
    return cmd_stage1(argparse.Namespace(job_id=job_id, force=args.force))


def cmd_stage(args) -> int:
    cfg = load_config()
    _build(args.stage, cfg, getattr(args, "offline", False)).run(
        args.job_id, force=args.force)
    return 0


def cmd_stage1(args) -> int:
    cfg = load_config()
    for name in STAGE1:
        _build(name, cfg).run(args.job_id, force=args.force)
    print("\nStage 1 complete. Inspect with:")
    print(f"  python -m app.cli inspect {args.job_id}")
    print(f"  python -m app.cli contact-sheet {args.job_id}")
    print(f"  python -m app.cli run {args.job_id}    # continue into the LLM stages")
    return 0


def cmd_run(args) -> int:
    """The whole pipeline, ingest through export, for one job.

    Shares `pipeline.runner` with the API so the two cannot drift — the CLI
    growing a stage the server forgets is the kind of difference that only
    surfaces during a demo.
    """
    from . import db
    from .pipeline.runner import run_pipeline

    cfg = load_config()

    # Persisting is what makes the *next* recording diffable: a job whose SOP
    # was never versioned has nothing to be the previous version of.
    document_id = args.document
    if args.save and not document_id:
        document_id = db.create_document(cfg)

    run_pipeline(cfg, args.job_id, offline=args.offline, force=args.force,
                 document_id=document_id)

    job = JobPaths(cfg, args.job_id)
    data = read_stage(job, "structure") or {}
    print(f"\n{data.get('count', 0)} steps generated. Actual API spend this job: "
          f"${_spent(job):.4f}")
    if document_id:
        print(f"saved as {document_id}")
        print(f"  python -m app.cli diff {args.job_id} <new_job_id> --save")
    print(f"  python -m app.cli show {args.job_id}")
    return 0


def cmd_diff(args) -> int:
    """The demo: what changed between two recordings of the same workflow."""
    from . import db
    from .pipeline.diff_stage import DiffStage

    cfg = load_config()
    stage = DiffStage(cfg, old_job_id=args.old_job_id, offline=args.offline)
    data = stage.run(args.new_job_id, force=args.force)

    summary = data.get("summary") or {}
    if not summary:
        print("\nNo differences found.")

    # Store the diff against the document so the review UI can accept or reject
    # each entry, and so the merged SOP — the one carrying preserved edits —
    # becomes the new current version.
    if args.save:
        from .models import DiffResult, SOP

        old = db.get_job(cfg, args.old_job_id) or {}
        document_id = old.get("document_id")
        if not document_id:
            print("\nThe previous job is not attached to a document — nothing to "
                  "version against.\nRe-run it with --save to create one:",
                  file=sys.stderr)
            print(f"  python -m app.cli run {args.old_job_id} --save",
                  file=sys.stderr)
            return 1

        result = DiffResult.model_validate(data["diff"])
        merged = SOP.model_validate(data["merged_sop"])
        db.attach_job_to_document(cfg, args.new_job_id, document_id)
        version = db.save_version(cfg, document_id, merged, source="merged",
                                  job_id=args.new_job_id)
        diff_id = db.save_diff(cfg, document_id, result)
        print(f"\nsaved {document_id} v{version} and diff {diff_id}")
    return 0


def _spent(job: JobPaths) -> float:
    """Total from cost.jsonl — what was really spent, not what was estimated."""
    if not job.cost_log.exists():
        return 0.0
    total = 0.0
    for line in job.cost_log.read_text().splitlines():
        try:
            total += json.loads(line).get("usd", 0.0)
        except json.JSONDecodeError:
            continue
    return total


def cmd_show(args) -> int:
    """Print the generated SOP as text. The stage-2 acceptance check."""
    cfg = load_config()
    job = JobPaths(cfg, args.job_id)
    data = read_stage(job, "structure")
    if not data:
        print(f"No SOP for {args.job_id} — run `structure` first.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data["sop"], indent=2))
        return 0

    sop = data["sop"]
    print(f"\n{sop['title']}")
    print("=" * len(sop["title"]))
    if sop.get("summary"):
        print(f"{sop['summary']}\n")
    for step in sop["steps"]:
        el = step["ui_element"]
        print(f"\n{step['order']}. {step['title']}   [{step['confidence']}]")
        print(f"   {step['instruction']}")
        if el.get("label"):
            print(f"   element: {el['type']} \"{el['label']}\" — {el['location_hint']}")
        if step.get("expected_result"):
            print(f"   expect:  {step['expected_result']}")
        for pre in step.get("prerequisites", []):
            print(f"   needs:   {pre}")
        if step.get("screenshot_ref"):
            print(f"   shot:    {step['screenshot_ref']}")
        if (step.get("meta") or {}).get("conflict"):
            print(f"   CONFLICT: {step['meta']['conflict']}")
    print()
    return 0


def cmd_inspect(args) -> int:
    cfg = load_config()
    job = JobPaths(cfg, args.job_id)
    if not job.root.exists():
        print(f"No job at {job.root}", file=sys.stderr)
        return 1

    print(f"job {args.job_id}  ->  {job.root}")
    for name in STAGE1 + LLM_STAGES + ["diff", "export"]:
        data = read_stage(job, name)
        if data is None:
            print(f"  [ ] {name}")
            continue
        detail = ""
        if name == "ingest":
            detail = f"{data.get('duration', 0):.1f}s, audio={data.get('has_audio')}"
        elif name == "frames":
            detail = f"{data.get('count')} frames"
        elif name == "detect_changes":
            detail = f"{data.get('count')} events (from {data.get('raw_count')} raw)"
        elif name == "select_candidates":
            detail = f"{data.get('count')} candidates"
        elif name == "transcribe":
            t = data.get("transcript") or {}
            detail = (f"{len(t.get('segments', []))} segments"
                      if t.get("available") else f"none ({data.get('note', '')})")
        elif name == "detect_steps":
            detail = (f"{data.get('accepted')} steps of {data.get('count')} "
                      f"candidates [{data.get('mode')}]")
        elif name == "structure":
            detail = (f"{data.get('count')} steps, "
                      f"{data.get('low_confidence', 0)} low-confidence "
                      f"[{data.get('mode')}]")
        elif name == "diff":
            summary = data.get("summary") or {}
            detail = (f"vs {data.get('old_job_id')}: "
                      + (", ".join(f"{v} {k}" for k, v in sorted(summary.items()))
                         or "no entries"))
        elif name == "export":
            detail = f"{data.get('count')} steps -> {data.get('markdown')}, {data.get('html')}"
        print(f"  [x] {name:18s} {detail}  ({data.get('_elapsed_sec')}s)")

    cands = read_stage(job, "select_candidates")
    if cands and cands.get("candidates"):
        print("\ncandidate frames:")
        for c in cands["candidates"]:
            print(f"  #{c['order']:02d}  t={c['timestamp']:7.2f}s  {c['frame_path']}")
    return 0


def cmd_contact_sheet(args) -> int:
    """Tile the chosen frames into one image. The fastest way to judge stage 1."""
    import cv2
    import numpy as np

    cfg = load_config()
    job = JobPaths(cfg, args.job_id)
    data = read_stage(job, "select_candidates")
    if not data or not data.get("candidates"):
        print("No candidates — run select_candidates first.", file=sys.stderr)
        return 1

    tiles = []
    for c in data["candidates"]:
        img = cv2.imread(str(job.abs(c["frame_path"])))
        if img is None:
            continue
        img = cv2.resize(img, (480, int(480 * img.shape[0] / img.shape[1])))
        label = f"#{c['order']} t={c['timestamp']:.1f}s"
        cv2.rectangle(img, (0, 0), (img.shape[1], 26), (0, 0, 0), -1)
        cv2.putText(img, label, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        tiles.append(img)

    if not tiles:
        print("Could not read any candidate frames.", file=sys.stderr)
        return 1

    cols = 3
    h = max(t.shape[0] for t in tiles)
    tiles = [cv2.copyMakeBorder(t, 0, h - t.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(30, 30, 30)) for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.full_like(tiles[0], 30))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    sheet = np.vstack(rows)

    out = job.root / "contact_sheet.jpg"
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f"wrote {out}")
    return 0


def cmd_estimate_cost(args) -> int:
    """Predict spend before committing to it. No API key, no calls."""
    from .llm.cost import (capped_image_tokens, estimate_diff, estimate_job,
                           format_estimate)

    cfg = load_config()
    print(f"\nimage tokens per frame (16:9, capped at {cfg.frames.llm_max_edge_px}px): "
          f"{capped_image_tokens(cfg):,}\n")

    job = estimate_job(cfg, args.minutes, has_transcript=not args.no_audio)
    print(format_estimate(cfg, job, f"ONE {args.minutes:g}-MINUTE VIDEO"))
    steps = job[1].images

    text_diff = estimate_diff(cfg, steps)
    print("\n" + format_estimate(cfg, text_diff, "DIFF (text tiers only — the normal path)"))

    worst = estimate_diff(cfg, steps, visual=cfg.diff.max_visual_comparisons)
    print("\n" + format_estimate(cfg, worst, "DIFF (worst case — visual fallback maxed)"))

    per_video = sum(r.usd for r in job)
    per_update = per_video + sum(r.usd for r in text_diff)
    worst_update = per_video + sum(r.usd for r in worst)

    # In production the previous SOP is already stored, so an update costs one
    # video plus a diff — the old recording is never reprocessed.
    print(f"\n  first time (create the document)   = ${per_video:.4f}")
    print(f"  each update (1 video + diff)      = ${per_update:.4f}")
    print(f"  each update, worst case           = ${worst_update:.4f}")
    print(f"\n  a $5 budget buys ~{int(5 / per_update)} updates "
          f"(~{int(5 / worst_update)} worst case)\n")
    print("  video ingestion, frame sampling, change detection and")
    print("  transcription are local — they cost nothing.\n")
    return 0


def cmd_config(args) -> int:
    print(json.dumps(load_config().model_dump(), indent=2, default=str))
    return 0


#: What the fixture pair is built to contain. `make_test_video.V1/V2` encode a
#: renamed button (Save -> Submit), a 2FA screen inserted after login, the
#: attachment step dropped, and review/confirm moved ahead of the form.
DEMO_GROUND_TRUTH = {"unchanged": 2, "modified": 3, "added": 1, "removed": 1}


def cmd_doctor(args) -> int:
    """Check this machine can run the pipeline, before anything is uploaded.

    Every failure here used to surface halfway through a run, as a traceback
    from whichever stage happened to need the missing piece first.
    """
    import importlib.util
    import shutil

    cfg = load_config()
    problems = 0

    def report(ok: bool, label: str, detail: str = "", fatal: bool = True) -> None:
        nonlocal problems
        mark = "ok  " if ok else ("FAIL" if fatal else "warn")
        print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
        if not ok and fatal:
            problems += 1

    print("\nEvergreen preflight\n")

    print(" environment")
    report(sys.version_info >= (3, 10), "python >= 3.10",
           f"running {sys.version.split()[0]}")

    from .config import resolve_ffmpeg
    try:
        ffmpeg = resolve_ffmpeg(cfg)
        report(bool(ffmpeg), "ffmpeg", str(ffmpeg))
    except Exception as exc:
        report(False, "ffmpeg", f"{type(exc).__name__}: {exc}")

    for mod, why in (("cv2", "video decode and SSIM"),
                     ("imagehash", "perceptual hashing"),
                     ("skimage", "structural similarity"),
                     ("scipy", "optimal step assignment")):
        report(importlib.util.find_spec(mod) is not None, f"{mod}", why)

    for mod, why in (("faster_whisper", "transcription (optional; "
                                        "the vision-only path is primary)"),
                     ("sentence_transformers", "similarity tier 2 (optional; "
                                               "falls back to lexical)")):
        report(importlib.util.find_spec(mod) is not None, mod, why, fatal=False)

    print("\n storage")
    try:
        cfg.jobs_root.mkdir(parents=True, exist_ok=True)
        probe = cfg.jobs_root / ".write_probe"
        probe.write_text("ok")
        probe.unlink()
        report(True, "data directory writable", str(cfg.data_root))
    except Exception as exc:
        report(False, "data directory writable", f"{type(exc).__name__}: {exc}")
    report(True, "database", str(cfg.db_file))

    print("\n model provider")
    from .config import provider_key
    key = provider_key(cfg)
    report(True, "provider", f"{cfg.llm.provider} (llm.provider in config.yaml)")
    if key:
        report(True, f"{cfg.key_env_var} present", f"...{key[-4:]}")
    else:
        report(cfg.llm.offline != "never", f"{cfg.key_env_var} not set",
               "llm.offline is 'never', so a run will fail"
               if cfg.llm.offline == "never"
               else f"llm.offline is '{cfg.llm.offline}', so runs fall back to "
                    "the placeholder path at zero cost",
               fatal=cfg.llm.offline == "never")

    print("\n demo fixtures")
    have = [j for j in ("demo_v1", "demo_v2")
            if read_stage(JobPaths(cfg, j), "structure") is not None]
    report(len(have) == 2, "demo_v1 and demo_v2 generated",
           "present" if len(have) == 2
           else "run `python -m app.cli demo` to build them "
                "(no API key needed; test_api.py skips without them)",
           fatal=False)

    if problems:
        print(f"\n{problems} blocking problem(s).\n")
        return 1
    print("\nAll clear.\n")
    return 0


def cmd_demo(args) -> int:
    """Build the fixture recordings and run the whole product over them.

    The reproducible proof, from nothing: it generates two synthetic screen
    recordings of the same workflow — the second after a UI change that renames
    Save to Submit, inserts a 2FA screen, drops the attachment step and moves
    review ahead of the form — runs both through the pipeline, and diffs them
    against known ground truth. No recording of your own and no setup beyond a
    key. On a clean checkout it is also what creates `demo_v1`/`demo_v2`,
    without which `tests/test_api.py` skips.

    `--offline` removes the API dependency but cannot demonstrate the diff; see
    the note it prints.
    """
    import tempfile

    from . import db
    from .pipeline.diff_stage import DiffStage
    from .pipeline.runner import run_pipeline

    cfg = load_config()
    v1_id, v2_id = f"{args.prefix}_v1", f"{args.prefix}_v2"

    # Guard rail, and not a theoretical one. `--offline` changes a stage's cache
    # fingerprint, so running this over jobs whose SOPs were generated for real
    # replaces that prose with placeholder text. That has already cost one
    # hand-written demo.
    existing = [j for j in (v1_id, v2_id)
                if read_stage(JobPaths(cfg, j), "structure") is not None]
    if existing and not args.force:
        print(f"{' and '.join(existing)} already have generated SOPs.\n"
              f"Re-running would overwrite them with placeholder text.\n\n"
              f"  python -m app.cli demo --force        # overwrite anyway\n"
              f"  python -m app.cli demo --prefix scratch   # leave them alone\n"
              f"  python -m app.cli diff {v1_id} {v2_id}   # just show the diff",
              file=sys.stderr)
        return 1

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import make_test_video as fixture  # noqa: E402

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        print("\n[1/4] building fixture recordings")
        for job_id, screens in ((v1_id, fixture.V1), (v2_id, fixture.V2)):
            video = tmp_dir / f"{job_id}.mp4"
            fixture.build(screens, video)
            place_upload(cfg, job_id, video, video.name)

        label = "offline, no API calls" if args.offline else f"via {cfg.llm.provider}"
        print(f"\n[2/4] first recording -> SOP ({label})")
        document_id = args.document or db.create_document(
            cfg, title="Submit an expense request")
        run_pipeline(cfg, v1_id, offline=args.offline, force=True,
                     document_id=document_id)

        print("\n[3/4] second recording, after the UI change -> SOP")
        run_pipeline(cfg, v2_id, offline=args.offline, force=True)

    print("\n[4/4] diff")
    data = DiffStage(cfg, old_job_id=v1_id, offline=args.offline).run(
        v2_id, force=True)
    summary = data.get("summary") or {}

    from .models import DiffResult, SOP
    result = DiffResult.model_validate(data["diff"])
    merged = SOP.model_validate(data["merged_sop"])
    db.attach_job_to_document(cfg, v2_id, document_id)
    version = db.save_version(cfg, document_id, merged, source="merged",
                              job_id=v2_id)
    db.save_diff(cfg, document_id, result)

    print(f"\n  document {document_id} is now at v{version}\n")
    width = max(len(k) for k in DEMO_GROUND_TRUTH)
    mismatched = []
    for key, expected in DEMO_GROUND_TRUTH.items():
        actual = int(summary.get(key, 0) or 0)
        flag = "" if actual == expected else f"   <- expected {expected}"
        if actual != expected:
            mismatched.append(key)
        print(f"  {key:<{width}}  {actual}{flag}")
    print(f"\n  {int(summary.get('reordered', 0) or 0)} step(s) also reported "
          f"as moved")

    if args.offline:
        # Not a failure, and worth spelling out rather than leaving the reader
        # to conclude the diff engine is broken. The offline path never invents
        # a ui_element.label or reads screen text, so both SOPs come out as the
        # same placeholder prose and there is genuinely nothing to diff. The
        # diff engine needs a model to have read the screens first.
        print("\n  --offline generates placeholder step text without reading the\n"
              "  screenshots, so both SOPs are identical and the diff correctly\n"
              "  reports no changes. To see the real diff, run without --offline\n"
              f"  (about $0.03 total on {cfg.llm.provider}).\n")
        return 0

    print(f"\n  python -m app.cli show {v2_id}")
    print(f"  python -m app.cli contact-sheet {v2_id}")
    print(f"  python -m app.cli inspect {v2_id}\n")

    if mismatched:
        print(f"Diff does not match the fixture's ground truth "
              f"({', '.join(mismatched)}).\n", file=sys.stderr)
        return 1
    print("  Matches the fixture's ground truth.\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(prog="app.cli", description="Evergreen pipeline driver")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="create a job from a video and run stage 1")
    n.add_argument("--video", required=True)
    n.add_argument("--job-id", default=None)
    n.add_argument("--force", action="store_true")
    n.set_defaults(func=cmd_new)

    s1 = sub.add_parser("stage1", help="ingest -> frames -> detect_changes -> select_candidates")
    s1.add_argument("job_id")
    s1.add_argument("--force", action="store_true")
    s1.set_defaults(func=cmd_stage1)

    r = sub.add_parser("run", help="the whole pipeline: ingest -> export")
    r.add_argument("job_id")
    r.add_argument("--force", action="store_true")
    r.add_argument("--offline", action="store_true",
                   help="skip every API call; emit schema-valid placeholder text")
    r.add_argument("--save", action="store_true",
                   help="store the SOP as v1 of a new document, so a later "
                        "recording can be diffed against it")
    r.add_argument("--document", default=None,
                   help="store as the next version of an existing document")
    r.set_defaults(func=cmd_run)

    df = sub.add_parser("diff", help="what changed between two recordings")
    df.add_argument("old_job_id", help="the job holding the previous version")
    df.add_argument("new_job_id", help="the job holding the new recording")
    df.add_argument("--force", action="store_true")
    df.add_argument("--offline", action="store_true",
                    help="offline tiers only — no LLM judge")
    df.add_argument("--save", action="store_true",
                    help="store the diff and the merged SOP as a new version")
    df.set_defaults(func=cmd_diff)

    for name in STAGES:
        sp = sub.add_parser(name, help=f"run the {name} stage")
        sp.add_argument("job_id")
        sp.add_argument("--force", action="store_true")
        if name in LLM_STAGES:
            sp.add_argument("--offline", action="store_true",
                            help="skip the API call; emit placeholder text")
        sp.set_defaults(func=cmd_stage, stage=name)

    sh = sub.add_parser("show", help="print the generated SOP")
    sh.add_argument("job_id")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=cmd_show)

    i = sub.add_parser("inspect", help="show what has run for a job")
    i.add_argument("job_id")
    i.set_defaults(func=cmd_inspect)

    cs = sub.add_parser("contact-sheet", help="tile chosen frames into one image")
    cs.add_argument("job_id")
    cs.set_defaults(func=cmd_contact_sheet)

    ec = sub.add_parser("estimate-cost", help="predict API spend for a video length")
    ec.add_argument("--minutes", type=float, default=5.0)
    ec.add_argument("--no-audio", action="store_true", help="assume no transcript")
    ec.set_defaults(func=cmd_estimate_cost)

    c = sub.add_parser("config", help="print the resolved config")
    c.set_defaults(func=cmd_config)

    dr = sub.add_parser("doctor", help="check this machine can run the pipeline")
    dr.set_defaults(func=cmd_doctor)

    dm = sub.add_parser("demo", help="build fixture recordings and run the whole "
                                     "product over them — no API key needed")
    dm.add_argument("--prefix", default="demo",
                    help="job id prefix; default 'demo' creates demo_v1/demo_v2")
    dm.add_argument("--document", default=None,
                    help="attach to an existing document instead of a new one")
    dm.add_argument("--force", action="store_true",
                    help="overwrite existing SOPs for these job ids")
    dm.add_argument("--offline", action="store_true",
                    help="no API calls at all — proves the pipeline runs, but "
                         "the diff will be empty because no screen text is read")
    dm.set_defaults(func=cmd_demo)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
