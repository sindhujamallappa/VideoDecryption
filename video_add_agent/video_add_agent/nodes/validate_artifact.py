"""Node 1 — validate_artifact

Validates the input contract, confirms the video file exists in the bucket,
and marks the stage as 'running' in Data Fabric.
"""
from __future__ import annotations

import logging

from video_add_agent.models.input import BucketContext, VideoArtifact
from video_add_agent.state import ErrorInfo, VideoAgentState
from video_add_agent.utils.bucket import check_file_exists
from video_add_agent.utils.entities import update_stage

logger = logging.getLogger(__name__)


def validate_artifact_node(state: VideoAgentState) -> dict:
    logger.info(
        "validate_artifact: start project_id=%s stage_id=%s",
        state.project_id, state.stage_id,
    )
    try:
        artifact = VideoArtifact.model_validate(state.video_artifact)
        bucket_ctx = BucketContext.model_validate(state.bucket_context)

        if not artifact.bucketPath:
            raise ValueError("videoArtifact.bucketPath is empty")
        if not artifact.id:
            raise ValueError("videoArtifact.id is empty")

        logger.info("validate_artifact: checking bucket path=%s", artifact.bucketPath)
        exists = check_file_exists(
            bucket_id=bucket_ctx.bucketId,
            folder_id=bucket_ctx.folderId,
            path=artifact.bucketPath,
        )
        if not exists:
            raise FileNotFoundError(
                f"Video artifact not found in bucket: {artifact.bucketPath}"
            )

        update_stage(
            folder_id=bucket_ctx.folderId,
            stage_id=state.stage_id,
            payload={"status": "running"},
        )
        logger.info("validate_artifact: stage set to running")
        return {}

    except Exception as exc:
        logger.error("validate_artifact: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="validate_artifact")}
