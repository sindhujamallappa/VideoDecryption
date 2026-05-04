from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BlockKind(str, Enum):
    """Block kind values.

    Includes both the legacy canonical names used by older fixtures/tests and
    the senior's Coded App schema (`static`, `dynamic_*`). Generators dispatch
    via `BlockKind.normalized` so callers don't need to care which flavor.
    """

    # Legacy / canonical generator categories
    text = "text"
    table = "table"
    process_map = "process_map"
    sign_off = "sign_off"
    version_history = "version_history"
    # Senior's template schema
    static = "static"
    dynamic_prose = "dynamic_prose"
    dynamic_table = "dynamic_table"
    dynamic_diagram = "dynamic_diagram"

    @property
    def normalized(self) -> "BlockKind":
        """Map senior's kinds onto the legacy generator categories.

        - dynamic_prose  -> text
        - dynamic_table  -> table
        - dynamic_diagram-> process_map
        - static         -> static (no LLM call; emit referenceContent verbatim)
        Legacy kinds map to themselves.
        """
        if self is BlockKind.dynamic_prose:
            return BlockKind.text
        if self is BlockKind.dynamic_table:
            return BlockKind.table
        if self is BlockKind.dynamic_diagram:
            return BlockKind.process_map
        return self


class SectionHeading(BaseModel):
    text: str
    level: int = 1


class TableColumn(BaseModel):
    """Column descriptor.

    Accepts either {header, key} (legacy) or {name, hint} (senior's schema)
    via aliases. After validation `header` is always populated.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    header: str = Field(default="", validation_alias="name")
    key: str = ""

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Accept both shapes; if only `name` is given, mirror it into `header`.
            if "header" not in data and "name" in data:
                data = {**data, "header": data["name"]}
        return data


class TemplateBlock(BaseModel):
    """A single block within a template section.

    Tolerates both the legacy schema (outputKey + flat fields) and the senior's
    schema (id/index/static-vs-dynamic discriminator, typed referenceContent,
    columns with name/hint, diagramType, overrideContent, etc.).
    """

    model_config = ConfigDict(extra="allow")

    outputKey: Optional[str] = None
    kind: BlockKind = BlockKind.text
    rules: Optional[str] = None
    columns: Optional[list[TableColumn]] = None
    fixedFirstColumn: Optional[Union[bool, list[str]]] = None
    # senior's schema sends a typed object: {kind: paragraph|table, ...};
    # legacy fixtures send a plain string. Accept both.
    referenceContent: Optional[Union[str, dict]] = None

    @property
    def reference_text(self) -> str:
        """Flatten referenceContent into a plain string for prompts/output."""
        rc = self.referenceContent
        if rc is None:
            return ""
        if isinstance(rc, str):
            return rc
        if isinstance(rc, dict):
            if rc.get("kind") == "paragraph":
                return str(rc.get("text") or "")
            if rc.get("kind") == "table":
                rows = rc.get("rows") or []
                # Render as a markdown pipe table best-effort.
                if not rows:
                    return ""
                header = "| " + " | ".join(str(c) for c in rows[0]) + " |"
                sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
                body = "\n".join(
                    "| " + " | ".join(str(c) for c in row) + " |"
                    for row in rows[1:]
                )
                return "\n".join([header, sep, body]).strip()
        return ""


class TemplateSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    heading: SectionHeading
    blocks: list[TemplateBlock]


class TemplateDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")

    sections: list[TemplateSection]

    def all_blocks(self) -> list[tuple[TemplateSection, TemplateBlock]]:
        return [(s, b) for s in self.sections for b in s.blocks]

    def dynamic_blocks(self) -> list[tuple[TemplateSection, TemplateBlock]]:
        """Blocks that need an LLM call. Static blocks are excluded — their
        content is emitted verbatim from referenceContent at stitch time."""
        return [(s, b) for s, b in self.all_blocks() if b.kind is not BlockKind.static and b.outputKey]
