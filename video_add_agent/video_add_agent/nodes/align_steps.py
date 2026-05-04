"""Node — align_steps

Joins filtered_transcript with deduplicated screenshots by timestamp.
For each transcript segment, finds all screenshots whose timestamp is
within `BASE_TOLERANCE_S × (1 + 0.5 × relax_factor)` of the segment midpoint.

`relax_factor` is incremented by coverage_check on retry, widening the
window each time so the loop eventually settles.
"""
from __future__ import annotations

import logging

from video_add_agent.state import AlignedStep, ErrorInfo, VideoAgentState

logger = logging.getLogger(__name__)

BASE_TOLERANCE_S = 2.0


def align_steps_node(state: VideoAgentState) -> dict:
    # Two predecessors via plain add_edge — LangGraph fires this once after
    # both branches settle, but if either branch set state.error, skip work
    # and let the outgoing conditional edge route to handle_error.
    if state.error:
        return {}
    logger.info(
        "align_steps: start project_id=%s relax_factor=%d",
        state.project_id, state.relax_factor,
    )
    try:
        tolerance = BASE_TOLERANCE_S * (1.0 + 0.5 * state.relax_factor)
        screenshots = state.screenshots
        aligned: list[AlignedStep] = []

        for seg in state.filtered_transcript:
            midpoint = (seg.start + seg.end) / 2.0
            indices = [
                i for i, s in enumerate(screenshots)
                if abs(s.timestamp - midpoint) <= tolerance
            ]
            aligned.append(
                AlignedStep(
                    timestamp_start=seg.start,
                    timestamp_end=seg.end,
                    transcript=seg.text,
                    screenshot_indices=indices,
                )
            )

        n_with_shots = sum(1 for a in aligned if a.screenshot_indices)
        logger.info(
            "align_steps: %d segments aligned (%d with screenshots, tolerance=%.1fs)",
            len(aligned), n_with_shots, tolerance,
        )
        # Reset coverage_gaps so coverage_check starts fresh.
        return {"aligned_steps": aligned, "coverage_gaps": []}

    except Exception as exc:
        logger.error("align_steps: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="align_steps")}
