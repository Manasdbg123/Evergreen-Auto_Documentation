"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import { diffWords } from "diff";
import { fetchDiff } from "@/lib/api";
import type { DiffResult, StepDiffEntry } from "@/lib/types";
import { useUnauthorizedDialog } from "./UnauthorizedDialogContext";
import ErrorBox from "./ErrorBox";

export default function DiffView({
  documentId,
  versionId,
}: {
  documentId: string;
  versionId: string;
}) {
  const [diff, setDiff] = useState<DiffResult | null>(null);
  const { showUnauthorizedDialog } = useUnauthorizedDialog();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchDiff(documentId, versionId, showUnauthorizedDialog)
      .then((result) => {
        if (!cancelled) setDiff(result);
      })
      .catch((err) => {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load diff");
      });
    return () => {
      cancelled = true;
    };
  }, [documentId, versionId]);

  if (error) return <ErrorBox error={error} />;
  if (!diff)
    return (
      <p className="text-sm text-neutral-500 px-6 py-8">Loading diff...</p>
    );

  return (
    <div className="max-w-2xl mx-auto px-6 py-8 space-y-8">
      {diff.stepDiffs.map((entry, i) => (
        <DiffStepEntry key={i} entry={entry} />
      ))}
    </div>
  );
}

function DiffStepEntry({ entry }: { entry: StepDiffEntry }) {
  if (entry.type === "unchanged") {
    return (
      <div>
        <h3 className="text-base font-semibold text-neutral-900 mb-2">
          {entry.newStep?.title}
        </h3>
        {entry.newStep?.screenshotUrl && (
          <div className="relative w-full aspect-video rounded-md overflow-hidden mb-2">
            <Image
              src={entry.newStep.screenshotUrl}
              alt={entry.newStep.title}
              fill
              className="object-contain"
            />
          </div>
        )}
        <p className="text-sm text-neutral-600 leading-relaxed">
          {entry.newStep?.bodyText}
        </p>
      </div>
    );
  }

  if (entry.type === "removed") {
    return (
      <div>
        <h3 className="text-base font-semibold text-neutral-900 mb-2 bg-red-500/20">
          {entry.oldStep?.title}
        </h3>
        {entry.oldStep?.screenshotUrl && (
          <div className="relative w-full aspect-video rounded-md overflow-hidden mb-2 border-2 border-red-500/20">
            <Image
              src={entry.oldStep.screenshotUrl}
              alt={entry.oldStep.title}
              fill
              className="object-contain"
            />
          </div>
        )}
        <p className="text-sm text-neutral-600 leading-relaxed bg-red-500/20">
          {entry.oldStep?.bodyText}
        </p>
      </div>
    );
  }

  if (entry.type === "added") {
    return (
      <div>
        <h3 className="text-base font-semibold text-neutral-900 mb-2 bg-green-500/20">
          {entry.newStep?.title}
        </h3>
        {entry.newStep?.screenshotUrl && (
          <div className="relative w-full aspect-video rounded-md overflow-hidden mb-2 border-2 border-green-500/20">
            <Image
              src={entry.newStep.screenshotUrl}
              alt={entry.newStep.title}
              fill
              className="object-contain"
            />
          </div>
        )}
        <p className="text-sm text-neutral-600 leading-relaxed bg-green-500/20">
          {entry.newStep?.bodyText}
        </p>
      </div>
    );
  }

  // modified — word-level inline diff for title and body, before/after border for image
  return (
    <div>
      <h3 className="text-base font-semibold text-neutral-900 mb-2">
        <WordDiff
          oldText={entry.oldStep?.title ?? ""}
          newText={entry.newStep?.title ?? ""}
        />
      </h3>

      {(entry.oldStep?.screenshotUrl || entry.newStep?.screenshotUrl) && (
        <div className="flex gap-2 mb-2">
          {entry.oldStep?.screenshotUrl && (
            <div className="relative flex-1 aspect-video rounded-md overflow-hidden border-2 border-red-500/20">
              <Image
                src={entry.oldStep.screenshotUrl}
                alt="before"
                fill
                className="object-contain"
              />
            </div>
          )}
          {entry.newStep?.screenshotUrl && (
            <div className="relative flex-1 aspect-video rounded-md overflow-hidden border-2 border-green-500/20">
              <Image
                src={entry.newStep.screenshotUrl}
                alt="after"
                fill
                className="object-contain"
              />
            </div>
          )}
        </div>
      )}

      <p className="text-sm text-neutral-600 leading-relaxed">
        <WordDiff
          oldText={entry.oldStep?.bodyText ?? ""}
          newText={entry.newStep?.bodyText ?? ""}
        />
      </p>
    </div>
  );
}

function WordDiff({ oldText, newText }: { oldText: string; newText: string }) {
  const parts = diffWords(oldText, newText);

  return (
    <>
      {parts.map((part, i) => {
        if (part.added) {
          return (
            <span key={i} className="bg-green-500/20">
              {part.value}
            </span>
          );
        }
        if (part.removed) {
          return (
            <span
              key={i}
              className="bg-red-500/20 line-through decoration-red-700/40"
            >
              {part.value}
            </span>
          );
        }
        return <span key={i}>{part.value}</span>;
      })}
    </>
  );
}
