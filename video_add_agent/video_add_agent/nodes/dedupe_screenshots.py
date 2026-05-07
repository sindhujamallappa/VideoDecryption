"""Node — dedupe_screenshots

Clusters keyframe candidates by perceptual-hash similarity, uploads one
representative per cluster to the UiPath bucket, and (Fix 4) classifies
each upload via the vision LLM so generate_sections has grounded
keystroke descriptions instead of hallucinations from `projectName`.

Representative selection prefers a frame near a filtered_transcript segment
midpoint (so screenshots match narrated steps). When filtered_transcript
isn't available yet (parallel branch hasn't finished), falls back to the
cluster-median by timestamp; align_steps refines later.

Vision analysis is cached to bucket at
`/projects/{projectId}/raw/image-{N}/vision.json` so retries don't
re-spend tokens. The classification feeds into score_quality (Fix 5).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from video_add_agent.models.input import BucketContext
from video_add_agent.state import ErrorInfo, Screenshot, VideoAgentState
from video_add_agent.utils.bucket import download_bytes, upload_bytes
from video_add_agent.utils.keyframes import cluster_by_hash, pick_representative
from video_add_agent.utils.llm import (
    build_image_block,
    build_llm,
    call_llm_multimodal,
    strip_json_fence,
)

logger = logging.getLogger(__name__)

DEDUPE_THRESHOLD = 5
_VISION_MODEL = "anthropic.claude-opus-4-7"

# Both env-overridable so the published process can be tuned in
# Orchestrator without a republish. Defaults sized to fit a 60-min
# serverless tier on a 3-hour video; lower if running on a tighter cap.
MAX_CLUSTERS = int(os.environ.get("VIDEO_AGENT_MAX_CLUSTERS", "80"))
VISION_CONCURRENCY = int(os.environ.get("VIDEO_AGENT_VISION_CONCURRENCY", "8"))

_VISION_SYSTEM = """\
You are analysing a single frame extracted from a process-walkthrough video.
Identify what is shown, using ONLY visual evidence — do not infer from any
file name, path, project name, or other context. Read text from the image
(title bars, window chrome, form labels) literally.

Return ONLY a JSON object with these keys:
{
  "classification": "screen_recording" | "meeting_frame" | "presentation_slide" | "other",
  "app_name": <string from a title bar / window chrome, or null if none visible>,
  "ui_state": <brief description: "form", "dashboard", "login page", "dialog", "table", "report viewer", or similar>,
  "visible_data": <brief description of field labels and values visible; redact PII like names, emails, account numbers>,
  "likely_action": <one short phrase based on cursor position, active field, highlighted button>,
  "screen_recording_confidence": <float 0.0-1.0; 1.0 = clearly an application UI>
}

Classification rules:
- "screen_recording" if the frame is a captured application UI (window chrome, form fields, menus, browser tabs, table rows visible)
- "meeting_frame" if it shows webcams, video-call participants, people in a room, or a Zoom/Teams/Meet UI featuring video tiles
- "presentation_slide" if it's a slide deck (title + bullet points, diagrams, large text)
- "other" for everything else (black frames, transitions, blurred mid-scrolls)

Do NOT invent app names. If no app name is visible in title bar or window
chrome, set app_name to null. Do NOT use the file name."""


async def _classify_one_screenshot(
    llm: Any,
    image_bytes: bytes,
    bucket_id: int,
    folder_id: int,
    cache_path: str,
) -> dict[str, Any]:
    """Vision LLM call with bucket-cache for idempotent retries."""
    try:
        cached_raw = download_bytes(bucket_id=bucket_id, folder_id=folder_id, path=cache_path)
        cached = json.loads(cached_raw.decode("utf-8"))
        cached["from_cache"] = True
        logger.debug("dedupe_screenshots: vision cache hit for %s", cache_path)
        return cached
    except Exception:
        # cache miss — proceed
        pass

    images = [build_image_block(image_bytes, mime="image/jpeg")]
    try:
        raw = await call_llm_multimodal(
            llm,
            _VISION_SYSTEM,
            "Analyse this frame. Return ONLY the JSON object.",
            images,
        )
        parsed = json.loads(strip_json_fence(raw))
    except Exception as exc:
        logger.warning(
            "dedupe_screenshots: vision call failed for %s — %s", cache_path, exc
        )
        # Conservative fallback so the pipeline keeps moving — mark
        # uncertain rather than guessing a class.
        parsed = {
            "classification": "other",
            "app_name": None,
            "ui_state": None,
            "visible_data": None,
            "likely_action": None,
            "screen_recording_confidence": 0.0,
            "vision_error": str(exc),
        }

    parsed["from_cache"] = False
    try:
        upload_bytes(
            bucket_id=bucket_id,
            folder_id=folder_id,
            path=cache_path,
            content=json.dumps(parsed, ensure_ascii=False, indent=2).encode("utf-8"),
            mime_type="application/json",
        )
    except Exception as exc:
        logger.warning(
            "dedupe_screenshots: failed to cache vision result at %s — %s",
            cache_path, exc,
        )
    return parsed


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

        # Time-uniform downsampling. For long meetings cluster_by_hash can
        # return 200+ clusters; capping bounds vision-LLM cost so the job
        # fits a serverless timeout. Chronological spacing preserves coverage
        # across the meeting flow rather than concentrating around long-
        # displayed UI states (size-based selection would skew that way).
        total_clusters = len(clusters)
        if total_clusters > MAX_CLUSTERS:
            clusters = sorted(clusters, key=lambda c: c[0].timestamp)
            step = total_clusters / MAX_CLUSTERS
            clusters = [clusters[int(i * step)] for i in range(MAX_CLUSTERS)]
            logger.info(
                "dedupe_screenshots: capped %d → %d clusters (time-uniform downsample)",
                total_clusters, MAX_CLUSTERS,
            )

        midpoints = [
            (s.start + s.end) / 2.0 for s in state.filtered_transcript
        ] or None

        # Stage 1: upload representatives (synchronous as before).
        upload_records: list[tuple[int, str, bytes, Any]] = []
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
            upload_records.append((cluster_idx, bucket_path, content, rep))

        # Stage 2: vision analysis in parallel (with bucket cache).
        # A single LLM client is reused across calls.
        screenshots: list[Screenshot] = []
        if upload_records:
            llm = build_llm(model_name=_VISION_MODEL)
            vision_paths = [
                f"/projects/{state.project_id}/raw/image-{idx}/vision.json"
                for idx, _, _, _ in upload_records
            ]

            sem = asyncio.Semaphore(VISION_CONCURRENCY)

            async def _bounded(content_bytes: bytes, vision_path: str) -> Any:
                async with sem:
                    return await _classify_one_screenshot(
                        llm,
                        content_bytes,
                        bucket_ctx.bucketId,
                        bucket_ctx.folderId,
                        vision_path,
                    )

            async def _gather_vision() -> list[Any]:
                tasks = [
                    _bounded(content, vision_path)
                    for (_, _, content, _), vision_path in zip(
                        upload_records, vision_paths
                    )
                ]
                return await asyncio.gather(*tasks, return_exceptions=True)

            vision_results = asyncio.run(_gather_vision())

            for (cluster_idx, bucket_path, _, rep), result, cluster in zip(
                upload_records, vision_results, clusters[: len(upload_records)]
            ):
                vision_payload: dict[str, Any] | None = None
                if isinstance(result, dict):
                    vision_payload = result
                else:
                    logger.warning(
                        "dedupe_screenshots: vision exception cluster %d: %s",
                        cluster_idx, result,
                    )
                screenshots.append(
                    Screenshot(
                        timestamp=rep.timestamp,
                        phash=rep.phash,
                        bucket_path=bucket_path,
                        cluster_size=len(cluster),
                        vision_analysis=vision_payload,
                    )
                )

            classified_summary = _summarise_classifications(screenshots)
            logger.info(
                "dedupe_screenshots: vision classified %d screenshots — %s",
                len(screenshots), classified_summary,
            )
        else:
            logger.warning(
                "dedupe_screenshots: no successful uploads — skipping vision pass"
            )

        return {"screenshots": screenshots}

    except Exception as exc:
        logger.error("dedupe_screenshots: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="dedupe_screenshots")}


def _summarise_classifications(screenshots: list[Screenshot]) -> dict[str, int]:
    counts: dict[str, int] = {
        "screen_recording": 0,
        "meeting_frame": 0,
        "presentation_slide": 0,
        "other": 0,
        "unclassified": 0,
    }
    for s in screenshots:
        if not s.vision_analysis:
            counts["unclassified"] += 1
            continue
        cls = s.vision_analysis.get("classification") or "other"
        counts[cls] = counts.get(cls, 0) + 1
    return counts
