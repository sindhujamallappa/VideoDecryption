"""Tests for transcribe_raw node."""
from __future__ import annotations

from pathlib import Path

from video_add_agent.nodes.transcribe_raw import transcribe_raw_node
from video_add_agent.utils.media import WhisperSegment


def test_transcribe_raw_populates_raw_transcript(sample_state, monkeypatch, tmp_path):
    monkeypatch.setattr(
        "video_add_agent.nodes.transcribe_raw.download_bytes", lambda **_: b"fake-video"
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.transcribe_raw.upload_bytes", lambda **_: None
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.transcribe_raw.extract_audio",
        lambda video_path, audio_path: Path(audio_path),
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.transcribe_raw.run_whisper",
        lambda *_a, **_k: [
            WhisperSegment(start=0.0, end=5.0, text="hello"),
            WhisperSegment(start=5.0, end=10.0, text="world"),
        ],
    )

    result = transcribe_raw_node(sample_state)
    assert "error" not in result
    raw = result["raw_transcript"]
    assert len(raw) == 2
    assert raw[0].text == "hello"
    assert result["transcript_path"].endswith("/transcript.txt")


def test_transcribe_raw_download_failure_returns_error(sample_state, monkeypatch):
    def _fail(**_):
        raise RuntimeError("bucket down")

    monkeypatch.setattr("video_add_agent.nodes.transcribe_raw.download_bytes", _fail)
    result = transcribe_raw_node(sample_state)
    assert result["error"].failed_node == "transcribe_raw"
    assert "bucket down" in result["error"].message
