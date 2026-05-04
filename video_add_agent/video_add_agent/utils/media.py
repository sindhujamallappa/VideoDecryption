"""Audio extraction + Whisper transcription helpers.

ffmpeg must be on PATH in the execution environment.
openai-whisper runs as a local model — no API key required.

Frame-extraction helpers used to live here; that work moved to
`utils/keyframes.py` for the perceptual-hash-based screenshot pipeline.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import whisper  # openai-whisper (local model)

logger = logging.getLogger(__name__)


@dataclass
class WhisperSegment:
    start: float   # seconds
    end: float
    text: str


def extract_audio(video_path: Path, output_path: Optional[Path] = None) -> Path:
    if output_path is None:
        output_path = video_path.with_suffix(".wav")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn",                          # no video
        "-acodec", "pcm_s16le",
        "-ar", "16000",                 # 16 kHz — optimal for Whisper
        "-ac", "1",                     # mono
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr.decode()[:500]}")
    logger.debug("extract_audio: wrote %s", output_path)
    return output_path


def run_whisper(audio_path: Path, model_size: str = "base") -> list[WhisperSegment]:
    logger.info("run_whisper: loading model=%s", model_size)
    model = whisper.load_model(model_size)
    result = model.transcribe(str(audio_path), verbose=False, fp16=False)
    segments = [
        WhisperSegment(
            start=float(seg["start"]),
            end=float(seg["end"]),
            text=str(seg["text"]).strip(),
        )
        for seg in result["segments"]
    ]
    logger.info("run_whisper: %d segments", len(segments))
    return segments


def segments_to_timestamped_text(segments: list[WhisperSegment]) -> str:
    lines: list[str] = []
    for seg in segments:
        mm = int(seg.start // 60)
        ss = int(seg.start % 60)
        lines.append(f"[{mm:02d}:{ss:02d}] {seg.text}")
    return "\n".join(lines)
