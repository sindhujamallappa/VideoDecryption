"""Tests for persist_output node."""
from __future__ import annotations

import json
from typing import Any

from video_add_agent.nodes.persist_output import persist_output_node


def test_persist_output_uploads_and_updates_stage(state_with_sections, monkeypatch):
    uploads: list[dict[str, Any]] = []
    stage_updates: list[dict[str, Any]] = []

    def _capture_upload(**kwargs):
        uploads.append(kwargs)

    def _capture_stage(**kwargs):
        stage_updates.append(kwargs)

    monkeypatch.setattr("video_add_agent.nodes.persist_output.upload_bytes", _capture_upload)
    monkeypatch.setattr("video_add_agent.nodes.persist_output.update_stage", _capture_stage)

    result = persist_output_node(state_with_sections)
    assert "error" not in result

    uploaded_paths = {u["path"] for u in uploads}
    assert any("content.json" in p for p in uploaded_paths)
    assert any("content.md" in p for p in uploaded_paths)

    json_upload = next(u for u in uploads if "content.json" in u["path"])
    data = json.loads(json_upload["content"])
    assert "purpose" in data

    assert stage_updates
    payload = stage_updates[0]["payload"]
    assert payload["status"] == "awaiting_approval"
    assert "outputBucketPath" in payload
    assert "contentMd" in payload
    assert "completedAt" in payload


def test_persist_output_returns_correct_paths(state_with_sections, monkeypatch):
    monkeypatch.setattr(
        "video_add_agent.nodes.persist_output.upload_bytes", lambda **_: None
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.persist_output.update_stage", lambda **_: None
    )

    result = persist_output_node(state_with_sections)
    assert result["content_json_path"] == "projects/proj-123/stages/add/output/content.json"
    assert result["content_md_path"] == "projects/proj-123/stages/add/output/content.md"
