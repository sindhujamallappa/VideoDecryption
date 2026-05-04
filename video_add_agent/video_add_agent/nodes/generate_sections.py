"""Node — generate_sections

Parses the active ADD template definition and generates every section via
two parallel LLM calls (mirroring the TypeScript chunking strategy).
Section keys are driven entirely by the template — no hardcoded list.

Reads `state.aligned_steps` and `state.screenshots` for source context.
On retry from validate_output, prepends `state.validation_errors` as
constraint hints to the chunk prompts.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from video_add_agent.models.input import ActiveAddTemplate
from video_add_agent.models.template import (
    BlockKind,
    SectionHeading,
    TemplateBlock,
    TemplateDefinition,
    TemplateSection,
)
from video_add_agent.state import ErrorInfo, VideoAgentState
from video_add_agent.utils.llm import build_llm, call_llm, strip_json_fence
from video_add_agent.utils.prompt import FALLBACK_SECTION_KEYS, build_chunk_system_prompt

logger = logging.getLogger(__name__)

_GENERATION_MODEL = "anthropic.claude-opus-4-7"


def generate_sections_node(state: VideoAgentState) -> dict:
    logger.info("generate_sections: start project_id=%s", state.project_id)
    try:
        result = asyncio.run(_generate(state))
        # Clear validation_errors so validate_output starts fresh.
        return {**result, "validation_errors": []}
    except Exception as exc:
        logger.error("generate_sections: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="generate_sections")}


async def _generate(state: VideoAgentState) -> dict:
    template = ActiveAddTemplate.model_validate(state.active_add_template)

    try:
        tmpl_def = TemplateDefinition.model_validate_json(template.definition)
        # Generate only for dynamic blocks with an outputKey. Static blocks
        # are emitted verbatim from referenceContent at stitch time, so they
        # never reach the LLM here.
        dynamic_blocks = tmpl_def.dynamic_blocks()
        if not dynamic_blocks:
            raise ValueError("template has no dynamic blocks")
    except Exception as exc:
        logger.warning(
            "generate_sections: template parse failed or no dynamic blocks (%s) "
            "— using canonical fallback keys", exc,
        )
        dynamic_blocks = _make_fallback_blocks()

    mid = max(1, len(dynamic_blocks) // 2)
    chunks = [dynamic_blocks[:mid], dynamic_blocks[mid:]]

    llm = build_llm(model_name=_GENERATION_MODEL)

    if state.validation_errors:
        logger.info(
            "generate_sections: retry with %d constraint hint(s)",
            len(state.validation_errors),
        )

    ctx: dict[str, Any] = {
        "project_id": state.project_id,
        "project_name": state.project_name,
        "aligned_steps": state.aligned_steps,
        "screenshots": state.screenshots,
        "validation_errors": list(state.validation_errors),
    }

    logger.info(
        "generate_sections: %d dynamic blocks split into %d chunks — running in parallel",
        len(dynamic_blocks), len(chunks),
    )
    chunk_results = await asyncio.gather(
        *[_generate_chunk(llm, chunk, i + 1, len(chunks), ctx) for i, chunk in enumerate(chunks)]
    )

    merged: dict[str, str] = {}
    for chunk_result in reversed(chunk_results):
        merged.update(chunk_result)

    for _, block in dynamic_blocks:
        if block.outputKey and not merged.get(block.outputKey):
            merged[block.outputKey] = "> **Gap:** To be confirmed with SME"
            logger.debug("generate_sections: filled missing key %s", block.outputKey)

    logger.info("generate_sections: produced %d sections", len(merged))
    return {"sections": merged}


async def _generate_chunk(
    llm: Any,
    blocks: list[tuple[TemplateSection, TemplateBlock]],
    chunk_num: int,
    total_chunks: int,
    ctx: dict[str, Any],
) -> dict[str, str]:
    logger.info(
        "generate_sections: chunk %d/%d — %d blocks", chunk_num, total_chunks, len(blocks)
    )
    system = build_chunk_system_prompt(blocks=blocks, **ctx)
    user = (
        f"Generate ADD content for chunk {chunk_num} of {total_chunks}. "
        "Return only the JSON object with the specified output keys."
    )
    raw = await call_llm(llm, system, user)
    return json.loads(strip_json_fence(raw))


def _make_fallback_blocks() -> list[tuple[TemplateSection, TemplateBlock]]:
    """Return a minimal block list from the 26 canonical ADD section keys."""
    result: list[tuple[TemplateSection, TemplateBlock]] = []
    for key in FALLBACK_SECTION_KEYS:
        heading_text = key.replace("_", " ").title()
        section = TemplateSection(
            heading=SectionHeading(text=heading_text, level=1),
            blocks=[],
        )
        block = TemplateBlock(outputKey=key, kind=BlockKind.text)
        result.append((section, block))
    return result
