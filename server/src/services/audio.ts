import ffmpeg from "fluent-ffmpeg";
import path from "path";
import { nanoid } from "nanoid";
import fs from "fs";

const TMP_DIR = path.resolve("tmp");
const PAUSE_GAP_THRESHOLD_SEC = 0.5; // a gap this long between words signals a natural pause — a likely step boundary

export interface TranscriptWord {
  word: string;
  startSec: number;
  endSec: number;
}

export interface TranscriptSegment {
  text: string;
  startSec: number;
  endSec: number;
}

export function extractAudioTrack(videoPath: string): Promise<string> {
  return new Promise((resolve, reject) => {
    fs.mkdirSync(TMP_DIR, { recursive: true });
    const outputPath = path.join(TMP_DIR, `${nanoid()}.wav`);

    ffmpeg(videoPath)
      .noVideo()
      .audioCodec("pcm_s16le")
      .audioFrequency(16000)
      .format("wav")
      .output(outputPath)
      .on("end", () => resolve(outputPath))
      .on("error", reject)
      .run();
  });
}

export function hasAudioTrack(videoPath: string): Promise<boolean> {
  return new Promise((resolve, reject) => {
    ffmpeg.ffprobe(videoPath, (err, metadata) => {
      if (err) return reject(err);
      resolve(metadata.streams.some((s) => s.codec_type === "audio"));
    });
  });
}

// Now requests WORD-level granularity, not just sentence-level —
// this is what lets us detect pauses inside a single long sentence,
// rather than only at sentence boundaries Whisper decides on its own.
export async function transcribeWithWordTimestamps(audioPath: string): Promise<{
  words: TranscriptWord[];
  segments: TranscriptSegment[];
}> {
  const formData = new FormData();
  formData.append("file", new Blob([fs.readFileSync(audioPath)]), "audio.wav");
  formData.append("model", "whisper-1");
  formData.append("response_format", "verbose_json");
  formData.append("timestamp_granularities[]", "word");
  formData.append("timestamp_granularities[]", "segment");

  const res = await fetch("https://api.openai.com/v1/audio/transcriptions", {
    method: "POST",
    headers: { Authorization: `Bearer ${process.env.OPENAI_API_KEY}` },
    body: formData,
  });

  if (!res.ok) {
    throw new Error(
      `Whisper transcription failed: ${res.status} ${await res.text()}`,
    );
  }

  const json = (await res.json()) as {
    words?: { word: string; start: number; end: number }[];
    segments?: { text: string; start: number; end: number }[];
  };

  return {
    words: (json.words ?? []).map((w) => ({
      word: w.word,
      startSec: w.start,
      endSec: w.end,
    })),
    segments: (json.segments ?? []).map((s) => ({
      text: s.text.trim(),
      startSec: s.start,
      endSec: s.end,
    })),
  };
}

// Finds natural pauses BETWEEN words (not just sentence ends) — each
// meaningful gap is a candidate step boundary. This catches "click add
// contact, [pause] fill in the name, [pause] the email" even though
// it's all one grammatical sentence with no punctuation-level break.
export function detectPauseBoundaries(words: TranscriptWord[]): number[] {
  const boundaries: number[] = [];

  for (let i = 0; i < words.length - 1; i++) {
    const gap = words[i + 1].startSec - words[i].endSec;
    if (gap >= PAUSE_GAP_THRESHOLD_SEC) {
      boundaries.push(words[i].endSec); // the moment right after the word preceding the pause
    }
  }

  return boundaries;
}

// Maps a timestamp back to the nearest surrounding words, for giving
// Claude readable narration context (used in aiStructuring.ts)
export function getNearbyWords(
  words: TranscriptWord[],
  timestampSec: number,
  windowSec = 3,
): string {
  return words
    .filter((w) => Math.abs(w.startSec - timestampSec) <= windowSec)
    .map((w) => w.word)
    .join("")
    .trim();
}

export async function getAudioDerivedTimestamps(videoPath: string): Promise<{
  timestamps: number[];
  words: TranscriptWord[];
  segments: TranscriptSegment[];
}> {
  const hasAudio = await hasAudioTrack(videoPath).catch(() => false);

  if (!hasAudio) {
    console.log("No audio track detected — proceeding visual-only.");
    return { timestamps: [], words: [], segments: [] };
  }

  const audioPath = await extractAudioTrack(videoPath);

  try {
    const { words, segments } = await transcribeWithWordTimestamps(audioPath);
    const pauseTimestamps = detectPauseBoundaries(words);
    const sentenceEndTimestamps = segments.map((s) => s.endSec);

    // union both signals — sentence ends AND mid-sentence pauses
    const allTimestamps = [
      ...new Set([...pauseTimestamps, ...sentenceEndTimestamps]),
    ].sort((a, b) => a - b);

    return { timestamps: allTimestamps, words, segments };
  } finally {
    fs.unlinkSync(audioPath);
  }
}
