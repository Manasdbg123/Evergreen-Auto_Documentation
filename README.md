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


## Known Flaws & Limitations

- Fire-and-forget background jobs - Volatile
- Loading-screen/transitional frames can still slip into both frame selection - mitigated in layers, not eliminated
- Requires the user to re-record the entire video for feature update

## What I'd Build Next

- **Version 0.0.2 → Incremental updates via short clips, not full re-recordings:** instead of re-uploading an entire workflow, let the user record just the part that changed (a new step, a modified screen) and splice it into the existing document at the right position.
- **Version 0.0.3 → GitHub Webhook to auto-update the documentation:** Pushes to github triggers a workflow to look for new changes in the product, record the new feature and auto update the docs.

## Proposed Production Architecture

<img width="991" height="660" alt="image 1" src="https://github.com/user-attachments/assets/060b6e48-a671-4a9e-a71c-4f400819989c" />


Presigned S3 uploads (bypass the API tier for large files), SQS-backed job queue with DLQ/redrive for durability and retry, Fargate worker fleet auto-scaled on queue depth (chosen over Lambda for ffmpeg's disk/runtime needs), Postgres + pgvector for step embeddings at real scale, Redis for OTP rate-limiting and diff-result caching, CloudFront in front of private S3 for delivery.
