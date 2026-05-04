"""Tests for BlockKind extension + senior's TemplateDefinition shape.

Covers:
- BlockKind.normalized maps dynamic_* and static onto canonical generators
- TemplateDefinition parses the senior's schema (typed referenceContent,
  columns: {name, hint}, static blocks without outputKey)
- dynamic_blocks() filters out static + missing-outputKey blocks
- TableColumn accepts both legacy {header, key} and senior's {name, hint}
- TemplateBlock.reference_text flattens typed referenceContent
"""
from __future__ import annotations

import json

from video_add_agent.models.template import (
    BlockKind,
    TableColumn,
    TemplateBlock,
    TemplateDefinition,
)


def test_block_kind_normalized_maps_senior_kinds():
    assert BlockKind.dynamic_prose.normalized is BlockKind.text
    assert BlockKind.dynamic_table.normalized is BlockKind.table
    assert BlockKind.dynamic_diagram.normalized is BlockKind.process_map
    assert BlockKind.static.normalized is BlockKind.static


def test_block_kind_legacy_kinds_normalize_to_themselves():
    assert BlockKind.text.normalized is BlockKind.text
    assert BlockKind.table.normalized is BlockKind.table
    assert BlockKind.process_map.normalized is BlockKind.process_map


def test_table_column_accepts_legacy_header_key():
    c = TableColumn.model_validate({"header": "Version", "key": "v"})
    assert c.header == "Version"
    assert c.key == "v"


def test_table_column_accepts_senior_name_hint():
    c = TableColumn.model_validate({"name": "Version", "hint": "MAJOR.MINOR"})
    assert c.header == "Version"


def test_template_block_static_no_outputkey():
    """Senior's static blocks have no outputKey — must still validate."""
    block = TemplateBlock.model_validate({
        "id": "x1",
        "index": 0,
        "kind": "static",
        "overrideContent": {"kind": "paragraph", "text": "hello"},
    })
    assert block.kind is BlockKind.static
    assert block.outputKey is None


def test_template_block_typed_reference_content_paragraph():
    block = TemplateBlock.model_validate({
        "id": "x2",
        "index": 1,
        "kind": "dynamic_prose",
        "outputKey": "block_6",
        "referenceContent": {"kind": "paragraph", "text": "intro paragraph"},
    })
    assert block.reference_text == "intro paragraph"


def test_template_block_typed_reference_content_table():
    block = TemplateBlock.model_validate({
        "id": "x3",
        "index": 2,
        "kind": "dynamic_table",
        "outputKey": "block_3",
        "columns": [{"name": "Version"}, {"name": "Date"}],
        "referenceContent": {"kind": "table", "rows": [["Version", "Date"], ["1.0", "2026"]]},
    })
    rt = block.reference_text
    assert "| Version | Date |" in rt
    assert "| --- | --- |" in rt
    assert "| 1.0 | 2026 |" in rt


def test_template_definition_parses_senior_schema():
    """A minimal senior-shape definition with mixed block kinds."""
    raw = json.dumps({
        "schemaVersion": 1,
        "name": "Test ADD",
        "kind": "add",
        "promptVersion": "test-v1",
        "sections": [
            {
                "heading": {"text": "Version History", "level": 1},
                "blocks": [
                    {
                        "id": "b1", "index": 0, "kind": "dynamic_table",
                        "outputKey": "block_3",
                        "columns": [{"name": "Version"}, {"name": "Date"}],
                    },
                    {
                        "id": "b2", "index": 1, "kind": "static",
                        "overrideContent": {"kind": "paragraph", "text": "header text"},
                    },
                ],
            },
            {
                "heading": {"text": "Purpose", "level": 1},
                "blocks": [
                    {
                        "id": "b3", "index": 0, "kind": "dynamic_prose",
                        "outputKey": "block_6",
                    },
                ],
            },
        ],
    })
    td = TemplateDefinition.model_validate_json(raw)
    assert len(td.sections) == 2
    assert len(td.all_blocks()) == 3
    # dynamic_blocks filters out static
    dyn = td.dynamic_blocks()
    assert len(dyn) == 2
    assert {b.outputKey for _, b in dyn} == {"block_3", "block_6"}


def test_template_definition_parses_real_fixture():
    """Smoke test against the real tenant template, if a fixture is present.

    `tests/fixtures/sample_input.json` is generated per-machine by
    `scripts/setup_run.py` (it contains bucket-specific GUIDs) and is not
    committed. Skip if absent so CI / a fresh checkout still passes.
    """
    import pytest
    from pathlib import Path
    fixture = Path(__file__).parent / "fixtures" / "sample_input.json"
    if not fixture.exists():
        pytest.skip(
            "tests/fixtures/sample_input.json not present — generate via "
            "scripts/setup_run.py to enable this smoke test"
        )
    raw = json.loads(fixture.read_text(encoding="utf-8"))
    defn = raw["activeAddTemplate"]["definition"]
    td = TemplateDefinition.model_validate_json(defn)
    assert len(td.sections) > 0
    kinds = {b.kind.value for _, b in td.all_blocks()}
    assert "dynamic_table" in kinds or "dynamic_prose" in kinds or "dynamic_diagram" in kinds
    for _, b in td.dynamic_blocks():
        assert b.outputKey, f"dynamic block missing outputKey: {b}"
