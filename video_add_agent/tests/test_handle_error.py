"""Tests for handle_error node."""
from __future__ import annotations

from typing import Any

from video_add_agent.nodes.handle_error import handle_error_node
from video_add_agent.state import ErrorInfo


def test_handle_error_sets_stage_rejected(sample_state, monkeypatch):
    updates: list[dict[str, Any]] = []

    def _capture(**kwargs):
        updates.append(kwargs)

    monkeypatch.setattr("video_add_agent.nodes.handle_error.update_stage", _capture)

    sample_state.error = ErrorInfo(
        message="Something went wrong", failed_node="transcribe_raw"
    )
    result = handle_error_node(sample_state)

    assert result == {}
    assert updates, "update_stage was never called"
    payload = updates[0]["payload"]
    assert payload["status"] == "rejected"
    assert "transcribe_raw" in payload["errorDesc"]


def test_handle_error_truncates_long_error(sample_state, monkeypatch):
    monkeypatch.setattr("video_add_agent.nodes.handle_error.update_stage", lambda **_: None)
    sample_state.error = ErrorInfo(message="x" * 3000, failed_node="generate_sections")
    result = handle_error_node(sample_state)
    assert result == {}


def test_handle_error_survives_entity_failure(sample_state, monkeypatch):
    def _raise(**_):
        raise RuntimeError("Data Fabric unreachable")

    monkeypatch.setattr("video_add_agent.nodes.handle_error.update_stage", _raise)
    sample_state.error = ErrorInfo(message="original", failed_node="validate_artifact")
    result = handle_error_node(sample_state)
    assert result == {}
