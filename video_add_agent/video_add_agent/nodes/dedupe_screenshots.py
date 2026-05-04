"""Node — dedupe_screenshots

Clusters keyframe candidates by perceptual-hash similarity and uploads one
representative per cluster to the UiPath bucket. The result is a small set
of meaningfully-different screenshots, with no duplicates.

Representative selection prefers a frame near a filtered_transcript segment
midpoint (so screenshots match narrated steps). When filtered_transcript
isn't available yet (parallel branch hasn't finished), falls back to the
cluster-median by timestamp; align_steps refines later.
"""
from __future__ import annotations

import logging
from pathlib import Path

from video_add_agent.models.input import BucketContext
from video_add_agent.state import ErrorInfo, Screenshot, VideoAgentState
from video_add_agent.utils.bucket import upload_bytes
from video_add_agent.utils.keyframes import cluster_by_hash, pick_representative

logger = logging.getLogger(__name__)

DEDUPE_THRESHOLD = 5


def dedupe_screenshots_node(state: VideoAgentState) -> dict:
    logger.info("dedupe_screenshots: start project_id=%s", state.project_id)
    try:
        candidates = state.keyframe_candidates
        if not candidates:
            logger.warning("dedupe_screenshots: no candidates — skipping")
            return {"screenshots": []}

        bucket_ctx = BucketContext.model_validate(state.bucket_context)
        clusters = cluster_by_hash(candidates, threshold=DEDUPE_THRESHOLD)
        logger.info(
            "dedupe_screenshots: %d candidates → %d clusters",
            len(candidates), len(clusters),
        )

        midpoints = [
            (s.start + s.end) / 2.0 for s in state.filtered_transcript
        ] or None

        screenshots: list[Screenshot] = []
        for cluster_idx, cluster in enumerate(clusters, start=1):
            rep = pick_representative(cluster, transcript_midpoints=midpoints)
            # Leading slash is required: senior's review screen reconstructs
            # the bucket path via imageBucketPath() which returns
            # /projects/... — the SDK's getReadUri is strict about matching
            # that exact form (see senior's src/uipath/buckets.ts:119-132).
            # Without the leading slash, signed-URL lookup 404s and images
            # don't render even though upload accepted either form.
            bucket_path = (
                f"/projects/{state.project_id}/raw/image-{cluster_idx}/"
                f"screenshot-{cluster_idx:02d}.jpg"
            )
            try:
                content = Path(rep.frame_path).read_bytes()
            except FileNotFoundError:
                logger.warning(
                    "dedupe_screenshots: frame file missing %s — skipping cluster %d",
                    rep.frame_path, cluster_idx,
                )
                continue
            upload_bytes(
                bucket_id=bucket_ctx.bucketId,
                folder_id=bucket_ctx.folderId,
                path=bucket_path,
                content=content,
                mime_type="image/jpeg",
            )
            screenshots.append(
                Screenshot(
                    timestamp=rep.timestamp,
                    phash=rep.phash,
                    bucket_path=bucket_path,
                    cluster_size=len(cluster),
                )
            )

        logger.info("dedupe_screenshots: uploaded %d screenshots", len(screenshots))
        return {"screenshots": screenshots}

    except Exception as exc:
        logger.error("dedupe_screenshots: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="dedupe_screenshots")}
