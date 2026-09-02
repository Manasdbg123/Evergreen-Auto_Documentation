"use client";

import { useState, useRef } from "react";
import ErrorBox from "./ErrorBox";

interface Props {
  titleField?: boolean;
  submitLabel: string;
  onSubmit: (file: File, title: string) => Promise<void>;
}

export default function VideoUploadForm({
  titleField = false,
  submitLabel,
  onSubmit,
}: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setIsDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }

  async function handleSubmit() {
    if (!file) return;
    setIsSubmitting(true);
    setError(null);
    try {
      await onSubmit(file, title || file.name);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setIsSubmitting(false);
    }
  }

  return (
    <div className="w-full max-w-xl">
      {titleField && (
        <input
          type="text"
          placeholder="Document title (optional)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="w-full mb-4 px-4 py-2 border border-neutral-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-neutral-900"
        />
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer border-2 border-dashed rounded-xl p-10 text-center transition-colors ${
          isDragging
            ? "border-neutral-900 bg-neutral-100"
            : "border-neutral-300"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept="video/mp4,video/quicktime,video/webm"
          className="hidden"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        {file ? (
          <p className="text-sm text-neutral-800 font-medium">{file.name}</p>
        ) : (
          <p className="text-sm text-neutral-500">
            Drag a video here, or click to browse
          </p>
        )}
      </div>

      {error && <ErrorBox error={error} />}

      <button
        onClick={handleSubmit}
        disabled={!file || isSubmitting}
        className="mt-6 w-full py-2.5 rounded-lg bg-neutral-900 text-white text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {isSubmitting ? "Uploading..." : submitLabel}
      </button>
    </div>
  );
}
