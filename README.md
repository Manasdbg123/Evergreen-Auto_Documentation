# Doc Diff - Generate and Maintain Doc from Rough Video Recordings

## **TL;DR - Overview**

Turns screen recordings into editable SOPs. Auto-updates docs from new product videos: 

1. Upload video
2. AI extracts meaningful frames 
    1. Find screenshot worthy timestamps with transcription + AI
    2. Find out spike in frame changes and note the timestamps 
    3. Merge results from (a) and (b) to obtain screenshot worthy frames
3. Find stable frames for the timestamps obtained
4. Claude structures it into steps with screenshots
5. Edit in a Notion-style TipTap editor
6. *Regenerate from a new updated recording*
7. Get a visual diff of what changed. 

I Built this to explore the same problem space Clueso works on, end to end

## Watch Demo Video: 

[<img src="https://github.com/user-attachments/assets/aada6841-7da1-4477-9165-92f279cfc2e1" width="1920" height="1080"
/>](https://www.youtube.com/embed/oODRKnCqoRk)

## Flow Diagram

<img width="776" height="660" alt="image" src="https://github.com/user-attachments/assets/23b9d5a9-ff1a-4293-8a5d-0f1edfec51b0" />


## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt

cd server
python -m app.cli doctor    # check ffmpeg, deps, key, storage — before uploading
python -m app.cli demo      # build two fixture recordings and diff them
```

`demo` is the whole product from nothing: it generates two synthetic
recordings of the same workflow — the second after a UI change that renames
Save to Submit, inserts a 2FA screen, drops the attachment step and moves
review ahead of the form — runs both through the pipeline, and checks the diff
against known ground truth. No recording of your own, ~$0.03 on Gemini Flash.
It is also what creates `demo_v1`/`demo_v2`, without which `tests/test_api.py`
skips.

Then, with your own recording:

```bash
python -m app.cli new --video ~/recording.mp4   # stage 1, no API key, no spend
python -m app.cli run <job_id> --save           # generate the SOP, store as v1
python -m app.cli diff <job_v1> <job_v2> --save # what changed. This is the point.

python -m uvicorn app.main:app --reload --port 8000
cd ../client && npm install && npm run dev      # http://localhost:5173
```

`python -m pytest tests/ -q` runs 72 tests and needs no API key.

One key in `.env` at the repo root — `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`,
matching `llm.provider` in `config.yaml`. Without one, `llm.offline: auto` runs
a deterministic placeholder path so the diff, editor and exports are all still
demoable at zero cost.

## What it costs

Images are ~88% of the spend, so the per-image token rate dominates. Per
5-minute video: **$0.137** on Anthropic, **$0.014** on Gemini Flash. A judged
diff adds ~$0.0016. Video decoding, frame sampling, change detection,
transcription and the first two similarity tiers are all local and free.

`python -m app.cli estimate-cost --minutes 5` predicts spend without a key.

## The interesting part

The diff engine is the product, and most of its design came from measurements
that contradicted the obvious approach:

- **Fixed similarity thresholds do not generalise.** The frame-to-frame noise
  floor is a property of the recording, not a constant. Change detection is
  adaptive (rolling median + MAD).
- **Neither SSIM nor pHash can identify a screen.** Two *different* screens
  sharing a UI template scored SSIM 0.979 / pHash 2 against a true duplicate's
  0.992 / 0. Comparing "ink" masks instead separates them 0.990 vs 0.756.
- **Never use a change signal as evidence of identity.** The same bug appeared
  four times: weighting `ui_element.label`, feeding `expected_result` to the
  embedder, showing it to the LLM judge, and letting a user's own edit count
  against them. Each had the shape *the harder a step changed, the less likely
  it was recognised as the same step* — so a renamed button got reported as
  "step removed, new step added", hiding the one thing the reader needed.
- **Prose similarity cannot be thresholded.** Same-meaning rewordings scored
  0.550–0.937 and genuinely different pairs 0.106–0.808 — the ranges overlap,
  so every threshold misclassifies something. Resolved structurally, with an
  LLM judge for the overlap only.

`CLAUDE.md` records the measurements behind each of these.

## Known Flaws & Limitations

- Background jobs are fire-and-forget in a single process. A restart mid-run
  loses the work; the job is now marked `failed` with a "re-run it" message at
  the next startup rather than left on `running` forever, but there is no queue
  and no retry.
- Loading-screen/transitional frames can still slip into both frame selection - mitigated in layers, not eliminated
- Requires the user to re-record the entire video for feature update
- Steps live as JSON inside a version row, so there is no cross-document query
- No auth, no multi-tenancy, no deployment - deliberately out of scope

## What I'd Build Next

- **Version 0.0.2 → Incremental updates via short clips, not full re-recordings:** instead of re-uploading an entire workflow, let the user record just the part that changed (a new step, a modified screen) and splice it into the existing document at the right position.
- **Version 0.0.3 → GitHub Webhook to auto-update the documentation:** Pushes to github triggers a workflow to look for new changes in the product, record the new feature and auto update the docs.

## Proposed Production Architecture

<img width="991" height="660" alt="image 1" src="https://github.com/user-attachments/assets/060b6e48-a671-4a9e-a71c-4f400819989c" />


Presigned S3 uploads (bypass the API tier for large files), SQS-backed job queue with DLQ/redrive for durability and retry, Fargate worker fleet auto-scaled on queue depth (chosen over Lambda for ffmpeg's disk/runtime needs), Postgres + pgvector for step embeddings at real scale, Redis for OTP rate-limiting and diff-result caching, CloudFront in front of private S3 for delivery.
