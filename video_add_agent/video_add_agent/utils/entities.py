"""UiPath Data Fabric entity helpers for stage row updates.

Uses the official `uipath` SDK's EntitiesService rather than raw REST,
because the staging tenant's Data Fabric does not expose
PATCH /EntityService/{Entity}/{id} — only the SDK's update_records works.

The SDK requires records to be Pydantic models (it calls .model_dump() on
each), so we wrap the payload in a tiny BaseModel with extra='allow'.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from pydantic import BaseModel, ConfigDict
from tenacity import retry, stop_after_attempt, wait_exponential
from uipath.platform import UiPath

logger = logging.getLogger(__name__)

# Entity key (Data Fabric entity GUID) for AgentifyStage on this tenant.
# Discovered via UiPath().entities.list_entities().
_STAGE_ENTITY_KEY = "c39b27d4-de3f-f111-8ef3-6045bd024144"

_RETRY = dict(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))


class _StageRecord(BaseModel):
    """Pydantic wrapper for AgentifyStage update payloads.

    extra='allow' lets us pass arbitrary fields (status, contentMd, etc.)
    without declaring them — the SDK calls .model_dump() and forwards
    everything to Data Fabric.
    """

    model_config = ConfigDict(extra="allow")
    Id: str


@lru_cache(maxsize=1)
def _client() -> UiPath:
    return UiPath()


@retry(**_RETRY)
def update_stage(folder_id: int, stage_id: str, payload: dict) -> None:
    """Patch an AgentifyStage row via the UiPath SDK.

    `folder_id` is unused now — the SDK reads auth and tenant from env vars.
    Kept in the signature so existing callers do not break.
    """
    del folder_id
    record = _StageRecord(Id=stage_id, **payload)
    resp = _client().entities.update_records(
        entity_key=_STAGE_ENTITY_KEY,
        records=[record],
    )
    failed = getattr(resp, "failed_records_count", 0) or 0
    if failed:
        raise RuntimeError(
            f"update_stage: {failed} record(s) failed for stage_id={stage_id}: "
            f"{getattr(resp, 'failure_records', None)!r}"
        )
    logger.info("update_stage: %s → status=%s", stage_id, payload.get("status", "?"))
