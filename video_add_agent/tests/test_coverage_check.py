"""Tests for coverage_check node."""
from __future__ import annotations


from video_add_agent.nodes.coverage_check import coverage_check_node
from video_add_agent.state import Screenshot, TranscriptSegment


def test_no_gaps_when_screenshots_close(sample_state):
    sample_state.filtered_transcript = [
        TranscriptSegment(start=0.0, end=2.0, text="x"),
    ]
    sample_state.screenshots = [
        Screenshot(timestamp=1.0, phash="a", bucket_path="p", cluster_size=1),
    ]
    result = coverage_check_node(sample_state)
    assert result["coverage_gaps"] == []
    # No retry counter increment when no gaps
    assert "retry_counts" not in result
    assert "relax_factor" not in result


def test_gap_emitted_when_screenshot_too_far(sample_state):
    sample_state.filtered_transcript = [
        TranscriptSegment(start=0.0, end=2.0, text="x"),
    ]
    sample_state.screenshots = [
        Screenshot(timestamp=100.0, phash="a", bucket_path="p", cluster_size=1),
    ]
    result = coverage_check_node(sample_state)
    assert len(result["coverage_gaps"]) == 1
    assert result["retry_counts"]["align_steps"] == 1
    assert result["relax_factor"] == 1


def test_increments_existing_retry_count(sample_state):
    sample_state.filtered_transcript = [
        TranscriptSegment(start=0.0, end=2.0, text="x"),
    ]
    sample_state.screenshots = []
    sample_state.retry_counts = {"align_steps": 1}
    sample_state.relax_factor = 1
    result = coverage_check_node(sample_state)
    assert result["retry_counts"]["align_steps"] == 2
    assert result["relax_factor"] == 2


def test_no_screenshots_at_all_marks_all_segments_as_gaps(sample_state):
    sample_state.filtered_transcript = [
        TranscriptSegment(start=0.0, end=2.0, text="x"),
        TranscriptSegment(start=10.0, end=12.0, text="y"),
    ]
    sample_state.screenshots = []
    result = coverage_check_node(sample_state)
    assert len(result["coverage_gaps"]) == 2
