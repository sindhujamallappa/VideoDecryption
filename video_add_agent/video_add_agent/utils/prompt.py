"""Prompt builders for section generation.

All section content is derived exclusively from the video transcript and
extracted screenshots — never from generic templates or invented data.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from video_add_agent.models.template import TemplateBlock, TemplateSection
    from video_add_agent.state import AlignedStep, Screenshot

# 26 canonical ADD section keys — used as fallback when template definition is absent
FALLBACK_SECTION_KEYS: list[str] = [
    "document_version_history",
    "signoff",
    "purpose",
    "key_roles_contacts",
    "objectives",
    "prerequisites",
    "process_overview_business_case",
    "applications_used",
    "as_is_process_map",
    "keystrokes_as_is",
    "process_overview_to_be",
    "to_be_functional_map",
    "functional_requirements",
    "non_functional_requirements",
    "solution_io_integration",
    "ixp_taxonomy",
    "out_of_scope",
    "known_business_exceptions",
    "known_technical_exceptions",
    "unknown_exceptions",
    "uat_scope",
    "uat_success_matrix",
    "continuous_improvement",
    "reporting_requirements",
    "risk_mitigation",
    "abbreviations",
]

_GLOBAL_RULES = """\
## Non-negotiable Rules
- NEVER invent numbers, percentages, SLAs, timings, counts, thresholds, or retry counts.
- NEVER import facts from generic templates or training knowledge. Every claim must appear in the source material.
- For missing data: `> **Gap:** To be confirmed with SME`
- Tables: valid markdown pipe tables only — header row, `|---|` separator, data rows.
- Process maps: ```mermaid\\nflowchart TD  (no image:// URLs inside Mermaid, no emojis in labels)
- Screenshot references: only in keystrokes/steps sections — ![alt](image://{projectId}/filename)
- Max 2 000 characters per section value.
- Return ONLY the JSON object — no markdown fences around it, no extra keys, no commentary."""


def _format_timestamp(seconds: float) -> str:
    mm = int(seconds // 60)
    ss = int(seconds % 60)
    return f"{mm:02d}:{ss:02d}"


def build_chunk_system_prompt(
    blocks: list[tuple["TemplateSection", "TemplateBlock"]],
    project_name: str,
    aligned_steps: list["AlignedStep"],
    screenshots: list["Screenshot"],
    project_id: str = "",
    validation_errors: list[str] | None = None,
) -> str:
    block_specs = "\n\n".join(_block_spec(s, b) for s, b in blocks)

    steps_text = (
        "\n".join(
            f"[{_format_timestamp(a.timestamp_start)}–{_format_timestamp(a.timestamp_end)}] "
            f"{a.transcript}"
            + (f"  → screenshots: {a.screenshot_indices}" if a.screenshot_indices else "")
            for a in aligned_steps
        )
        or "No process steps aligned."
    )

    shots_text = (
        "\n".join(
            f"[{i}] at {_format_timestamp(s.timestamp)}: image://{project_id}/{s.bucket_path.split('/')[-1]}"
            for i, s in enumerate(screenshots)
        )
        or "No screenshots available."
    )

    output_keys = "\n".join(f'  "{b.outputKey}"' for _, b in blocks)

    constraints_block = ""
    if validation_errors:
        bullets = "\n".join(f"- {e}" for e in validation_errors)
        constraints_block = (
            "\n## Constraints from previous attempt (MUST address)\n"
            f"{bullets}\n"
        )

    return f"""\
You are a senior business analyst generating an Agent Design Document (ADD) for the project "{project_name}".

## Source Material

### Aligned Process Steps (transcript window + screenshot indices)
{steps_text}

### Screenshots Available (referenced by index in the steps above)
{shots_text}
{constraints_block}
## Output Format
Return a single valid JSON object with EXACTLY these keys and no others:
{{
{output_keys}
}}

## Section Specifications
{block_specs}

{_GLOBAL_RULES}"""


def _block_spec(section: "TemplateSection", block: "TemplateBlock") -> str:
    lines = [f"### `{block.outputKey}` — {block.kind.value}"]
    lines.append(f"Heading: **{section.heading.text}**")
    if block.rules:
        lines.append(f"Rules: {block.rules}")
    if block.columns:
        cols = " | ".join(
            c.header if hasattr(c, "header") else str(c) for c in block.columns
        )
        lines.append(f"Table columns: `{cols}`")
        lines.append(
            "Emit a valid markdown pipe table with those exact columns. "
            "Use 'To be confirmed with SME' for any unknown cell values."
        )
    if block.reference_text:
        lines.append(f"Reference: {block.reference_text[:200]}")
    return "\n".join(lines)
