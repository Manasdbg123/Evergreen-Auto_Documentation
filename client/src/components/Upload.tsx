import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { JobRecord } from "../types";

interface Props {
  /** Given, this recording becomes the next version of that document. */
  documentId?: string;
  label: string;
  onComplete: (jobId: string) => void;
}

const STAGES = [
  "ingest",
  "transcribe",
  "frames",
  "detect_changes",
  "select_candidates",
  "detect_steps",
  "structure",
  "export",
];

/**
 * Upload a recording and follow the pipeline.
 *
 * Polled rather than streamed: the pipeline is a background task in a
 * single-process server, and a websocket would be more moving parts than an
 * MVP needs to show a stage name changing.
 */
export function Upload({ documentId, label, onComplete }: Props) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [finished, setDone] = useState<{ jobId: string; spend: number } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const done = useRef(false);

  useEffect(() => {
    if (!jobId || done.current) return;
    const timer = setInterval(async () => {
      try {
        const record = await api.getJob(jobId);
        setJob(record);
        if (record.status === "complete") {
          done.current = true;
          clearInterval(timer);
          onComplete(jobId);
          // Collapse the panel once the document is open. The eight stage
          // chips are worth watching while the pipeline runs and are clutter
          // afterwards — but the spend is worth keeping on screen, so it
          // becomes a single quiet line. A failure keeps its full panel,
          // because that one still needs reading.
          setDone({ jobId, spend: record.spend_usd ?? 0 });
          setJobId(null);
          setJob(null);
        } else if (record.status === "failed") {
          done.current = true;
          clearInterval(timer);
          setError(record.error ?? "The pipeline failed.");
        }
      } catch (e) {
        setError((e as Error).message);
      }
    }, 1200);
    return () => clearInterval(timer);
  }, [jobId, onComplete]);

  async function upload(file: File) {
    setBusy(true);
    setError("");
    setDone(null);
    done.current = false;
    try {
      const result = await api.upload(file, { documentId });
      setJobId(result.job_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const currentStage = job?.stage;
  const reached = currentStage ? STAGES.indexOf(currentStage) : -1;

  return (
    <div className="upload">
      <label className="filebutton">
        {busy ? "Uploading…" : label}
        <input
          type="file"
          accept="video/mp4,video/quicktime,video/webm,.mkv"
          disabled={busy || (!!jobId && !done.current)}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload(file);
          }}
        />
      </label>

      {jobId && (
        <div className="progress">
          <code>{jobId}</code>
          <div className="stages">
            {STAGES.map((stage, i) => (
              <span
                key={stage}
                className={
                  job?.status === "complete" || i < reached
                    ? "stage done"
                    : i === reached
                      ? "stage active"
                      : "stage"
                }
              >
                {stage}
              </span>
            ))}
          </div>
          {job?.spend_usd !== undefined && job.spend_usd > 0 && (
            <span className="hint">spent ${job.spend_usd.toFixed(4)}</span>
          )}
        </div>
      )}

      {finished && (
        <p className="finished">
          Done · <code>{finished.jobId}</code>
          {finished.spend > 0 && <> · ${finished.spend.toFixed(4)}</>}
        </p>
      )}

      {error && <p className="error">{error}</p>}
    </div>
  );
}
