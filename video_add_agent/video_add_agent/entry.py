"""Single entry point callable from UiPath Orchestrator.

Usage (Orchestrator job argument):
    run_video_agent(input_dict)  →  AgentOutput | AgentFailure as dict

The `graph` export is also used by langgraph.json for the UiPath runtime.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from video_add_agent.graph import graph
from video_add_agent.models.input import AgentInput
from video_add_agent.models.output import AgentFailure, AgentOutput

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_video_agent(input: dict[str, Any]) -> dict[str, Any]:  # noqa: A002
    """Validate input, execute the LangGraph pipeline, return a structured dict.

    On success returns AgentOutput fields (success=True).
    On any failure returns AgentFailure fields (success=False).
    """
    project_id: str = input.get("projectId", "")
    stage_id: str = input.get("stageId", "")

    try:
        agent_input = AgentInput.model_validate(input)
    except ValidationError as exc:
        logger.error("run_video_agent: input validation failed — %s", exc)
        return AgentFailure(
            projectId=project_id, stageId=stage_id,
            error=f"Input validation failed: {exc}",
        ).model_dump()

    logger.info(
        "run_video_agent: start project_id=%s stage_id=%s file=%s",
        agent_input.projectId, agent_input.stageId, agent_input.sourceFilename,
    )

    # Pydantic VideoAgentState fills defaults for everything but the inputs.
    initial_state: dict[str, Any] = {
        "project_id": agent_input.projectId,
        "stage_id": agent_input.stageId,
        "project_name": agent_input.projectName,
        "source_filename": agent_input.sourceFilename,
        "video_artifact": agent_input.videoArtifact.model_dump(),
        "bucket_context": agent_input.bucketContext.model_dump(),
        "active_add_template": agent_input.activeAddTemplate.model_dump(),
    }

    try:
        final_state = graph.invoke(initial_state)
    except Exception as exc:
        logger.error("run_video_agent: graph execution raised — %s", exc, exc_info=True)
        return AgentFailure(
            projectId=agent_input.projectId, stageId=agent_input.stageId,
            error=f"Graph execution failed: {exc}",
        ).model_dump()

    # final_state is dict-like (LangGraph normalizes Pydantic state on return).
    err = final_state.get("error") if isinstance(final_state, dict) else getattr(final_state, "error", None)
    if err:
        msg = err["message"] if isinstance(err, dict) else getattr(err, "message", str(err))
        return AgentFailure(
            projectId=agent_input.projectId, stageId=agent_input.stageId,
            error=msg,
        ).model_dump()

    def _get(field: str, default=None):
        if isinstance(final_state, dict):
            return final_state.get(field, default)
        return getattr(final_state, field, default)

    return AgentOutput(
        projectId=agent_input.projectId,
        stageId=agent_input.stageId,
        contentJsonPath=_get("content_json_path") or "",
        contentMdPath=_get("content_md_path") or "",
        screenshotCount=len(_get("screenshots", []) or []),
        transcriptPath=_get("transcript_path"),
        summary="Generated ADD content from video walkthrough.",
    ).model_dump()
