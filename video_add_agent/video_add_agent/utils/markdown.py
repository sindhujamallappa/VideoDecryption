"""Markdown validation and stitching helpers."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from video_add_agent.models.template import TemplateBlock, TemplateSection

_MERMAID_TD_RE = re.compile(r"```mermaid\s*\nflowchart\s+TD", re.MULTILINE)
_MERMAID_LR_RE = re.compile(r"(```mermaid\s*\nflowchart\s+)LR", re.MULTILINE)


def validate_pipe_table(content: str) -> bool:
    """True if content contains at least a header row, separator, and one data row."""
    pipe_lines = [
        ln.strip() for ln in content.splitlines()
        if ln.strip().startswith("|") and ln.strip().endswith("|")
    ]
    has_sep = any(re.fullmatch(r"\|[-| :]+\|", ln) for ln in pipe_lines)
    return len(pipe_lines) >= 3 and has_sep


def validate_mermaid(content: str) -> bool:
    return bool(_MERMAID_TD_RE.search(content))


def fix_mermaid(content: str) -> str:
    """Normalise flowchart LR → TD and ensure the fence is closed."""
    content = _MERMAID_LR_RE.sub(r"\1TD", content)
    # Ensure closing fence exists
    fence_count = content.count("```")
    if fence_count % 2 != 0:
        content = content.rstrip() + "\n```"
    return content


def enforce_section_cap(content: str, max_chars: int = 2000) -> str:
    if len(content) <= max_chars:
        return content
    truncated = content[:max_chars]
    # Break at last newline to avoid cutting mid-line
    last_nl = truncated.rfind("\n")
    return truncated[:last_nl] if last_nl > 0 else truncated


def stitch_sections_md(
    sections: dict[str, str],
    all_blocks: list[tuple["TemplateSection", "TemplateBlock"]],
) -> str:
    """Assemble the full content.md with <!-- section:{key} --> markers.

    Walks every block in template order. Dynamic blocks (with outputKey)
    pull their body from `sections` and get a `<!-- section:{key} -->`
    marker. Static blocks (no outputKey) emit their referenceContent
    verbatim under the section heading — no marker, since they're not
    template-driven keys.
    """
    from video_add_agent.models.template import BlockKind  # avoid circular import

    parts: list[str] = []
    seen_dynamic_keys: set[str] = set()
    seen_section_headings: set[int] = set()

    for section, block in all_blocks:
        level = section.heading.level
        heading = "#" * max(1, level) + " " + section.heading.text

        if block.kind is BlockKind.static:
            body = block.reference_text.strip()
            if not body:
                continue
            # Emit heading once per section (multiple statics may share one).
            section_id = id(section)
            if section_id not in seen_section_headings:
                seen_section_headings.add(section_id)
                parts.append(f"{heading}\n\n{body}")
            else:
                parts.append(body)
            continue

        # Dynamic block — needs an outputKey to link to generated content.
        key = block.outputKey
        if not key or key in seen_dynamic_keys:
            continue
        seen_dynamic_keys.add(key)
        body = sections.get(key, "")
        if not body:
            continue
        section_id = id(section)
        if section_id in seen_section_headings:
            # Heading already emitted by a prior block in this section —
            # just append the marker + body, don't repeat the heading.
            parts.append(f"<!-- section:{key} -->\n{body}")
        else:
            seen_section_headings.add(section_id)
            parts.append(f"<!-- section:{key} -->\n{heading}\n\n{body}")

    return "\n\n".join(parts)
