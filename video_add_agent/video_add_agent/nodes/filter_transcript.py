"""Node — filter_transcript (multi-mode)

Windows the raw Whisper transcript into ~20 s chunks, scores each window
for BOTH walkthrough-relevance AND discussion-relevance via a single
batched UiPath AI Fabric call, and drops windows below the keep-floor.

Mode-aware scoring (vs. the original walkthrough-only behaviour) was
added after the PLDT SIPOC failure: a 60-min discussion video had every
window scored < 0.5 because the prompt only recognised UI actions, so
filter_transcript dropped 100% of it and the LLM hallucinated the entire
ADD from `projectName`. Now each window emits two scores and we keep on
`max(walkthrough_score, discussion_score)`.

Three modes surface in the `transcript_mode` state field:
- `walkthrough` — kept windows are dominated by UI-action language
- `discussion`  — kept windows are dominated by process-explanation language
- `combined`    — both kinds of content present in roughly equal weight
- `unfiltered`  — Mode C fallback: retention dropped below the floor, so
                  we pass the full transcript through; downstream nodes
                  must treat its grounding signal as weak.

Tunable thresholds live in `video_add_agent/utils/filter_config.py`.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from video_add_agent.state import TranscriptSegment, VideoAgentState
from video_add_agent.utils import filter_config as cfg
from video_add_agent.utils.llm import build_llm, call_llm, strip_json_fence

logger = logging.getLogger(__name__)

_MODEL = "anthropic.claude-opus-4-7"

TranscriptMode = Literal["walkthrough", "discussion", "combined", "unfiltered"]


_SYSTEM = """\
You are a transcript-relevance scorer for a process-documentation pipeline.
Each input window is ~20 seconds of speech from a video. The pipeline
generates an Agent Design Document (ADD) — a structured doc describing
how a software process works. We need to know which windows contain
material that helps build that doc.

Score EACH window on TWO independent dimensions:

  1. walkthrough_score (0.0–1.0):
     1.0 = describes a concrete UI action (clicking, typing, navigating,
           selecting, scrolling, opening a screen, submitting a form,
           "click the X button", "type Y in field Z", "after I press
           Enter the dashboard loads")
     0.0 = no UI actions described (small talk, music, fillers, silence)

  2. discussion_score (0.0–1.0):
     1.0 = explains how a process works at a conceptual level
           (process steps, inputs/outputs, suppliers, customers, system
           interactions, exception handling, "the way it currently works
           is", "the bot logs into X and downloads Y", "first we receive
           the email then we validate", SIPOC framing, "as-is" vs "to-be",
           role descriptions)
     0.0 = none of the above

A window can score high on BOTH (a demo with narrated explanation), one
(pure walkthrough OR pure SME explanation), or neither (off-topic).

Return ONLY a JSON array of objects matching the input order. Each entry:
  {
    "index": <int>,
    "walkthrough_score": <float 0.0–1.0>,
    "discussion_score": <float 0.0–1.0>,
    "reason": "<one short phrase>"
  }
No extra text, no markdown fences."""


def _make_windows(
    segments: list[TranscriptSegment], window_s: float
) -> list[list[TranscriptSegment]]:
    """Greedy windowing: accumulate segments until duration ≥ window_s."""
    windows: list[list[TranscriptSegment]] = []
    current: list[TranscriptSegment] = []
    current_start: float | None = None
    for seg in segments:
        if not current:
            current_start = seg.start
        current.append(seg)
        if seg.end - (current_start or seg.start) >= window_s:
            windows.append(current)
            current = []
            current_start = None
    if current:
        windows.append(current)
    return windows


def _window_text(window: list[TranscriptSegment]) -> str:
    return " ".join(s.text for s in window).strip()


def _window_duration(window: list[TranscriptSegment]) -> float:
    return window[-1].end - window[0].start if window else 0.0


async def _score_batch(
    llm, windows: list[list[TranscriptSegment]]
) -> list[tuple[float, float]]:
    """Score up to BATCH_SIZE windows in one LLM call.

    Returns a list of (walkthrough_score, discussion_score) tuples in
    input order. On JSON parse failure, falls back to per-window scoring.
    """
    payload = json.dumps(
        [{"index": i, "text": _window_text(w)} for i, w in enumerate(windows)],
        ensure_ascii=False,
    )
    user = f"Score these {len(windows)} windows on BOTH dimensions:\n{payload}"
    raw = await call_llm(llm, _SYSTEM, user)
    try:
        parsed = json.loads(strip_json_fence(raw))
        scores: list[tuple[float, float]] = [(0.0, 0.0)] * len(windows)
        for entry in parsed:
            idx = int(entry["index"])
            if 0 <= idx < len(windows):
                w_s = float(entry.get("walkthrough_score", 0.0))
                d_s = float(entry.get("discussion_score", 0.0))
                scores[idx] = (w_s, d_s)
        return scores
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "filter_transcript: batch JSON parse failed (%s) — falling back to per-window",
            exc,
        )
        return await _score_per_window(llm, windows)


async def _score_per_window(
    llm, windows: list[list[TranscriptSegment]]
) -> list[tuple[float, float]]:
    """Per-window fallback. Slower but resilient to batch parse failures."""

    async def one(idx: int, w: list[TranscriptSegment]) -> tuple[int, tuple[float, float]]:
        try:
            raw = await call_llm(
                llm,
                "Score this transcript window on TWO dimensions: walkthrough_score "
                "(UI actions described) and discussion_score (process discussion). "
                'Return ONLY JSON: {"walkthrough_score": float, "discussion_score": float, "reason": str}.',
                _window_text(w),
            )
            data = json.loads(strip_json_fence(raw))
            return idx, (
                float(data.get("walkthrough_score", 0.0)),
                float(data.get("discussion_score", 0.0)),
            )
        except Exception as exc:
            logger.warning(
                "filter_transcript: per-window scoring failed at %d: %s", idx, exc
            )
            # Be lenient on failure: keep the window with a moderate score.
            return idx, (0.5, 0.5)

    results = await asyncio.gather(*(one(i, w) for i, w in enumerate(windows)))
    out: list[tuple[float, float]] = [(0.0, 0.0)] * len(windows)
    for i, sc in results:
        out[i] = sc
    return out


def _classify_mode(
    kept_scores: list[tuple[float, float]],
) -> TranscriptMode:
    """Look at the averages of the kept windows' two scores; whichever
    dominates by ≥ MODE_DOMINANCE_MARGIN sets the label, else 'combined'.
    """
    if not kept_scores:
        return "combined"
    avg_w = sum(s[0] for s in kept_scores) / len(kept_scores)
    avg_d = sum(s[1] for s in kept_scores) / len(kept_scores)
    if avg_w - avg_d > cfg.MODE_DOMINANCE_MARGIN:
        return "walkthrough"
    if avg_d - avg_w > cfg.MODE_DOMINANCE_MARGIN:
        return "discussion"
    return "combined"


def filter_transcript_node(state: VideoAgentState) -> dict:
    logger.info("filter_transcript: start project_id=%s", state.project_id)
    try:
        return asyncio.run(_filter(state))
    except Exception as exc:
        logger.error("filter_transcript: failed — %s", exc, exc_info=True)
        from video_add_agent.state import ErrorInfo
        return {"error": ErrorInfo(message=str(exc), failed_node="filter_transcript")}


async def _filter(state: VideoAgentState) -> dict:
    raw = state.raw_transcript
    if not raw:
        logger.warning("filter_transcript: raw_transcript empty — nothing to filter")
        return {
            "filtered_transcript": [],
            "audio_drop_ratio": 0.0,
            "transcript_mode": "unfiltered",
            "transcript_retention_pct": 0.0,
        }

    windows = _make_windows(raw, cfg.WINDOW_SECONDS)
    logger.info(
        "filter_transcript: %d segments → %d windows", len(raw), len(windows)
    )

    llm = build_llm(model_name=_MODEL)
    all_scores: list[tuple[float, float]] = []
    for batch_start in range(0, len(windows), cfg.BATCH_SIZE):
        batch = windows[batch_start : batch_start + cfg.BATCH_SIZE]
        all_scores.extend(await _score_batch(llm, batch))

    # Effective per-window score = max of the two dimensions.
    effective = [max(w_s, d_s) for w_s, d_s in all_scores]
    kept_pairs = [
        (window, scores)
        for window, eff, scores in zip(windows, effective, all_scores)
        if eff >= cfg.KEEP_FLOOR
    ]

    raw_seconds = sum(_window_duration(w) for w in windows) or 1e-9
    kept_seconds = sum(_window_duration(w) for w, _ in kept_pairs)
    drop_ratio = 1.0 - (kept_seconds / raw_seconds)
    retention_pct = 1.0 - drop_ratio

    # Mode C — fallback. If we kept too little, pass the FULL transcript
    # through with `transcript_mode="unfiltered"`. Downstream consumers
    # (especially generate_sections's grounding gate) read the mode field
    # and treat unfiltered transcripts as weak grounding.
    if retention_pct < cfg.FALLBACK_RETENTION_FLOOR:
        logger.warning(
            "filter_transcript: retention %.1f%% below fallback floor %.1f%% — "
            "returning unfiltered transcript",
            retention_pct * 100, cfg.FALLBACK_RETENTION_FLOOR * 100,
        )
        return {
            "filtered_transcript": list(raw),
            "audio_drop_ratio": 0.0,
            "transcript_mode": "unfiltered",
            "transcript_retention_pct": 1.0,
        }

    kept_segments = [seg for w, _ in kept_pairs for seg in w]
    kept_scores = [scores for _, scores in kept_pairs]
    mode = _classify_mode(kept_scores)

    logger.info(
        "filter_transcript: kept %d/%d windows (%.1f%% retained, mode=%s)",
        len(kept_pairs), len(windows), retention_pct * 100, mode,
    )
    return {
        "filtered_transcript": kept_segments,
        "audio_drop_ratio": drop_ratio,
        "transcript_mode": mode,
        "transcript_retention_pct": retention_pct,
    }
