"""Markdown validation, stitching, and citation-density helpers."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from video_add_agent.models.template import TemplateBlock, TemplateSection

_MERMAID_TD_RE = re.compile(r"```mermaid\s*\nflowchart\s+TD", re.MULTILINE)
_MERMAID_LR_RE = re.compile(r"(```mermaid\s*\nflowchart\s+)LR", re.MULTILINE)

# Citation forms accepted by count_citation_density:
#   [MM:SS-MM:SS]   transcript timestamp range (preferred)
#   [MM:SS–MM:SS]   en-dash variant the LLM sometimes emits
#   [MM:SS]         single timestamp
#   (MM:SS)         parenthetical timestamp
#   screenshot-NN.jpg / screenshot-N.jpg
#   image://...     bucket-relative screenshot URL (counts as citation)
_CITATION_RE = re.compile(
    r"\[\d{1,2}:\d{2}\s*[-–—]\s*\d{1,2}:\d{2}\]"     # [MM:SS-MM:SS]
    r"|\[\d{1,2}:\d{2}\]"                              # [MM:SS]
    r"|\(\d{1,2}:\d{2}\)"                              # (MM:SS)
    r"|screenshot-\d{1,3}\.jpg"                        # screenshot-NN.jpg
    r"|image://[^\s)\]]+",                             # image://...
)

# A "claim sentence" is a sentence that contains substantive content (not
# just a heading, table separator, fence, or gap marker).
_GAP_RE = re.compile(r"\bTo be confirmed with SME\b", re.IGNORECASE)
_TABLE_SEP_RE = re.compile(r"^\s*\|[-| :]+\|\s*$")
_FENCE_RE = re.compile(r"^\s*```")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_BULLET_RE = re.compile(r"^\s*[-*]\s+")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


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


def count_citation_density(block_md: str) -> float:
    """Return the fraction of substantive lines that contain at least one
    citation. Used by score_quality (Fix 5) to flag low-grounding blocks.

    "Substantive lines" excludes blank lines, headings, code fences,
    table separators, table rows (rows have their own structure; we
    count them once via the table as a whole), and lines that are only
    a Gap marker.

    A block with no substantive lines returns 1.0 (vacuously cited).
    A block where every claim has a citation returns 1.0.
    A block where no claim has a citation returns 0.0.
    """
    if not block_md or not block_md.strip():
        return 1.0

    # Tables are special: count them as ONE substantive unit. If any
    # citation appears inside the table, count it as cited; otherwise
    # uncited. Strip them out so per-line counting doesn't blow up the
    # denominator.
    in_table = False
    table_buf: list[str] = []
    table_units: list[tuple[bool, str]] = []  # (cited, dummy_text)
    non_table_lines: list[str] = []

    for line in block_md.splitlines():
        if _TABLE_ROW_RE.match(line):
            in_table = True
            table_buf.append(line)
            continue
        if in_table:
            joined = "\n".join(table_buf)
            cited = bool(_CITATION_RE.search(joined))
            table_units.append((cited, joined))
            table_buf = []
            in_table = False
        non_table_lines.append(line)

    if in_table and table_buf:
        joined = "\n".join(table_buf)
        cited = bool(_CITATION_RE.search(joined))
        table_units.append((cited, joined))

    substantive_total = 0
    cited_total = 0

    for cited, _ in table_units:
        substantive_total += 1
        if cited:
            cited_total += 1

    in_fence = False
    for line in non_table_lines:
        stripped = line.strip()
        if _FENCE_RE.match(stripped):
            in_fence = not in_fence
            continue
        if in_fence:
            # Code/Mermaid fences carry citations elsewhere (or aren't
            # claims); skip.
            continue
        if not stripped:
            continue
        if _HEADING_RE.match(stripped):
            continue
        if _TABLE_SEP_RE.match(stripped):
            continue
        if _GAP_RE.search(stripped):
            # Gap markers are explicit "we don't know" — don't penalise.
            continue
        substantive_total += 1
        if _CITATION_RE.search(stripped):
            cited_total += 1

    if substantive_total == 0:
        return 1.0
    return cited_total / substantive_total


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
