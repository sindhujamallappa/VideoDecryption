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


def test_stale_blocks_get_sme_placeholder(sample_state, monkeypatch):
    """Fix 3: a template block missing from `sections` is filled with a
    stale-block SME placeholder before content.json is uploaded."""
    sample_state.sections = {}  # nothing generated → all blocks are stale
    uploads: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "video_add_agent.nodes.persist_output.upload_bytes",
        lambda **kw: uploads.append(kw),
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.persist_output.update_stage", lambda **_: None
    )

    persist_output_node(sample_state)

    json_upload = next(u for u in uploads if "content.json" in u["path"])
    data = json.loads(json_upload["content"])
    assert data, "content.json should be non-empty even with no generated sections"
    # Every value is the stale-block placeholder.
    assert all("Not captured from source material" in v for v in data.values())


def test_summary_section_prepended_to_content_md(sample_state, monkeypatch):
    """Fix 3 + Fix 5: when quality_report is present, content.md gets an
    'ADD Generation Summary' section at the top."""
    sample_state.sections = {"purpose": "Invoice approval [00:00–00:10]"}
    sample_state.quality_report = {
        "overall_score": 0.65,
        "recommendation": "acceptable",
        "grounded_blocks": ["purpose"],
        "placeholder_blocks": [],
        "suspected_hallucination_blocks": [],
        "hallucination_evidence": {},
        "screenshot_coverage": {
            "total": 3,
            "actually_analyzed_with_vision": 3,
            "screen_recordings_detected": 3,
        },
    }
    uploads: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "video_add_agent.nodes.persist_output.upload_bytes",
        lambda **kw: uploads.append(kw),
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.persist_output.update_stage", lambda **_: None
    )

    persist_output_node(sample_state)

    md_upload = next(u for u in uploads if "content.md" in u["path"])
    md = md_upload["content"].decode("utf-8")
    assert md.startswith("# ADD Generation Summary")
    assert "Overall confidence:" in md
    assert "Recommendation:" in md
    assert "acceptable" in md


def test_low_confidence_warning_in_summary(sample_state, monkeypatch):
    """When recommendation == 'insufficient_grounding', the summary
    block includes the 'Low-confidence ADD' warning callout."""
    sample_state.sections = {"purpose": "> **Gap:** To be confirmed with SME"}
    sample_state.quality_report = {
        "overall_score": 0.0,
        "recommendation": "insufficient_grounding",
        "grounded_blocks": [],
        "placeholder_blocks": ["purpose"],
        "suspected_hallucination_blocks": [],
        "hallucination_evidence": {},
        "screenshot_coverage": {"total": 0, "actually_analyzed_with_vision": 0},
    }
    uploads: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "video_add_agent.nodes.persist_output.upload_bytes",
        lambda **kw: uploads.append(kw),
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.persist_output.update_stage", lambda **_: None
    )

    persist_output_node(sample_state)
    md_upload = next(u for u in uploads if "content.md" in u["path"])
    md = md_upload["content"].decode("utf-8")
    assert "Low-confidence ADD" in md
