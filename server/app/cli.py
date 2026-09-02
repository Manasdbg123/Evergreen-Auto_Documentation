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
    """The whole pipeline, ingest through structure, for one job."""
    cfg = load_config()
    for name in STAGE1 + LLM_STAGES:
        _build(name, cfg, args.offline).run(args.job_id, force=args.force)

    job = JobPaths(cfg, args.job_id)
    data = read_stage(job, "structure") or {}
    spent = _spent(job)
    print(f"\n{data.get('count', 0)} steps generated. Actual API spend this job: "
          f"${spent:.4f}")
    print(f"  python -m app.cli show {args.job_id}")
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

    r = sub.add_parser("run", help="the whole pipeline: ingest -> structure")
    r.add_argument("job_id")
    r.add_argument("--force", action="store_true")
    r.add_argument("--offline", action="store_true",
                   help="skip every API call; emit schema-valid placeholder text")
    r.set_defaults(func=cmd_run)

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

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
