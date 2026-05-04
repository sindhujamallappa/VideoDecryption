"""Node — persist_output

Serialises content.json and content.md, uploads both to the ADD output
bucket paths, then updates the AgentifyStage row to `awaiting_approval`.

Screenshots are already in the bucket (uploaded by dedupe_screenshots);
this node only persists the textual ADD payload.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from video_add_agent.models.input import ActiveAddTemplate, BucketContext
from video_add_agent.models.template import TemplateDefinition
from video_add_agent.nodes.generate_sections import _make_fallback_blocks
from video_add_agent.state import ErrorInfo, VideoAgentState
from video_add_agent.utils.bucket import upload_bytes
from video_add_agent.utils.entities import update_stage
from video_add_agent.utils.markdown import stitch_sections_md

logger = logging.getLogger(__name__)

_CONTENT_MD_PREVIEW_CHARS = 9_500


def persist_output_node(state: VideoAgentState) -> dict:
    logger.info("persist_output: start project_id=%s", state.project_id)
    try:
        project_id = state.project_id
        stage_id = state.stage_id
        sections: dict[str, str] = dict(state.sections)
        bucket_ctx = BucketContext.model_validate(state.bucket_context)
        template = ActiveAddTemplate.model_validate(state.active_add_template)

        try:
            tmpl_def = TemplateDefinition.model_validate_json(template.definition)
            all_blocks = tmpl_def.all_blocks()
        except Exception:
            all_blocks = _make_fallback_blocks()

        content_json_path = f"projects/{project_id}/stages/add/output/content.json"
        upload_bytes(
            bucket_id=bucket_ctx.bucketId,
            folder_id=bucket_ctx.folderId,
            path=content_json_path,
            content=json.dumps(sections, ensure_ascii=False, indent=2).encode("utf-8"),
            mime_type="application/json",
        )
        logger.info("persist_output: uploaded content.json")

        content_md = stitch_sections_md(sections, all_blocks)
        content_md_path = f"projects/{project_id}/stages/add/output/content.md"
        upload_bytes(
            bucket_id=bucket_ctx.bucketId,
            folder_id=bucket_ctx.folderId,
            path=content_md_path,
            content=content_md.encode("utf-8"),
            mime_type="text/markdown",
        )
        logger.info("persist_output: uploaded content.md")

        completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        update_stage(
            folder_id=bucket_ctx.folderId,
            stage_id=stage_id,
            payload={
                "status": "awaiting_approval",
                "outputBucketPath": content_json_path,
                "contentMd": content_md[:_CONTENT_MD_PREVIEW_CHARS],
                "completedAt": completed_at,
            },
        )
        logger.info("persist_output: stage set to awaiting_approval")

        return {
            "content_json_path": content_json_path,
            "content_md_path": content_md_path,
        }

    except Exception as exc:
        logger.error("persist_output: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="persist_output")}
