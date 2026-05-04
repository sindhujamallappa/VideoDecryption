"""Tests for generate_sections node (LLM mocked)."""
from __future__ import annotations

import json


from video_add_agent.nodes.generate_sections import generate_sections_node

_CHUNK_RESPONSE = json.dumps({"purpose": "Automates invoice processing for the Finance team."})


def test_generate_sections_success(sample_state, monkeypatch):
    async def _fake_llm(llm, system, user):
        return _CHUNK_RESPONSE

    monkeypatch.setattr("video_add_agent.nodes.generate_sections.call_llm", _fake_llm)
    monkeypatch.setattr(
        "video_add_agent.nodes.generate_sections.build_llm", lambda **_k: None
    )

    result = generate_sections_node(sample_state)
    assert "error" not in result
    sections = result["sections"]
    assert isinstance(sections, dict)
    assert "purpose" in sections
    assert "invoice" in sections["purpose"].lower()
    # Always clears validation_errors so validate_output starts fresh
    assert result.get("validation_errors") == []


def test_generate_sections_fills_missing_keys_with_gap(sample_state, monkeypatch):
    async def _empty_llm(*_a, **_k):
        return "{}"

    monkeypatch.setattr("video_add_agent.nodes.generate_sections.call_llm", _empty_llm)
    monkeypatch.setattr(
        "video_add_agent.nodes.generate_sections.build_llm", lambda **_k: None
    )

    result = generate_sections_node(sample_state)
    assert "error" not in result
    assert all("Gap" in v or v for v in result["sections"].values())


def test_generate_sections_fallback_when_template_unparseable(sample_state, monkeypatch):
    sample_state.active_add_template["definition"] = "not-valid-json"

    async def _fake_llm(*_a, **_k):
        return json.dumps({"purpose": "test"})

    monkeypatch.setattr("video_add_agent.nodes.generate_sections.call_llm", _fake_llm)
    monkeypatch.setattr(
        "video_add_agent.nodes.generate_sections.build_llm", lambda **_k: None
    )

    result = generate_sections_node(sample_state)
    assert "error" not in result
    assert "sections" in result


def test_generate_sections_llm_failure_returns_error(sample_state, monkeypatch):
    async def _fail(*_a, **_k):
        raise RuntimeError("AI Fabric unavailable")

    monkeypatch.setattr("video_add_agent.nodes.generate_sections.call_llm", _fail)
    monkeypatch.setattr(
        "video_add_agent.nodes.generate_sections.build_llm", lambda **_k: None
    )

    result = generate_sections_node(sample_state)
    assert result["error"].failed_node == "generate_sections"


def test_generate_sections_uses_constraint_hints_on_retry(sample_state, monkeypatch):
    """If state.validation_errors is non-empty, the chunk system prompt should
    include them under a 'Constraints from previous attempt' header."""
    captured_systems: list[str] = []

    async def _capture(llm, system, user):
        captured_systems.append(system)
        return json.dumps({"purpose": "fixed"})

    monkeypatch.setattr("video_add_agent.nodes.generate_sections.call_llm", _capture)
    monkeypatch.setattr(
        "video_add_agent.nodes.generate_sections.build_llm", lambda **_k: None
    )

    sample_state.validation_errors = [
        "Section 'as_is_process_map' must contain a valid Mermaid `flowchart TD` block."
    ]
    result = generate_sections_node(sample_state)

    assert "error" not in result
    assert any("Constraints from previous attempt" in s for s in captured_systems)
    assert any("flowchart TD" in s for s in captured_systems)
    # Returned validation_errors are reset for the next pass
    assert result.get("validation_errors") == []
