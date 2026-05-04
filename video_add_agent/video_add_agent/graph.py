"""LangGraph stateful graph for the video → ADD pipeline.

  validate_artifact
       │
  transcribe_raw
       ├─────────────────────┐
       ▼                     ▼
filter_transcript    screenshot_pipeline
       │             (extract_keyframes + dedupe_screenshots)
       │                     │
       └─────────┬───────────┘
                 ▼
            align_steps  ◄──────┐
                 ▼               │ (gaps & retries left)
           coverage_check ───────┘
                 ▼
         generate_sections  ◄───┐
                 ▼               │ (validation_errors & retries left)
          validate_output ───────┘
                 ▼
          persist_output
                 ▼
                END

Any node setting state.error → handle_error → END.

Both parallel branches (filter_transcript and screenshot_pipeline) are exactly
one super-step long, so plain add_edge from each into align_steps fires in the
same super-step → align_steps runs exactly once with both branches' state
merged. Splitting screenshots into two nodes (extract+dedupe) made the right
branch two steps deep, which broke the barrier and caused align_steps to fire
twice — once with empty screenshots, once after dedupe — clashing with
coverage_check on coverage_gaps.
"""
from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from video_add_agent.nodes.align_steps import align_steps_node
from video_add_agent.nodes.coverage_check import coverage_check_node
from video_add_agent.nodes.dedupe_screenshots import dedupe_screenshots_node
from video_add_agent.nodes.extract_keyframes import extract_keyframes_node
from video_add_agent.nodes.filter_transcript import filter_transcript_node
from video_add_agent.nodes.generate_sections import generate_sections_node
from video_add_agent.nodes.handle_error import handle_error_node
from video_add_agent.nodes.persist_output import persist_output_node
from video_add_agent.nodes.transcribe_raw import transcribe_raw_node
from video_add_agent.nodes.validate_artifact import validate_artifact_node
from video_add_agent.nodes.validate_output import validate_output_node
from video_add_agent.state import VideoAgentState

MAX_RETRIES = 2  # re-tries beyond initial attempt; total attempts = MAX_RETRIES + 1


def _screenshot_pipeline_node(state: VideoAgentState) -> dict:
    """Right-branch composite: extract_keyframes then dedupe_screenshots.

    Kept as one LangGraph node so the right branch is one super-step, matching
    the left branch's depth and letting the implicit join at align_steps fire
    exactly once.
    """
    if state.error:
        return {}
    update1 = extract_keyframes_node(state)
    if "error" in update1:
        return update1
    interim = state.model_copy(update=update1)
    update2 = dedupe_screenshots_node(interim)
    if "error" in update2:
        return {**update1, **update2}
    return {**update1, **update2}


def _route_or_error(next_node: str):
    """Forward to next_node, unless state.error short-circuits to handle_error."""
    def _fn(state: VideoAgentState) -> str:
        return "handle_error" if state.error else next_node
    _fn.__name__ = f"route_to_{next_node}"
    return _fn


def _route_fanout(next_nodes: list[str]):
    """Fan out to several nodes concurrently (LangGraph runs them in parallel)."""
    def _fn(state: VideoAgentState) -> list[str]:
        return ["handle_error"] if state.error else list(next_nodes)
    _fn.__name__ = f"fanout_to_{'_and_'.join(next_nodes)}"
    return _fn


def _route_after_coverage(state: VideoAgentState) -> str:
    if state.error:
        return "handle_error"
    if state.coverage_gaps and state.retry_counts.get("align_steps", 0) <= MAX_RETRIES:
        return "align_steps"
    return "generate_sections"


def _route_after_validation(state: VideoAgentState) -> str:
    if state.error:
        return "handle_error"
    if (
        state.validation_errors
        and state.retry_counts.get("generate_sections", 0) <= MAX_RETRIES
    ):
        return "generate_sections"
    return "persist_output"


_b = StateGraph(VideoAgentState)

_b.add_node("validate_artifact", validate_artifact_node)
_b.add_node("transcribe_raw", transcribe_raw_node)
_b.add_node("filter_transcript", filter_transcript_node)
_b.add_node("screenshot_pipeline", _screenshot_pipeline_node)
_b.add_node("align_steps", align_steps_node)
_b.add_node("coverage_check", coverage_check_node)
_b.add_node("generate_sections", generate_sections_node)
_b.add_node("validate_output", validate_output_node)
_b.add_node("persist_output", persist_output_node)
_b.add_node("handle_error", handle_error_node)

# Linear prefix
_b.add_edge(START, "validate_artifact")
_b.add_conditional_edges("validate_artifact", _route_or_error("transcribe_raw"))

# Parallel fan-out from transcribe_raw — both branches are exactly one
# super-step long so the join at align_steps fires once.
_b.add_conditional_edges(
    "transcribe_raw",
    _route_fanout(["filter_transcript", "screenshot_pipeline"]),
    path_map=["filter_transcript", "screenshot_pipeline", "handle_error"],
)

# Implicit join at align_steps — plain edges from BOTH predecessors. Because
# both branches finish in the same super-step, the two edges fire together
# and align_steps runs exactly once. align_steps short-circuits on state.error.
_b.add_edge("filter_transcript", "align_steps")
_b.add_edge("screenshot_pipeline", "align_steps")

# Coverage feedback loop
_b.add_conditional_edges("align_steps", _route_or_error("coverage_check"))
_b.add_conditional_edges(
    "coverage_check",
    _route_after_coverage,
    path_map=["align_steps", "generate_sections", "handle_error"],
)

# Validation feedback loop. validate_output increments retry_counts when it
# emits hints; on retry, generate_sections clears validation_errors so the
# next validate_output starts fresh.
_b.add_conditional_edges("generate_sections", _route_or_error("validate_output"))
_b.add_conditional_edges(
    "validate_output",
    _route_after_validation,
    path_map=["generate_sections", "persist_output", "handle_error"],
)

# Tail
_b.add_conditional_edges("persist_output", _route_or_error(END))
_b.add_edge("handle_error", END)

graph = _b.compile()
