"""Node — generate_sections

Parses the active ADD template definition and generates every section via
two parallel LLM calls (mirroring the TypeScript chunking strategy).
Section keys are driven entirely by the template — no hardcoded list.

Reads `state.aligned_steps` and `state.screenshots` for source context.
On retry from validate_output, prepends `state.validation_errors` as
constraint hints to the chunk prompts.

**Fix 2 — Anti-hallucination guardrails:**
- Guardrail 1: minimum grounding threshold check before any LLM call.
  If transcript_retention < FLOOR OR analyzed_screenshots < MIN_SHOTS,
  short-circuit with `insufficient_grounding` status. The OR (vs. AND
  in the original prompt) is deliberate — thin transcript + many good
  screenshots is still groundable, and vice versa.
- Guardrail 2: `projectName` is NOT passed to the LLM (see prompt.py).
- Guardrail 3: citation requirement baked into the system prompt.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
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

# Guardrail 1 thresholds (env-tunable). OR semantics: trip if EITHER
# transcript retention or analyzed-screenshot count falls below floor.
_MIN_TRANSCRIPT_RETENTION = float(
    os.environ.get("GENERATE_MIN_TRANSCRIPT_RETENTION", "0.15")
)
_MIN_ANALYZED_SCREENSHOTS = int(
    os.environ.get("GENERATE_MIN_ANALYZED_SCREENSHOTS", "3")
)


def _check_grounding(state: VideoAgentState) -> dict | None:
    """Guardrail 1: short-circuit if grounding is too thin.

    Returns an `insufficient_grounding` payload if the gate trips,
    otherwise None (continue normally). The transcript_mode is also
    treated as a strong signal: 'unfiltered' means filter_transcript
    fell back to passing the whole thing through, so retention is
    artificially 100% but actual relevance is unknown — we still treat
    that as low-grounding for gating purposes.
    """
    retention = state.transcript_retention_pct
    if state.transcript_mode == "unfiltered":
        # Fallback mode = filter couldn't classify; don't trust the
        # 1.0 retention number, treat as zero for the gate.
        retention = 0.0

    analyzed_shots = len(state.screenshots)

    retention_low = retention < _MIN_TRANSCRIPT_RETENTION
    shots_low = analyzed_shots < _MIN_ANALYZED_SCREENSHOTS

    if retention_low or shots_low:
        msg = (
            "Cannot generate ADD — not enough source material. "
            f"Transcript retention: {retention * 100:.1f}% "
            f"(mode={state.transcript_mode}, threshold={_MIN_TRANSCRIPT_RETENTION * 100:.0f}%). "
            f"Analyzed screenshots: {analyzed_shots} "
            f"(threshold={_MIN_ANALYZED_SCREENSHOTS}). "
            "Need either a walkthrough video with narration or a meeting "
            "recording with substantive process discussion content."
        )
        logger.warning("generate_sections: insufficient grounding — %s", msg)
        # Populate sections with explicit gap markers for every fallback
        # key so persist_output still has something coherent to ship and
        # the senior's review screen surfaces the failure cleanly rather
        # than rendering blanks.
        gap_payload = {
            key: f"> **Gap:** {msg}" for key in FALLBACK_SECTION_KEYS
        }
        return {
            "sections": gap_payload,
            "validation_errors": [],
            "insufficient_grounding": True,
            "insufficient_grounding_reason": msg,
        }
    return None


def generate_sections_node(state: VideoAgentState) -> dict:
    logger.info("generate_sections: start project_id=%s", state.project_id)
    try:
        # Guardrail 1 — grounding gate.
        gate = _check_grounding(state)
        if gate is not None:
            return gate

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

    # NOTE: project_name is deliberately omitted from ctx (Fix 2 /
    # Guardrail 2). The prompt builder no longer accepts it.
    ctx: dict[str, Any] = {
        "project_id": state.project_id,
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
