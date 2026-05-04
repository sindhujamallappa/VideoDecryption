"""Tests for validate_artifact node."""
from __future__ import annotations


from video_add_agent.nodes.validate_artifact import validate_artifact_node


def test_valid_artifact_returns_no_error(sample_state, mock_bucket, mock_entities, monkeypatch):
    monkeypatch.setattr(
        "video_add_agent.nodes.validate_artifact.update_stage", lambda **_: None
    )
    result = validate_artifact_node(sample_state)
    assert "error" not in result


def test_missing_bucket_path_returns_error(sample_state, monkeypatch):
    sample_state.video_artifact["bucketPath"] = ""
    monkeypatch.setattr(
        "video_add_agent.nodes.validate_artifact.check_file_exists", lambda **_: True
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.validate_artifact.update_stage", lambda **_: None
    )
    result = validate_artifact_node(sample_state)
    assert result["error"].failed_node == "validate_artifact"


def test_file_not_found_returns_error(sample_state, monkeypatch):
    monkeypatch.setattr(
        "video_add_agent.nodes.validate_artifact.check_file_exists", lambda **_: False
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.validate_artifact.update_stage", lambda **_: None
    )
    result = validate_artifact_node(sample_state)
    assert "not found" in result["error"].message.lower()
    assert result["error"].failed_node == "validate_artifact"


def test_stage_set_to_running_on_success(sample_state, monkeypatch):
    updates: list[dict] = []

    def _capture(**kwargs):
        updates.append(kwargs)

    monkeypatch.setattr(
        "video_add_agent.nodes.validate_artifact.check_file_exists", lambda **_: True
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.validate_artifact.update_stage", _capture
    )
    validate_artifact_node(sample_state)
    assert any(u.get("payload", {}).get("status") == "running" for u in updates)
