"""Tests for the routing functions in graph.py.

The compiled `graph` itself can't easily be exercised under pytest without
patching every external call, so we exercise the routing logic directly.
This still verifies retry-budget enforcement and error short-circuiting.
"""
from __future__ import annotations


from video_add_agent.graph import (
    MAX_RETRIES,
    _route_after_coverage,
    _route_after_validation,
    _route_fanout,
    _route_or_error,
)
from video_add_agent.state import (
    CoverageGap,
    ErrorInfo,
    VideoAgentState,
)


def _state(**kwargs) -> VideoAgentState:
    base = dict(
        project_id="p", stage_id="s", project_name="n", source_filename="f",
        video_artifact={}, bucket_context={}, active_add_template={},
    )
    base.update(kwargs)
    return VideoAgentState(**base)


def test_route_or_error_forward_when_no_error():
    fn = _route_or_error("next_node")
    assert fn(_state()) == "next_node"


def test_route_or_error_short_circuits_on_error():
    fn = _route_or_error("next_node")
    s = _state(error=ErrorInfo(message="boom", failed_node="x"))
    assert fn(s) == "handle_error"


def test_route_fanout_emits_all_targets_when_clean():
    fn = _route_fanout(["a", "b"])
    assert fn(_state()) == ["a", "b"]


def test_route_fanout_short_circuits_to_handle_error():
    fn = _route_fanout(["a", "b"])
    s = _state(error=ErrorInfo(message="boom", failed_node="x"))
    assert fn(s) == ["handle_error"]


def test_route_after_coverage_continues_when_no_gaps():
    s = _state(coverage_gaps=[])
    assert _route_after_coverage(s) == "generate_sections"


def test_route_after_coverage_retries_when_gaps_and_budget_left():
    s = _state(
        coverage_gaps=[CoverageGap(start=0.0, end=1.0, reason="r")],
        retry_counts={"align_steps": 1},
    )
    assert _route_after_coverage(s) == "align_steps"


def test_route_after_coverage_gives_up_at_max_retries():
    """At retry_counts > MAX_RETRIES, must continue forward even with gaps."""
    s = _state(
        coverage_gaps=[CoverageGap(start=0.0, end=1.0, reason="r")],
        retry_counts={"align_steps": MAX_RETRIES + 1},
    )
    assert _route_after_coverage(s) == "generate_sections"


def test_route_after_validation_continues_when_no_errors():
    s = _state(validation_errors=[])
    assert _route_after_validation(s) == "persist_output"


def test_route_after_validation_retries_when_errors_and_budget_left():
    s = _state(
        validation_errors=["bad mermaid"],
        retry_counts={"generate_sections": 1},
    )
    assert _route_after_validation(s) == "generate_sections"


def test_route_after_validation_gives_up_at_max_retries():
    s = _state(
        validation_errors=["bad mermaid"],
        retry_counts={"generate_sections": MAX_RETRIES + 1},
    )
    assert _route_after_validation(s) == "persist_output"


def test_max_retries_default_value():
    assert MAX_RETRIES == 2
