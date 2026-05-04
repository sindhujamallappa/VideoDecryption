"""Node — filter_transcript

Windows the raw Whisper transcript into ~20 s chunks, scores each window for
process-relevance via a single batched UiPath AI Fabric call, and drops
windows below the threshold. The filtered transcript is what downstream
nodes (align_steps, generate_sections) actually consume.
"""
from __future__ import annotations

import asyncio
import json
import logging

from video_add_agent.state import TranscriptSegment, VideoAgentState
from video_add_agent.utils.llm import build_llm, call_llm, strip_json_fence

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 20.0
RELEVANCE_THRESHOLD = 0.5
BATCH_SIZE = 30
_MODEL = "anthropic.claude-opus-4-7"


_SYSTEM = """\
You are a transcript-relevance scorer for software-process walkthrough videos.
Given a list of transcript windows, score each one for whether it describes
a step in a software process: clicking, typing, navigating, or explaining a UI.

For EACH window in the input array, return a score 0.0–1.0 (1.0 = definitely
process content, 0.0 = small talk / silence / fillers / off-topic) plus a
one-line reason.

Return ONLY a JSON array of {"index": int, "score": float, "reason": str}
matching the input order. No extra text, no markdown fences."""


def _make_windows(segments: list[TranscriptSegment], window_s: float) -> list[list[TranscriptSegment]]:
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


async def _score_batch(llm, windows: list[list[TranscriptSegment]]) -> list[float]:
    """Send up to BATCH_SIZE windows in one LLM call. Returns scores in input order.

    On JSON parse failure, falls back to scoring each window individually.
    """
    payload = json.dumps(
        [{"index": i, "text": _window_text(w)} for i, w in enumerate(windows)],
        ensure_ascii=False,
    )
    user = f"Score these {len(windows)} windows:\n{payload}"
    raw = await call_llm(llm, _SYSTEM, user)
    try:
        parsed = json.loads(strip_json_fence(raw))
        scores = [0.0] * len(windows)
        for entry in parsed:
            idx = int(entry["index"])
            if 0 <= idx < len(windows):
                scores[idx] = float(entry["score"])
        return scores
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning(
            "filter_transcript: batch JSON parse failed (%s) — falling back to per-window",
            exc,
        )
        return await _score_per_window(llm, windows)


async def _score_per_window(llm, windows: list[list[TranscriptSegment]]) -> list[float]:
    """Per-window fallback. Slower but resilient to batch parse failures."""
    async def one(idx: int, w: list[TranscriptSegment]) -> tuple[int, float]:
        try:
            raw = await call_llm(
                llm,
                "Score this transcript window 0.0–1.0 for process-walkthrough relevance. "
                "Return ONLY JSON: {\"score\": float, \"reason\": str}.",
                _window_text(w),
            )
            data = json.loads(strip_json_fence(raw))
            return idx, float(data.get("score", 0.0))
        except Exception as exc:
            logger.warning("filter_transcript: per-window scoring failed at %d: %s", idx, exc)
            return idx, 1.0  # be lenient on failure — keep the window
    results = await asyncio.gather(*(one(i, w) for i, w in enumerate(windows)))
    scores = [0.0] * len(windows)
    for i, s in results:
        scores[i] = s
    return scores


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
        return {"filtered_transcript": [], "audio_drop_ratio": 0.0}

    windows = _make_windows(raw, WINDOW_SECONDS)
    logger.info("filter_transcript: %d segments → %d windows", len(raw), len(windows))

    llm = build_llm(model_name=_MODEL)
    all_scores: list[float] = []
    for batch_start in range(0, len(windows), BATCH_SIZE):
        batch = windows[batch_start : batch_start + BATCH_SIZE]
        all_scores.extend(await _score_batch(llm, batch))

    kept_windows = [w for w, s in zip(windows, all_scores) if s >= RELEVANCE_THRESHOLD]
    kept_segments = [seg for w in kept_windows for seg in w]

    raw_seconds = sum(_window_duration(w) for w in windows) or 1e-9
    kept_seconds = sum(_window_duration(w) for w in kept_windows)
    drop_ratio = 1.0 - (kept_seconds / raw_seconds)
    logger.info(
        "filter_transcript: kept %d/%d windows (%.1f%% of audio dropped)",
        len(kept_windows), len(windows), drop_ratio * 100,
    )
    return {
        "filtered_transcript": kept_segments,
        "audio_drop_ratio": drop_ratio,
    }
