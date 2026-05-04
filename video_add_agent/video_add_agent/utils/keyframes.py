"""Pure helpers for perceptual-hash-based keyframe extraction and clustering.

Used by `extract_keyframes` and `dedupe_screenshots` nodes. Splitting these
out keeps the node bodies thin and makes the algorithms easy to unit-test
without ffmpeg or PIL involvement.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Iterable

import imagehash
from PIL import Image

from video_add_agent.state import ScreenshotCandidate

logger = logging.getLogger(__name__)


def dump_frames(video_path: Path, output_dir: Path, fps: int = 2) -> list[Path]:
    """Use ffmpeg to dump video frames at `fps` frames-per-second.

    Returns the sorted list of frame paths. ffmpeg must be on PATH.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vf", f"fps={fps},scale=640:360",
        "-q:v", "5",
        str(output_dir / "frame_%05d.jpg"),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg frame dump failed: {result.stderr.decode(errors='replace')[:500]}"
        )
    frames = sorted(output_dir.glob("frame_*.jpg"))
    logger.info("dump_frames: dumped %d frames at %d fps", len(frames), fps)
    return frames


def compute_phash(frame_path: Path) -> imagehash.ImageHash:
    """Perceptual hash of a single frame (16-char hex when stringified)."""
    with Image.open(frame_path) as im:
        return imagehash.phash(im)


def parse_phash(hex_str: str) -> imagehash.ImageHash:
    """Inverse of `str(phash)` — used to compare hashes loaded from state."""
    return imagehash.hex_to_hash(hex_str)


def phash_distance(a: str | imagehash.ImageHash, b: str | imagehash.ImageHash) -> int:
    """Hamming distance between two phashes. Accepts hex strings or hash objects."""
    ha = parse_phash(a) if isinstance(a, str) else a
    hb = parse_phash(b) if isinstance(b, str) else b
    return ha - hb  # imagehash.ImageHash defines __sub__ as Hamming distance


def gate_consecutive(
    frames_with_hashes: Iterable[tuple[Path, imagehash.ImageHash, float]],
    threshold: int,
) -> list[ScreenshotCandidate]:
    """Walk frames in order; keep one only when its hash distance from the
    previously-kept hash is strictly greater than `threshold`.

    `frames_with_hashes` items are (frame_path, phash, timestamp_seconds).
    """
    kept: list[ScreenshotCandidate] = []
    last_hash: imagehash.ImageHash | None = None
    for path, h, ts in frames_with_hashes:
        if last_hash is None or (h - last_hash) > threshold:
            kept.append(
                ScreenshotCandidate(timestamp=ts, phash=str(h), frame_path=str(path))
            )
            last_hash = h
    return kept


def cluster_by_hash(
    candidates: list[ScreenshotCandidate],
    threshold: int,
) -> list[list[ScreenshotCandidate]]:
    """Single-link clustering: a candidate joins an existing cluster if its
    distance to ANY member of that cluster is ≤ `threshold`. Otherwise it
    starts a new cluster. O(n²) but n is small (low hundreds at most).
    """
    clusters: list[list[ScreenshotCandidate]] = []
    for cand in candidates:
        ch = parse_phash(cand.phash)
        joined = False
        for cluster in clusters:
            if any(phash_distance(ch, parse_phash(m.phash)) <= threshold for m in cluster):
                cluster.append(cand)
                joined = True
                break
        if not joined:
            clusters.append([cand])
    return clusters


def pick_representative(
    cluster: list[ScreenshotCandidate],
    transcript_midpoints: list[float] | None = None,
) -> ScreenshotCandidate:
    """Choose one candidate per cluster.

    Preference order:
      1. If `transcript_midpoints` is non-empty: the cluster member with the
         smallest distance to ANY transcript midpoint.
      2. Otherwise: the cluster's median by timestamp.
    """
    if not cluster:
        raise ValueError("cluster is empty")
    if transcript_midpoints:
        return min(
            cluster,
            key=lambda c: min(abs(c.timestamp - m) for m in transcript_midpoints),
        )
    sorted_by_time = sorted(cluster, key=lambda c: c.timestamp)
    return sorted_by_time[len(sorted_by_time) // 2]
