# Evergreen

Converts screen recordings into structured, editable SOPs with screenshots, and
shows a reviewable diff when the same workflow is recorded again after a UI change.

Positioning: documentation for software whose source code you don't own. Target
users are support, ops, training and QA teams documenting internal admin panels,
vendor tools, desktop apps and mobile apps — where a screen recording is the only
available source of truth. Deliberately NOT aimed at dev teams documenting their
own repo.

6-day MVP for an internal product assignment. Must demonstrate core value end to
end. Not production-ready. Do not spend time on UI polish, auth, multi-tenancy,
deployment, or infrastructure.

## The single most important outcome

Upload recording v1 → get an SOP → upload recording v2 of the same workflow after
a UI change → see exactly which steps changed, added, removed.

**The diff engine is the product.** If scope must be cut, cut the editor, cut
exports, cut everything else. Never cut the diff.

## Stack

- **Backend:** Python, FastAPI
- **Frontend:** React + Vite + TypeScript, TipTap editor
- **Video:** ffmpeg, OpenCV
- **Transcription:** faster-whisper, local, CPU. No OpenAI API.
- **LLM:** one key, one vendor at a time, chosen by `llm.provider`
  - `anthropic` (the brief's requirement, and the documented default): 
    `claude-haiku-4-5-20251001` classify, `claude-sonnet-5` structure/vision
  - `gemini` (what the demo currently runs on): `gemini-2.5-flash` for both
  - No stage knows which is active. Prompts, schemas and cost accounting are
    shared; only the transport differs.
- **Storage:** local filesystem + SQLite. No cloud, S3, or Postgres.

## Pipeline

```
ingest → transcribe → frames → detect_changes → select_candidates
       → detect_steps → structure → diff → export
```

Every stage is an independently callable module reading and writing disk, so any
stage can be re-run in isolation. Cache aggressively: re-running only the LLM
stage during development must cost nothing.

1. **Ingest** — accept mp4/mov/webm, store under `data/jobs/{job_id}/`, job
   record in SQLite with status tracking.
2. **Audio** — ffmpeg extract, faster-whisper word-level timestamps, persist JSON.
   **Must work with no audio at all.** Vision-only path first; transcript is
   optional enrichment.
3. **Frames + change detection** — sample 2fps, pHash + SSIM between consecutive
   frames. For each change event select the *stable* frame: the first frame after
   the change whose similarity to the next returns above the stability threshold.
   Avoids mid-transition blur. Settle delay 250–500ms, configurable.
4. **Candidate reduction — cost critical.** Never send every sampled frame. Reduce
   to 10–25 candidates before any vision call. Resize to max 1568px long edge.
5. **Step detection** — candidates + transcript to Haiku. Genuine step boundaries
   vs noise (scrolling, hover, alt-tab, incidental mouse movement).
6. **Structuring** — confirmed frames + transcript to Sonnet with vision. Strict
   JSON schema per step: `step_id, order, title, instruction, ui_element,
   expected_result, screenshot_ref, prerequisites, confidence`. No markdown at
   this stage — the structured schema is what makes diffing possible.

   **Conflict rule:** video is ground truth for what happened; transcript is a
   hint for intent and naming. If narration says "Save" and the frame shows
   "Submit", trust the frame, use the transcript only for surrounding
   explanation, and set `confidence: low` so the step surfaces for review.
7. **Editor** — TipTap with custom node types `step` and `screenshot` so the
   document stays structured. Version every save in SQLite. Minimal styling.
8. **Diff engine — the core IP.** Align v2 steps to v1 using semantic similarity
   of `title` + `instruction` and visual similarity of screenshots. Classify each
   step `unchanged | modified | added | removed | reordered`.
   - Align on **text/JSON first**. Fall back to images only when text is
     ambiguous. Image diffing every step is prohibitively expensive.
   - Handle reorder-vs-remove+add explicitly and document the heuristic.
   - **Manual edits on unchanged steps must survive regeneration.** Hard
     requirement — if regenerating destroys hand-written notes, the product is
     useless.
   - Per-step review UI, accept or reject each change individually.
9. **Export** — Markdown and HTML only.

## Config

All tunable values in a single config: sampling rate, similarity thresholds,
settle delay, max candidate frames, step granularity, tone, model per stage. The
client-configurable surface is a selling point of the pitch — make it visible.

## Constraints

- One API key, Anthropic only
- Total API spend under $5 — log estimated token cost per run to stdout
- No Docker, no cloud, no auth
- Runs locally with `uvicorn` and `npm run dev`

## Build order

1. Ingest + frames + change detection, verified on a test video, zero LLM calls
2. Structuring into JSON schema, printed to console
3. Minimal viewer
4. **Diff engine** — allocate the most time here
5. Editor with edit preservation
6. Export

Get stage 1 correct before touching the LLM. If frame selection is bad, nothing
downstream can recover.

## How to work with me

Propose structure and schema before writing implementation code. Build stage by
stage and stop after each so I can test on a real recording. Flag explicitly if a
design choice will make the diff engine harder later.

---

# Working notes (current state)

## Commands

```bash
source .venv/bin/activate && cd server
python -m app.cli new --video ~/rec.mp4       # ingest → candidates
python -m app.cli run <job_id> [--offline]    # whole pipeline → SOP → export
python -m app.cli run <job_id> --save         # ...and store it as a document v1
python -m app.cli diff <old_job> <new_job>    # THE demo. --save to version it
python -m app.cli show <job_id> [--json]      # print the generated SOP
python -m app.cli contact-sheet <job_id>      # visual check of chosen frames
python -m app.cli inspect <job_id>            # what ran, what it found
python -m app.cli <stage> <job_id> --force    # re-run one stage
python -m app.cli estimate-cost --minutes 5   # predict spend, no key needed
python -m pytest tests/ -q                    # 64 tests, no API key needed

# Both halves, as the brief requires:
python -m uvicorn app.main:app --reload --port 8000
cd ../client && npm install && npm run dev    # http://localhost:5173
npm run verify                                # SOP <-> editor round-trip check
```

`--offline` changes a stage's cache fingerprint, so running it on a job whose
SOP was generated for real **overwrites that SOP with placeholder text**. Use a
throwaway job id when exercising the offline path.

## Environment facts

- ffmpeg 4.4.2 at `/usr/bin/ffmpeg`. `resolve_ffmpeg()` prefers system, falls
  back to the `imageio-ffmpeg` bundled binary (no sudo available here).
- Keys load from `.env` at the repo root (gitignored). `GEMINI_API_KEY` is set
  and working; `ANTHROPIC_API_KEY` is not available. `llm.provider: gemini`.
- `llm.offline: auto` in config.yaml makes
  `detect_steps` and `structure` fall back to a deterministic placeholder path
  (`app/llm/offline.py`) so everything downstream is buildable and demoable
  with zero spend. Its output is schema-identical, marked `[offline]`, and
  always `confidence: low`; it never guesses a `ui_element.label`, because a
  fabricated label is indistinguishable from a real one to the diff engine.
  Set `llm.offline: never` for a real client run.
- Model ids in `models.*` carry the date suffix the brief specifies
  (`claude-haiku-4-5-20251001`). Current Anthropic ids are undated
  (`claude-haiku-4-5`); `llm/client.py` catches the resulting 404 and says so.
- `client/` is now React + Vite + TypeScript with TipTap custom `step` and
  `screenshot` nodes. The editor schema is deliberately *closed* — no generic
  paragraph, heading or list — so a user cannot create a block that has nowhere
  to live in `models.Step` and would be dropped on the next save. Saving is a
  field-for-field read, never a parse.
- The pre-existing TypeScript/Next.js implementation is parked in `server/src`,
  snapshotted at commit `0c3dbf1`. The old Next.js `client/` was replaced; it
  remains in git history at that commit.
- `EVERGREEN_DB` overrides `paths.db_path`, so tests and second instances do
  not write into the demo database.

## Measured findings that drove design decisions

- **Fixed SSIM thresholds do not generalise.** On a clean fixture the noise floor
  sat at dissimilarity 0.0135 while real screen changes reached only 0.038. Change
  detection is adaptive: rolling median + MAD, `k=3.5`.
- **SSIM and pHash must be OR'd, not AND'd.** AND missed 2 of 5 real transitions.
- **Neither SSIM nor pHash can identify a screen.** Two *different* screens sharing
  a UI template scored SSIM 0.979 / pHash 2, versus a true duplicate at 0.992 / 0.
  Raising resolution made SSIM *worse*. Hence `ink_mask` in `pipeline/video.py`:
  mask to pixels darker than their local neighbourhood, compare IoU. Same
  measurement separates 0.990 vs 0.756.
- **Cost is ~88% images**, so the per-image token rate dominates everything.
  `candidates.max_frames` is the second lever. Per 5-min video:
  Anthropic $0.137, Gemini Flash $0.014. A $5 budget buys 35 updates on
  Anthropic, 319 on Gemini.
- **Gemini bills images per tile, Anthropic per pixel.** Measured with
  `count_tokens` on one 1280x720 frame: gemini-2.5-flash 259 tokens,
  gemini-3.5-flash 1101, Anthropic 1229 (w*h/750). A shared formula would
  misprice a run by more than the entire budget, so `image_tokens` is part of
  the provider contract and the Gemini numbers live in `cost.image_tokens`.
- **Gemini bills thought tokens as output.** 434 thought tokens to produce a
  14-token classification — the reasoning cost 30x the answer.
  `models.thinking_budget: 0` disables it; `GeminiUsage` folds thoughts into
  output tokens so `cost.jsonl` cannot under-report.
- **Gemini rejects `additionalProperties`** (400). Schemas are authored in
  Anthropic's stricter dialect and narrowed on the way out; safe only because
  Gemini's constrained decoding cannot emit an unnamed key anyway.
- **Free-tier Gemini keys cannot use the Pro models** (429 RESOURCE_EXHAUSTED),
  and `gemini-2.5-pro` is closed to new keys entirely. Flash reads screen text
  correctly on the fixture, which is the only vision capability needed here.

## Open items

- The Anthropic provider is tested against a stub only — no key to run it once.
  The Gemini provider has been run end to end on both fixtures.
- The free-tier Gemini key's daily quota is small; a handful of full runs plus
  judged diffs exhausts it. The offline tiers keep working.
- `demo_v1`'s SOP text was reconstructed by hand after an `--offline` run
  overwrote it and the quota blocked regeneration. Provenance (candidate ids,
  timestamps, phashes, screenshots) is the original; the prose is a faithful
  transcription, and `_fingerprint` is set to `restored-by-hand` so the next
  real run recomputes rather than trusting the cache.
- Background jobs are fire-and-forget in a single process. A server restart
  mid-run leaves a job on `running` forever.
- `tests/test_stage1.py` writes into the real `data/jobs/test_*` rather than a
  tmp dir, so two concurrent pytest runs collide. The API tests are isolated
  (`EVERGREEN_DB`); stage 1 is not.
- Steps are stored as JSON inside a version row, so there is no way to query
  across documents ("every SOP that mentions the Submit button"). Fine at MVP
  scale, wrong at real scale.

## Diff engine findings

- **Do not weight `ui_element.label` as evidence of step identity.** It is the
  thing that *changes* in a UI update, so weighting it high means the harder a
  step changed, the less likely it is recognised as the same step. At weight
  0.20 the Save->Submit case scored 0.51 against a 0.62 threshold and reported
  as remove+add. Now 0.05. Matching signals and change signals are separate.
- **Prose similarity cannot be thresholded.** Measured on fixture pairs,
  same-meaning rewordings scored 0.550-0.937 and genuinely-different pairs
  0.106-0.808 — a 0.259 overlap, no clean cut. Resolved structurally instead:
  `ui_element.label`/`type`/`prerequisites` compared exactly; prose compared
  with tolerance (0.72); `location_hint` is COSMETIC and never drives status.
- **Reorder detection: gate the accusation, not the evidence.** Excluding
  low-confidence pairs from the subsequence hid real moves, because the step
  involved in a swap often also changed and so scored lower — removing it made
  the rest look perfectly ordered. All pairs now participate; only pairs above
  `reorder_min_similarity` can be *called* moved.
- **The LIS tie-break matters.** Equal-length subsequences exist and the choice
  decides which step is accused of moving. Weighted by match confidence, so
  the pairs we are surest about stay anchored. Deterministic across runs.

- **One bug, found four times: a change signal used as evidence of identity.**
  Each instance had the same shape — the harder a step changed, the less likely
  it was recognised as the same step — and each was fixed by separating what
  *matches* a step from what *changed* about it.

  1. `ui_element.label` at weight 0.20 (already noted above). Now 0.05.
  2. `expected_result` fed the embedding via `similarity_text`. It describes a
     step's *consequence*, which changes when the workflow changes AROUND the
     step. Inserting a 2FA screen rewrote sign-in's expected result and dropped
     that pair to 0.500 against a 0.62 threshold. Measured: with it, correct
     pairs bottomed at 0.500 and wrong pairs reached 0.508 — **overlapping**,
     so no threshold works. Without it: 0.744 vs 0.395, a 0.349 margin.
  3. The LLM judge was shown the full `diff_payload`. Given both a renamed
     label and a changed `expected_result`, it ruled "Enter expense details /
     Save" and "Enter expense details / Submit" *different steps* — the demo's
     headline case, regressed to remove+add. `_judgeable` now withholds
     `expected_result`; the judge then names the rename in its own words.
  4. A human's own edit counted against them: heavier editing meant lower
     similarity, so regeneration was most likely to discard the most
     carefully-written steps. `Step.identity_view` judges identity on
     model-written text on both sides, using `StepMeta.generated_values`.

- **Escalation must reach below `match_threshold`.** The ambiguous band's floor
  *was* the match threshold, so a pair scoring under it was never adjudicated —
  meaning the pairs one step away from being reported as remove+add were the
  only ones guaranteed no judge. `diff.escalate_floor` (0.40) fixes it.

- **An abstaining field must not score zero.** When `identity_view` suppresses
  an edited field, its fixed weight became dead loss and dragged a correct pair
  to 0.599 against 0.62 — the edit was neutralised, then punished anyway.
  Lexical weights now renormalise over comparable fields only.

- **Two judges, not one.** Identity ("same step?") and prose ("same meaning?")
  are different questions: a step can be the same step and still have changed,
  or be reworded and unchanged. One similarity number cannot express both. The
  prose judge is batched into a single call for the whole document and settles
  the `field_rewrite_threshold` overlap. A judged diff costs ~$0.0016.

## Verified end to end

`demo_v1 → demo_v2` matches ground truth on both the offline and judged paths:
2 unchanged, 3 modified, 1 added, 1 removed, 1 also moved — the `Save → Submit`
rename reported as one modified step, not remove+add. The same flow through the
API preserves a hand edit across regeneration, and rejecting a change restores
the previous text.
