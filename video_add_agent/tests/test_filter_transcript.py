"""Tests for filter_transcript node (LLM mocked)."""
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
    sample_state.raw_transcript = _make_segments(3)

    async def _scores(llm, system, user):
        return json.dumps([
            {"index": 0, "score": 0.9, "reason": "process"},
            {"index": 1, "score": 0.1, "reason": "small talk"},
            {"index": 2, "score": 0.8, "reason": "process"},
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


def test_audio_drop_ratio_computed(sample_state, monkeypatch):
    sample_state.raw_transcript = _make_segments(4)

    async def _all_low(llm, system, user):
        return json.dumps([
            {"index": i, "score": 0.0, "reason": "off-topic"} for i in range(4)
        ])

    monkeypatch.setattr("video_add_agent.nodes.filter_transcript.call_llm", _all_low)
    monkeypatch.setattr(
        "video_add_agent.nodes.filter_transcript.build_llm", lambda **_k: None
    )

    result = filter_transcript_node(sample_state)
    assert result["filtered_transcript"] == []
    assert result["audio_drop_ratio"] == 1.0


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


def test_batch_parse_failure_falls_back_to_per_window(sample_state, monkeypatch):
    sample_state.raw_transcript = _make_segments(2)
    call_count = {"n": 0}

    async def _bad_then_good(llm, system, user):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "this is not json at all"
        # Per-window fallback expects {"score": ..., "reason": ...}
        return json.dumps({"score": 0.8, "reason": "ok"})

    monkeypatch.setattr("video_add_agent.nodes.filter_transcript.call_llm", _bad_then_good)
    monkeypatch.setattr(
        "video_add_agent.nodes.filter_transcript.build_llm", lambda **_k: None
    )

    result = filter_transcript_node(sample_state)
    assert "error" not in result
    # Both windows scored 0.8 in the per-window fallback → both kept
    assert len(result["filtered_transcript"]) == 2
