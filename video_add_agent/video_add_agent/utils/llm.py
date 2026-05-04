"""LLM call wrappers using the UiPath LLM Gateway directly.

Why not UiPathChat from uipath_langchain? It requires a JWT access token
(plus UIPATH_TENANT_ID / UIPATH_ORGANIZATION_ID env vars). On staging tenants
where authentication uses Personal Access Tokens (`rt_...`), UiPathChat refuses
to start. The gateway's OpenAI-compatible endpoint accepts PAT auth fine, so
we use langchain_openai.ChatOpenAI pointed at the gateway URL instead.

Per-tenant gateway path (from the Agentify .env):
    /agenthub_/llm/api/chat/completions?api-version=2024-08-01-preview

Required headers:
    Authorization: Bearer <PAT>
    X-UIPATH-OrganizationUnitId: <folder>
    X-UiPath-LlmGateway-NormalizedApi-ModelName: <model>
"""
from __future__ import annotations

import logging
import os
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_RETRY = dict(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)

# Default folder ID for X-UIPATH-OrganizationUnitId. Can be overridden by
# UIPATH_FOLDER_ID env var. Defaults to the Agentify staging folder.
_DEFAULT_FOLDER_ID = os.environ.get("UIPATH_FOLDER_ID", "10934")
_API_VERSION = "2024-08-01-preview"


def build_llm(model_name: str = "anthropic.claude-opus-4-7", **_: Any) -> ChatOpenAI:
    """Construct a langchain ChatOpenAI client pointed at the UiPath LLM Gateway.

    Note: the `temperature` keyword arg is silently ignored — Claude Opus 4.7
    rejects requests that include `temperature` ("temperature is deprecated for
    this model"), so we don't forward it.
    """
    base_url = os.environ.get("UIPATH_URL", "").rstrip("/")
    token = os.environ.get("UIPATH_ACCESS_TOKEN", "")
    if not base_url or not token:
        raise RuntimeError("UIPATH_URL and UIPATH_ACCESS_TOKEN must be set in env")

    return ChatOpenAI(
        model=model_name,
        base_url=f"{base_url}/agenthub_/llm/api",
        api_key=token,
        default_headers={
            "X-UIPATH-OrganizationUnitId": _DEFAULT_FOLDER_ID,
            "X-UiPath-LlmGateway-NormalizedApi-ModelName": model_name,
        },
        default_query={"api-version": _API_VERSION},
        temperature=None,
        max_tokens=4096,
    )


@retry(**_RETRY)
async def call_llm(llm: ChatOpenAI, system: str, user: str) -> str:
    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    result = await llm.ainvoke(messages)
    content = result.content
    assert isinstance(content, str), f"Unexpected LLM content type: {type(content)}"
    return content


@retry(**_RETRY)
async def call_llm_multimodal(
    llm: ChatOpenAI,
    system: str,
    text: str,
    images: list[dict[str, Any]],
) -> str:
    """Send text + base64 image blocks to a vision-capable model via the gateway."""
    content: list[dict[str, Any]] = [{"type": "text", "text": text}, *images]
    messages = [SystemMessage(content=system), HumanMessage(content=content)]
    result = await llm.ainvoke(messages)
    raw = result.content
    assert isinstance(raw, str), f"Unexpected LLM content type: {type(raw)}"
    return raw


def strip_json_fence(raw: str) -> str:
    """Remove optional ```json ... ``` fences from LLM output."""
    raw = raw.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        body = parts[1] if len(parts) >= 2 else raw
        if body.startswith("json"):
            body = body[4:]
        return body.strip()
    return raw
