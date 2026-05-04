from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class VideoArtifact(BaseModel):
    id: str
    kind: Literal["video"]
    bucketPath: str
    mimeType: str
    sizeBytes: int


class BucketContext(BaseModel):
    bucketId: int
    folderId: int


class ActiveAddTemplate(BaseModel):
    templateId: str
    promptVersion: str
    definition: str  # JSON string — deserialised into TemplateDefinition by generate_sections


class AgentInput(BaseModel):
    projectId: str
    stageId: str
    projectName: str
    inputType: Literal["video"]
    sourceFilename: str
    videoArtifact: VideoArtifact
    bucketContext: BucketContext
    activeAddTemplate: ActiveAddTemplate
