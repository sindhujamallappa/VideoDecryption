"""Node — handle_error  (conditional, reachable from any node)

Updates the stage row to 'rejected' with a truncated errorDesc, then
returns an empty dict so the graph can exit cleanly via END.
"""
from __future__ import annotations

import logging

from video_add_agent.models.input import BucketContext
from video_add_agent.state import VideoAgentState
from video_add_agent.utils.entities import update_stage

logger = logging.getLogger(__name__)

_ERROR_DESC_MAX = 2_000


def handle_error_node(state: VideoAgentState) -> dict:
    error = state.error
    message = error.message if error else "Unknown error"
    failed_node = error.failed_node if error else "unknown"
    project_id = state.project_id
    stage_id = state.stage_id

    logger.error(
        "handle_error: project_id=%s stage_id=%s failed_node=%s error=%s",
        project_id, stage_id, failed_node, message,
    )

    try:
        bucket_ctx = BucketContext.model_validate(state.bucket_context)
        error_desc = f"[{failed_node}] {message}"[:_ERROR_DESC_MAX]
        update_stage(
            folder_id=bucket_ctx.folderId,
            stage_id=stage_id,
            payload={"status": "rejected", "errorDesc": error_desc},
        )
    except Exception:
        logger.error("handle_error: could not update stage row", exc_info=True)

    return {}
