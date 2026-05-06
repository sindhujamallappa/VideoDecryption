"""Tests for filter_transcript node (LLM mocked).

After Fix 1 the LLM emits BOTH walkthrough_score and discussion_score
per window; effective score is max(...). Mode C kicks in when retention
falls below FALLBACK_RETENTION_FLOOR.
"""
from __future__ import annotations

import json

from video_add_agent.nodes.filter_transcript import filter_transcript_node
from video_add_agent.state import TranscriptSegment


def _make_segments(n: int, dur: float = 25.0) -> list[TranscriptSegment]:
    return [
        TranscriptSegment(start=i * dur, end=(i + 1) * dur, text=f"window {i} text")
        for i in range(n)
    ]


def test_drops_low_score_windows(sample_state, monkeypatch):
    """Walkthrough mode: high walkthrough_score on 2 windows, 0 on 1 → keep 2."""
    sample_state.raw_transcript = _make_segments(3)

    async def _scores(llm, system, user):
        return json.dumps([
            {"index": 0, "walkthrough_score": 0.9, "discussion_score": 0.1, "reason": "process"},
            {"index": 1, "walkthrough_score": 0.05, "discussion_score": 0.05, "reason": "small talk"},
            {"index": 2, "walkthrough_score": 0.8, "discussion_score": 0.1, "reason": "process"},
        ])

    monkeypatch.setattr("video_add_agent.nodes.filter_transcript.call_llm", _scores)
    monkeypatch.setattr(
        "video_add_agent.nodes.filter_transcript.build_llm", lambda **_k: None
    )

    result = filter_transcript_node(sample_state)
    assert "error" not in result
    kept = result["filtered_transcript"]
    assert len(kept) == 2
    assert all("window" in s.text for s in kept)
    assert result["transcript_mode"] == "walkthrough"
    assert result["transcript_retention_pct"] > 0.5


def test_discussion_mode_keeps_process_talk(sample_state, monkeypatch):
    """Mode B: discussion_score dominates → mode='discussion'."""
    sample_state.raw_transcript = _make_segments(4)

    async def _scores(llm, system, user):
        return json.dumps([
            {"index": 0, "walkthrough_score": 0.1, "discussion_score": 0.85, "reason": "SIPOC"},
            {"index": 1, "walkthrough_score": 0.1, "discussion_score": 0.7, "reason": "process talk"},
            {"index": 2, "walkthrough_score": 0.1, "discussion_score": 0.6, "reason": "as-is"},
            {"index": 3, "walkthrough_score": 0.05, "discussion_score": 0.05, "reason": "off-topic"},
        ])

    monkeypatch.setattr("video_add_agent.nodes.filter_transcript.call_llm", _scores)
    monkeypatch.setattr(
        "video_add_agent.nodes.filter_transcript.build_llm", lambda **_k: None
    )

    result = filter_transcript_node(sample_state)
    assert result["transcript_mode"] == "discussion"
    assert len(result["filtered_transcript"]) == 3


def test_combined_mode_when_both_dimensions_present(sample_state, monkeypatch):
    """Mixed-mode video: both walkthrough and discussion content kept,
    averages within MODE_DOMINANCE_MARGIN → mode='combined'."""
    sample_state.raw_transcript = _make_segments(2)

    async def _scores(llm, system, user):
        return json.dumps([
            {"index": 0, "walkthrough_score": 0.7, "discussion_score": 0.65, "reason": "demo+narration"},
            {"index": 1, "walkthrough_score": 0.65, "discussion_score": 0.7, "reason": "narration+demo"},
        ])

    monkeypatch.setattr("video_add_agent.nodes.filter_transcript.call_llm", _scores)
    monkeypatch.setattr(
        "video_add_agent.nodes.filter_transcript.build_llm", lambda **_k: None
    )

    result = filter_transcript_node(sample_state)
    assert result["transcript_mode"] == "combined"
    assert len(result["filtered_transcript"]) == 2


def test_mode_c_fallback_when_all_low_returns_full_transcript(sample_state, monkeypatch):
    """All windows score below KEEP_FLOOR → retention < FALLBACK floor →
    Mode C: pass FULL transcript through with mode='unfiltered'."""
    sample_state.raw_transcript = _make_segments(4)

    async def _all_low(llm, system, user):
        return json.dumps([
            {"index": i, "walkthrough_score": 0.05, "discussion_score": 0.05, "reason": "off-topic"}
            for i in range(4)
        ])

    monkeypatch.setattr("video_add_agent.nodes.filter_transcript.call_llm", _all_low)
    monkeypatch.setattr(
        "video_add_agent.nodes.filter_transcript.build_llm", lambda **_k: None
    )

    result = filter_transcript_node(sample_state)
    # Critical: full transcript returned, NOT empty.
    assert len(result["filtered_transcript"]) == 4
    assert result["transcript_mode"] == "unfiltered"
    # Retention is reported as 1.0 because we passed everything through.
    assert result["transcript_retention_pct"] == 1.0
    # audio_drop_ratio reflects the unfiltered passthrough.
    assert result["audio_drop_ratio"] == 0.0


def test_empty_raw_transcript_returns_empty(sample_state, monkeypatch):
    sample_state.raw_transcript = []
    # No LLM call expected; supply a sentinel that would crash if called
    monkeypatch.setattr(
        "video_add_agent.nodes.filter_transcript.build_llm",
        lambda **_k: (_ for _ in ()).throw(RuntimeError("LLM should not be called")),
    )
    result = filter_transcript_node(sample_state)
    assert result["filtered_transcript"] == []
    assert result["audio_drop_ratio"] == 0.0
    assert result["transcript_mode"] == "unfiltered"
    assert result["transcript_retention_pct"] == 0.0


def test_batch_parse_failure_falls_back_to_per_window(sample_state, monkeypatch):
    """Garbage batch JSON triggers per-window fallback. Each window in
    the fallback returns walkthrough/discussion scores ≥ KEEP_FLOOR so
    both windows survive."""
    sample_state.raw_transcript = _make_segments(2)
    call_count = {"n": 0}

    async def _bad_then_good(llm, system, user):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "this is not json at all"
        return json.dumps({"walkthrough_score": 0.8, "discussion_score": 0.1, "reason": "ok"})

    monkeypatch.setattr("video_add_agent.nodes.filter_transcript.call_llm", _bad_then_good)
    monkeypatch.setattr(
        "video_add_agent.nodes.filter_transcript.build_llm", lambda **_k: None
    )

    result = filter_transcript_node(sample_state)
    assert "error" not in result
    assert len(result["filtered_transcript"]) == 2
