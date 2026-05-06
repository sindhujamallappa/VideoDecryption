"""Tests for the score_quality node (Fix 5)."""
from __future__ import annotations

from video_add_agent.nodes.score_quality import score_quality_node


def test_grounded_block_when_content_traces_to_transcript(sample_state):
    """A block whose proper nouns appear in the transcript scores 'grounded'."""
    sample_state.sections = {
        "purpose": (
            "The Invoice processing app handles approval [00:05–00:15]. "
            "INV-001 is reviewed at [00:15–00:30]."
        ),
    }
    result = score_quality_node(sample_state)
    rep = result["quality_report"]
    assert "purpose" in rep["grounded_blocks"]
    assert rep["overall_score"] == 1.0
    assert rep["recommendation"] == "acceptable"


def test_placeholder_block_when_only_gap_marker(sample_state):
    sample_state.sections = {
        "purpose": "> **Gap:** To be confirmed with SME",
    }
    result = score_quality_node(sample_state)
    rep = result["quality_report"]
    assert "purpose" in rep["placeholder_blocks"]
    assert rep["overall_score"] == 0.0
    assert rep["recommendation"] == "insufficient_grounding"


def test_suspected_hallucination_when_only_in_project_name(sample_state):
    """Block mentions 'UiBank' which appears in projectName but NOT in the
    transcript or vision corpus → flagged as suspected hallucination."""
    sample_state.project_name = "UiBank_videoTesting_05062026"
    # Transcript discusses something else entirely.
    sample_state.raw_transcript = sample_state.raw_transcript[:0]
    sample_state.sections = {
        "purpose": "Agent will log into UiBank using a service account.",
        "applications_used": "UiBank web application is the primary system.",
    }
    # Strip vision evidence too so 'UiBank' is unsupported anywhere.
    for s in sample_state.screenshots:
        s.vision_analysis = None
    result = score_quality_node(sample_state)
    rep = result["quality_report"]
    assert "purpose" in rep["suspected_hallucination_blocks"]
    assert "applications_used" in rep["suspected_hallucination_blocks"]
    assert "uibank" in rep["hallucination_evidence"]["purpose"].lower()
    assert rep["recommendation"] in {"insufficient_grounding", "needs_review"}


def test_recommendation_thresholds(sample_state):
    """≥0.4 = acceptable, 0.15–0.4 = needs_review, <0.15 = insufficient."""
    # 1 grounded out of 4 = 0.25 → needs_review
    sample_state.sections = {
        "purpose": "Invoice processing [00:00–00:10]",  # grounded
        "objectives": "> **Gap:** To be confirmed with SME",
        "purpose_2": "> **Gap:** To be confirmed with SME",
        "purpose_3": "> **Gap:** To be confirmed with SME",
    }
    rep = score_quality_node(sample_state)["quality_report"]
    assert rep["recommendation"] == "needs_review"


def test_screenshot_coverage_summary(sample_state):
    """The screenshot_coverage block reflects vision_analysis classifications."""
    sample_state.sections = {"purpose": "Invoice processing [00:00–00:10]"}
    rep = score_quality_node(sample_state)["quality_report"]
    cov = rep["screenshot_coverage"]
    assert cov["total"] == 3
    assert cov["actually_analyzed_with_vision"] == 3
    assert cov["screen_recordings_detected"] == 3


def test_empty_sections_emits_insufficient_grounding(sample_state):
    sample_state.sections = {}
    rep = score_quality_node(sample_state)["quality_report"]
    assert rep["overall_score"] == 0.0
    assert rep["recommendation"] == "insufficient_grounding"
    assert rep["grounded_blocks"] == []


def test_insufficient_grounding_flag_propagates(sample_state):
    """If the upstream insufficient_grounding flag is True, recommendation
    is forced to insufficient_grounding regardless of section content."""
    sample_state.insufficient_grounding = True
    sample_state.sections = {
        "purpose": "Invoice [00:00–00:05] processing [00:05–00:10]",
    }
    rep = score_quality_node(sample_state)["quality_report"]
    assert rep["recommendation"] == "insufficient_grounding"
