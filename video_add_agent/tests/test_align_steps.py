"""Tests for align_steps node."""
from __future__ import annotations


from video_add_agent.nodes.align_steps import align_steps_node
from video_add_agent.state import Screenshot, TranscriptSegment


def _make_state_with(state, segments, screenshots, relax=0):
    state.filtered_transcript = segments
    state.screenshots = screenshots
    state.relax_factor = relax
    return state


def test_pairs_segments_to_nearby_screenshots(sample_state):
    state = _make_state_with(
        sample_state,
        segments=[
            TranscriptSegment(start=0.0, end=4.0, text="step 1"),
            TranscriptSegment(start=10.0, end=14.0, text="step 2"),
        ],
        screenshots=[
            Screenshot(timestamp=2.0, phash="a", bucket_path="p1", cluster_size=1),
            Screenshot(timestamp=12.0, phash="b", bucket_path="p2", cluster_size=1),
            Screenshot(timestamp=100.0, phash="c", bucket_path="p3", cluster_size=1),
        ],
    )
    result = align_steps_node(state)
    aligned = result["aligned_steps"]
    assert len(aligned) == 2
    assert aligned[0].screenshot_indices == [0]
    assert aligned[1].screenshot_indices == [1]


def test_no_screenshot_inside_tolerance_returns_empty_indices(sample_state):
    state = _make_state_with(
        sample_state,
        segments=[TranscriptSegment(start=0.0, end=4.0, text="step")],
        screenshots=[Screenshot(timestamp=100.0, phash="a", bucket_path="p", cluster_size=1)],
    )
    result = align_steps_node(state)
    assert result["aligned_steps"][0].screenshot_indices == []


def test_relax_factor_widens_window(sample_state):
    """At relax=0 (BASE_TOLERANCE_S=2.0) a 4s gap is excluded; at relax=4 it's included."""
    seg = [TranscriptSegment(start=0.0, end=2.0, text="step")]
    shots = [Screenshot(timestamp=4.5, phash="a", bucket_path="p", cluster_size=1)]
    # midpoint=1.0, screenshot at 4.5 → distance 3.5
    # tolerance at relax=0: 2.0 → excluded
    # tolerance at relax=2: 2.0 * (1+1) = 4.0 → included
    state0 = _make_state_with(sample_state.model_copy(deep=True), seg, shots, relax=0)
    state2 = _make_state_with(sample_state.model_copy(deep=True), seg, shots, relax=2)
    assert align_steps_node(state0)["aligned_steps"][0].screenshot_indices == []
    assert align_steps_node(state2)["aligned_steps"][0].screenshot_indices == [0]


def test_resets_coverage_gaps(sample_state):
    sample_state.coverage_gaps = []  # default
    sample_state.filtered_transcript = []
    result = align_steps_node(sample_state)
    assert result["coverage_gaps"] == []
