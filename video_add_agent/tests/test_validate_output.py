"""Tests for validate_output node."""
from __future__ import annotations


from video_add_agent.nodes.validate_output import validate_output_node


def test_valid_sections_pass_through(state_with_sections):
    result = validate_output_node(state_with_sections)
    assert "error" not in result
    assert "purpose" in result["sections"]
    # Clean section, no constraint hints emitted
    assert result["validation_errors"] == []


def test_missing_key_filled_with_gap_and_emits_hint(state_with_sections):
    state_with_sections.sections = {}
    result = validate_output_node(state_with_sections)
    assert "error" not in result
    assert "Gap" in result["sections"].get("purpose", "")
    # Missing key triggers a constraint hint and increments retry count
    assert any("missing" in e.lower() for e in result["validation_errors"])
    assert result["retry_counts"]["generate_sections"] == 1


def test_invalid_mermaid_replaced_with_placeholder_and_emits_hint(state_with_sections):
    state_with_sections.active_add_template["definition"] = (
        '{"sections": [{"heading": {"text": "Process Map", "level": 1},'
        '"blocks": [{"outputKey": "as_is_process_map", "kind": "process_map"}]}]}'
    )
    state_with_sections.sections = {
        "as_is_process_map": "this is not mermaid at all"
    }
    result = validate_output_node(state_with_sections)
    assert "error" not in result
    content = result["sections"]["as_is_process_map"]
    assert "flowchart TD" in content
    assert any("Mermaid" in e for e in result["validation_errors"])


def test_long_section_trimmed_to_cap(state_with_sections):
    state_with_sections.sections["purpose"] = "x" * 3000
    result = validate_output_node(state_with_sections)
    assert "error" not in result
    assert len(result["sections"]["purpose"]) <= 2000
