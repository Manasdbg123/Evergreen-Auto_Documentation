"""Stage 2 — optional narration transcript.

Deliberately the weakest link in the chain. The vision path is authoritative:
video is ground truth for *what happened*, narration is only a hint about
*intent and naming*. So every failure here is non-fatal — no audio track, no
model downloaded, a corrupt wav, a crash inside whisper — and the pipeline
continues with an empty transcript rather than refusing to produce an SOP.

Set transcribe.required: true in config to make failures hard instead.
"""

from __future__ import annotations

from typing import Any

from ..models import Transcript, TranscriptSegment, Word
from .base import JobPaths, Stage
from .video import extract_audio


class TranscribeStage(Stage):
    name = "transcribe"
    depends_on = ["ingest"]

    def config_slice(self) -> dict[str, Any]:
        return {"transcribe": self.cfg.transcribe.model_dump()}

    def compute(self, job: JobPaths, inputs: dict[str, Any]) -> dict[str, Any]:
        tc = self.cfg.transcribe

        if not tc.enabled:
            return self._empty("transcription disabled in config")

        meta = inputs["ingest"]
        if not meta.get("has_audio"):
            print("[transcribe] no audio track — continuing with the vision-only path")
            return self._empty("source has no audio track")

        src = job.abs(meta["source_path"])
        wav = job.root / "audio.wav"

        try:
            if not extract_audio(src, wav, self.cfg):
                return self._fail("ffmpeg could not extract an audio track")
        except Exception as exc:
            return self._fail(f"audio extraction failed: {exc}")

        try:
            transcript = self._run_whisper(wav, tc)
        except ImportError:
            return self._fail(
                "faster-whisper is not installed (pip install faster-whisper)"
            )
        except Exception as exc:
            return self._fail(f"transcription failed: {exc}")

        words = sum(len(s.words) for s in transcript.segments)
        print(
            f"[transcribe] {len(transcript.segments)} segments, {words} words, "
            f"language={transcript.language}"
        )
        return {"transcript": transcript.model_dump()}

    # ----------------------------------------------------------------------

    def _run_whisper(self, wav, tc) -> Transcript:
        from faster_whisper import WhisperModel

        print(f"[transcribe] loading whisper '{tc.model}' on {tc.device} ({tc.compute_type})")
        model = WhisperModel(tc.model, device=tc.device, compute_type=tc.compute_type)

        segments, info = model.transcribe(
            str(wav),
            language=tc.language,
            word_timestamps=True,   # the diff and candidate ranking both want these
            vad_filter=True,        # drop long silences rather than hallucinate over them
        )

        out: list[TranscriptSegment] = []
        for s in segments:
            out.append(TranscriptSegment(
                start=round(s.start, 3),
                end=round(s.end, 3),
                text=s.text.strip(),
                words=[
                    Word(
                        text=w.word.strip(),
                        start=round(w.start, 3),
                        end=round(w.end, 3),
                        probability=round(getattr(w, "probability", 0.0), 4),
                    )
                    for w in (s.words or [])
                ],
            ))

        return Transcript(
            available=bool(out),
            language=info.language,
            duration=round(info.duration, 3),
            segments=out,
        )

    def _empty(self, note: str) -> dict[str, Any]:
        return {"transcript": Transcript(available=False, note=note).model_dump(),
                "note": note}

    def _fail(self, note: str) -> dict[str, Any]:
        """A failure the pipeline survives — unless the config says otherwise."""
        if self.cfg.transcribe.required:
            raise RuntimeError(f"Transcription is required but failed: {note}")
        print(f"[transcribe] {note} — continuing without a transcript")
        return self._empty(note)
