"""Node — extract_keyframes

Walks the source video at SAMPLE_FPS using ffmpeg, computes a perceptual
hash for each frame, and keeps a frame as a candidate ONLY when its hash
differs from the previously-kept frame's hash by more than KEYFRAME_THRESHOLD
(Hamming distance). This filters out long stretches of identical UI.

Downloads the video independently of transcribe_raw — small file (typically
<10 MB), parallelism beats coordination overhead.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from video_add_agent.models.input import BucketContext, VideoArtifact
from video_add_agent.state import ErrorInfo, VideoAgentState
from video_add_agent.utils.bucket import download_bytes
from video_add_agent.utils.keyframes import compute_phash, dump_frames, gate_consecutive

logger = logging.getLogger(__name__)

SAMPLE_FPS = 2
KEYFRAME_THRESHOLD = 8


def extract_keyframes_node(state: VideoAgentState) -> dict:
    logger.info("extract_keyframes: start project_id=%s", state.project_id)
    try:
        artifact = VideoArtifact.model_validate(state.video_artifact)
        bucket_ctx = BucketContext.model_validate(state.bucket_context)

        # Download video to a per-run tempdir we hand off to disk.
        # (The dir is intentionally NOT cleaned up here — dedupe_screenshots
        # reads the frame files. persist_output / process exit do final cleanup.)
        run_dir = Path(tempfile.mkdtemp(prefix="video_add_agent_kf_"))
        video_path = run_dir / artifact.bucketPath.split("/")[-1]
        video_path.write_bytes(
            download_bytes(
                bucket_id=bucket_ctx.bucketId,
                folder_id=bucket_ctx.folderId,
                path=artifact.bucketPath,
            )
        )
        logger.info("extract_keyframes: downloaded video (%d bytes)", video_path.stat().st_size)

        frames_dir = run_dir / "frames"
        frame_paths = dump_frames(video_path, frames_dir, fps=SAMPLE_FPS)

        # (path, hash, timestamp_seconds)
        frames_with_hashes = []
        for i, fp in enumerate(frame_paths):
            try:
                h = compute_phash(fp)
            except Exception as exc:
                logger.warning("extract_keyframes: pHash failed for %s: %s", fp, exc)
                continue
            ts = i / SAMPLE_FPS
            frames_with_hashes.append((fp, h, ts))

        candidates = gate_consecutive(frames_with_hashes, threshold=KEYFRAME_THRESHOLD)
        logger.info(
            "extract_keyframes: %d frames sampled → %d candidates (threshold=%d)",
            len(frame_paths), len(candidates), KEYFRAME_THRESHOLD,
        )
        return {"keyframe_candidates": candidates}

    except Exception as exc:
        logger.error("extract_keyframes: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="extract_keyframes")}
