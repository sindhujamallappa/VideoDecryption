"""Node — persist_output

Serialises content.json and content.md, uploads both to the ADD output
bucket paths, then updates the AgentifyStage row to `awaiting_approval`.

Screenshots are already in the bucket (uploaded by dedupe_screenshots);
this node only persists the textual ADD payload.

**Fix 3:** before serialisation we ensure content.json has an entry for
every template block. Blocks with no LLM output get a stale-block
placeholder (`"{{SECTION_NAME}} — Not captured from source material. ..."`)
so the senior's TS DOCX merge — which only updates blocks present in
content.json — has nothing to leave un-merged. Eliminates the "Femi Otu"
/ "PeopleSoft" Frankenstein-doc bug.

**Fix 5 integration:** if `state.quality_report` is present, content.md
is prefixed with an "ADD Generation Summary" section that surfaces
overall_score, recommendation, transcript_mode, and screenshot
classification counts. The senior's review UI can pattern-match the
recommendation field for banner colour-coding.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from video_add_agent.models.input import ActiveAddTemplate, BucketContext
from video_add_agent.models.template import (
    BlockKind,
    TemplateBlock,
    TemplateDefinition,
    TemplateSection,
)
from video_add_agent.nodes.generate_sections import _make_fallback_blocks
from video_add_agent.state import ErrorInfo, VideoAgentState
from video_add_agent.utils.bucket import upload_bytes
from video_add_agent.utils.entities import update_stage
from video_add_agent.utils.markdown import stitch_sections_md

logger = logging.getLogger(__name__)

_CONTENT_MD_PREVIEW_CHARS = 9_500


def _stale_placeholder(block: TemplateBlock, section: TemplateSection) -> str:
    """Generic SME placeholder for a block we did not generate content for.

    Prefer the section heading text since it's the user-visible name; fall
    back to the outputKey so the placeholder is unambiguous in debug.
    """
    name = section.heading.text or block.outputKey or "section"
    return (
        f"> **{name}** — Not captured from source material. "
        "To be confirmed with SME."
    )


def _fill_stale_blocks(
    sections: dict[str, str],
    all_blocks: list[tuple[TemplateSection, TemplateBlock]],
) -> tuple[dict[str, str], int]:
    """Ensure every dynamic template block has an entry in `sections`.

    Returns the (possibly augmented) sections dict and a count of how
    many stale blocks were filled. We only touch dynamic blocks
    (kind != static, outputKey present); static blocks are emitted by
    the stitcher from referenceContent and never live in content.json.
    """
    out = dict(sections)
    stale_count = 0
    for section, block in all_blocks:
        if block.kind is BlockKind.static:
            continue
        if not block.outputKey:
            continue
        if not out.get(block.outputKey):
            out[block.outputKey] = _stale_placeholder(block, section)
            stale_count += 1
    return out, stale_count


def _build_summary_md(
    quality_report: dict[str, Any] | None,
    stale_count: int,
    transcript_mode: str,
    transcript_retention_pct: float,
) -> str:
    """Top-of-document summary section. Plain markdown so the TS DOCX
    export renders it as a normal heading + bullet list."""
    lines = ["# ADD Generation Summary", ""]
    if quality_report:
        score_pct = float(quality_report.get("overall_score", 0.0)) * 100
        rec = quality_report.get("recommendation", "needs_review")
        grounded = len(quality_report.get("grounded_blocks", []) or [])
        placeholders = len(quality_report.get("placeholder_blocks", []) or [])
        suspects = len(
            quality_report.get("suspected_hallucination_blocks", []) or []
        )
        shot_cov = quality_report.get("screenshot_coverage", {}) or {}
        total_shots = shot_cov.get("total", 0)
        analyzed_shots = shot_cov.get("actually_analyzed_with_vision", 0)
        screen_recs = shot_cov.get("screen_recordings_detected", 0)

        lines.append(f"- **Overall confidence:** {score_pct:.0f}%")
        lines.append(f"- **Recommendation:** `{rec}`")
        lines.append(
            f"- **Blocks populated from source:** {grounded} grounded, "
            f"{placeholders} placeholder, {suspects} flagged for review"
        )
        lines.append(f"- **Stale template blocks cleared:** {stale_count}")
        lines.append(
            f"- **Transcript mode:** {transcript_mode} "
            f"({transcript_retention_pct * 100:.0f}% retained)"
        )
        lines.append(
            f"- **Screenshots analyzed:** {analyzed_shots} of {total_shots} "
            f"({screen_recs} were screen recordings)"
        )
        if rec == "insufficient_grounding":
            lines.append("")
            lines.append(
                "> **⚠ Low-confidence ADD.** This document was generated with "
                "insufficient source grounding. Treat every section as a "
                "draft requiring SME validation."
            )
        elif suspects:
            evidence = quality_report.get("hallucination_evidence", {}) or {}
            blocks_str = ", ".join(
                quality_report.get("suspected_hallucination_blocks", [])[:5]
            )
            lines.append("")
            lines.append(
                f"> **⚠ Sections flagged for review:** {blocks_str}. "
                "These contain content that may not be supported by the "
                "transcript or screenshots."
            )
    else:
        lines.append(
            f"- **Transcript mode:** {transcript_mode} "
            f"({transcript_retention_pct * 100:.0f}% retained)"
        )
        lines.append(f"- **Stale template blocks cleared:** {stale_count}")
    lines.append("")
    return "\n".join(lines)


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

        # Fix 3: backfill any template block missing from generated sections
        # with a stale-block placeholder so the senior's TS DOCX merge has
        # nothing to leave un-merged.
        sections, stale_count = _fill_stale_blocks(sections, all_blocks)

        content_json_path = f"projects/{project_id}/stages/add/output/content.json"
        upload_bytes(
            bucket_id=bucket_ctx.bucketId,
            folder_id=bucket_ctx.folderId,
            path=content_json_path,
            content=json.dumps(sections, ensure_ascii=False, indent=2).encode("utf-8"),
            mime_type="application/json",
        )
        logger.info(
            "persist_output: uploaded content.json (%d sections, %d stale-filled)",
            len(sections), stale_count,
        )

        body_md = stitch_sections_md(sections, all_blocks)
        summary_md = _build_summary_md(
            quality_report=state.quality_report,
            stale_count=stale_count,
            transcript_mode=state.transcript_mode,
            transcript_retention_pct=state.transcript_retention_pct,
        )
        content_md = summary_md + "\n" + body_md
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
