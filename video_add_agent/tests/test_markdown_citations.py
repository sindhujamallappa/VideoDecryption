"""Tests for the citation-density helper in utils.markdown (Fix 2)."""
from __future__ import annotations

from video_add_agent.utils.markdown import count_citation_density


def test_block_with_full_citations_density_one():
    body = (
        "User opens Invoice app [00:00–00:10].\n"
        "User selects pending queue [00:10–00:25].\n"
        "User reviews row [00:25–00:40].\n"
    )
    assert count_citation_density(body) == 1.0


def test_block_with_no_citations_density_zero():
    body = (
        "User opens the app.\n"
        "User selects the queue.\n"
        "User reviews a row.\n"
    )
    assert count_citation_density(body) == 0.0


def test_screenshot_ref_counts_as_citation():
    body = "User clicks the submit button as shown in screenshot-03.jpg.\n"
    assert count_citation_density(body) == 1.0


def test_image_url_counts_as_citation():
    body = "![cap](image://proj-123/screenshot-04.jpg)\n"
    assert count_citation_density(body) == 1.0


def test_gap_marker_does_not_count_against_density():
    body = (
        "User reviews invoice [00:05–00:15].\n"
        "> **Gap:** To be confirmed with SME\n"
        "User approves invoice [00:20–00:30].\n"
    )
    # Gap line is ignored in the denominator → 2 cited / 2 substantive = 1.0
    assert count_citation_density(body) == 1.0


def test_table_counts_as_one_unit():
    body = (
        "| Step | Description |\n"
        "|---|---|\n"
        "| 1 | Open app [00:00–00:05] |\n"
        "| 2 | Submit form |\n"
    )
    # Whole table has at least one citation → cited unit count = 1, total = 1
    assert count_citation_density(body) == 1.0


def test_heading_lines_excluded():
    body = (
        "## Purpose\n"
        "User opens Invoice app [00:00–00:10].\n"
    )
    # Heading excluded; one substantive line, one cited → 1.0
    assert count_citation_density(body) == 1.0


def test_partial_density():
    body = (
        "User opens the app [00:00–00:05].\n"
        "User then closes the app.\n"
    )
    assert 0.4 < count_citation_density(body) < 0.6


def test_empty_block_returns_one():
    """Vacuous: nothing to cite, nothing to penalise."""
    assert count_citation_density("") == 1.0
    assert count_citation_density("   \n  \n") == 1.0


def test_code_fence_excluded():
    body = (
        "```mermaid\n"
        "flowchart TD\n"
        "  A --> B\n"
        "```\n"
        "User reviews flow [00:00–00:05].\n"
    )
    # Fence content excluded; one substantive line cited → 1.0
    assert count_citation_density(body) == 1.0
