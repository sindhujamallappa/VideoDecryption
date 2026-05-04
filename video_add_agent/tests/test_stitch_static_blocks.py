"""Tests that markdown stitching emits static-block referenceContent verbatim
under the section heading, while dynamic blocks get section: markers."""
from __future__ import annotations

from video_add_agent.models.template import (
    BlockKind,
    SectionHeading,
    TemplateBlock,
    TemplateSection,
)
from video_add_agent.utils.markdown import stitch_sections_md


def _section(heading: str, blocks: list[TemplateBlock]) -> TemplateSection:
    return TemplateSection(heading=SectionHeading(text=heading, level=1), blocks=blocks)


def test_static_block_emitted_verbatim_with_heading():
    block = TemplateBlock.model_validate({
        "kind": "static",
        "referenceContent": {"kind": "paragraph", "text": "Boilerplate intro."},
    })
    section = _section("Intro", [block])
    md = stitch_sections_md({}, [(section, block)])
    assert "# Intro" in md
    assert "Boilerplate intro." in md
    # Static blocks have no marker
    assert "<!-- section:" not in md


def test_dynamic_block_uses_marker_and_pulls_content():
    block = TemplateBlock.model_validate({
        "outputKey": "block_6",
        "kind": "dynamic_prose",
    })
    section = _section("Purpose", [block])
    md = stitch_sections_md({"block_6": "This process automates X."}, [(section, block)])
    assert "<!-- section:block_6 -->" in md
    assert "# Purpose" in md
    assert "This process automates X." in md


def test_mixed_section_static_then_dynamic():
    """A single section can have static + dynamic blocks. Heading appears once."""
    static_block = TemplateBlock.model_validate({
        "kind": "static",
        "referenceContent": {"kind": "paragraph", "text": "Intro paragraph"},
    })
    dynamic_block = TemplateBlock.model_validate({
        "outputKey": "block_8",
        "kind": "dynamic_table",
    })
    section = _section("Roles", [static_block, dynamic_block])
    md = stitch_sections_md(
        {"block_8": "| A | B |\n| --- | --- |\n| 1 | 2 |"},
        [(section, static_block), (section, dynamic_block)],
    )
    # Heading appears once, body wraps both
    assert md.count("# Roles") == 1
    assert "Intro paragraph" in md
    assert "| 1 | 2 |" in md
    assert "<!-- section:block_8 -->" in md


def test_static_block_with_empty_reference_content_is_skipped():
    block = TemplateBlock.model_validate({"kind": "static"})
    section = _section("Empty", [block])
    md = stitch_sections_md({}, [(section, block)])
    assert md == ""
