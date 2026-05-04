from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class AgentOutput(BaseModel):
    success: Literal[True] = True
    projectId: str
    stageId: str
    contentJsonPath: str
    contentMdPath: str
    screenshotCount: int
    transcriptPath: Optional[str] = None
    summary: str = "Generated ADD content from video walkthrough."


class AgentFailure(BaseModel):
    success: Literal[False] = False
    projectId: str
    stageId: str
    error: str
