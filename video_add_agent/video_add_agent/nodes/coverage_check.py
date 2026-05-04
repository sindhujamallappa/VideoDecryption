"""Node — coverage_check

Detects "coverage gaps": filtered_transcript segments whose midpoint is
more than COVERAGE_TOLERANCE_S seconds from any aligned screenshot.

When gaps exist, increments `retry_counts["align_steps"]` and `relax_factor`
on the returned dict — the router then decides whether to route back to
align_steps (if retry budget left) or forward to generate_sections.
"""
from __future__ import annotations

import logging

from video_add_agent.state import CoverageGap, ErrorInfo, VideoAgentState

logger = logging.getLogger(__name__)

COVERAGE_TOLERANCE_S = 4.0


def coverage_check_node(state: VideoAgentState) -> dict:
    logger.info("coverage_check: start project_id=%s", state.project_id)
    try:
        screenshots = state.screenshots
        screenshot_times = [s.timestamp for s in screenshots]
        gaps: list[CoverageGap] = []

        for seg in state.filtered_transcript:
            midpoint = (seg.start + seg.end) / 2.0
            if not screenshot_times:
                gaps.append(
                    CoverageGap(
                        start=seg.start, end=seg.end,
                        reason="no screenshots available",
                    )
                )
                continue
            min_dist = min(abs(t - midpoint) for t in screenshot_times)
            if min_dist > COVERAGE_TOLERANCE_S:
                gaps.append(
                    CoverageGap(
                        start=seg.start, end=seg.end,
                        reason=f"closest screenshot {min_dist:.1f}s away "
                               f"(tolerance {COVERAGE_TOLERANCE_S:.1f}s)",
                    )
                )

        logger.info(
            "coverage_check: %d gaps in %d segments",
            len(gaps), len(state.filtered_transcript),
        )
        ret: dict = {"coverage_gaps": gaps}
        if gaps:
            new_counts = dict(state.retry_counts)
            new_counts["align_steps"] = new_counts.get("align_steps", 0) + 1
            ret["retry_counts"] = new_counts
            ret["relax_factor"] = state.relax_factor + 1
        return ret

    except Exception as exc:
        logger.error("coverage_check: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="coverage_check")}
