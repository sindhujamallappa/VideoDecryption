"""State for the video-to-ADD LangGraph agent.

Pydantic BaseModel — supersedes the previous TypedDict.
Each node returns a partial dict that LangGraph applies via `model_copy(update=...)`.
Disjoint per-branch writes mean no `Annotated[..., reducer]` is required.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


# ── Domain models held inside list fields on the state ─────────────────────


class TranscriptSegment(BaseModel):
    """One Whisper segment with timestamps (seconds from video start) and text."""

    start: float
    end: float
    text: str


class ScreenshotCandidate(BaseModel):
    """A frame surviving the consecutive-pHash-distance gate in extract_keyframes.

    Not yet uploaded; lives only on disk during the run. Dedupe selects one
    representative per cluster and uploads it as a `Screenshot`.
    """

    timestamp: float
    phash: str
    frame_path: str  # local temp path; deleted after upload


class Screenshot(BaseModel):
    """A deduplicated screenshot — one representative per cluster, uploaded."""

    timestamp: float
    phash: str
    bucket_path: str
    cluster_size: int  # how many candidates this rep stands in for (debug)


class AlignedStep(BaseModel):
    """A unit of process timeline pairing a transcript window with screenshots.

    Produced by align_steps; consumed by generate_sections.
    """

    timestamp_start: float
    timestamp_end: float
    transcript: str
    screenshot_indices: list[int] = Field(default_factory=list)


class CoverageGap(BaseModel):
    """Region of filtered_transcript with no aligned screenshot inside tolerance.

    Drives the coverage_check → align_steps retry loop.
    """

    start: float
    end: float
    reason: str


class ErrorInfo(BaseModel):
    """Top-level error — its presence routes to handle_error."""

    message: str
    failed_node: str


# ── Top-level graph state ──────────────────────────────────────────────────


class VideoAgentState(BaseModel):
    """LangGraph state for the video → ADD pipeline."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ── Inputs (set once at START) ─────────────────────────────────────────
    project_id: str
    stage_id: str
    project_name: str
    source_filename: str
    video_artifact: dict
    bucket_context: dict
    active_add_template: dict

    # ── Transcription ──────────────────────────────────────────────────────
    raw_transcript: list[TranscriptSegment] = Field(
        default_factory=list,
        description="Unfiltered Whisper output. Preserved for debugging/audit.",
    )
    filtered_transcript: list[TranscriptSegment] = Field(
        default_factory=list,
        description="Process-relevant subset after LLM relevance scoring.",
    )
    audio_drop_ratio: float = Field(
        default=0.0,
        description="Fraction of audio seconds dropped (0.0–1.0). Logged per run.",
    )

    # ── Vision / screenshots ───────────────────────────────────────────────
    keyframe_candidates: list[ScreenshotCandidate] = Field(
        default_factory=list,
        description="Frames where consecutive pHash distance > KEYFRAME_THRESHOLD.",
    )
    screenshots: list[Screenshot] = Field(
        default_factory=list,
        description="Deduplicated, uploaded screenshots referenced by the ADD.",
    )

    # ── Alignment & coverage ───────────────────────────────────────────────
    aligned_steps: list[AlignedStep] = Field(
        default_factory=list,
        description="Transcript windows paired with screenshot indices.",
    )
    coverage_gaps: list[CoverageGap] = Field(
        default_factory=list,
        description="Non-empty + retries_left → coverage_check routes back to align_steps.",
    )
    relax_factor: int = Field(
        default=0,
        description="Incremented per align_steps retry; widens matching tolerance.",
    )

    # ── ADD generation & validation ────────────────────────────────────────
    sections: dict[str, str] = Field(default_factory=dict)
    validation_errors: list[str] = Field(
        default_factory=list,
        description="Constraint hints from validate_output. Non-empty + retries_left "
                    "→ validate_output routes back to generate_sections with these hints.",
    )

    # ── Persistence outputs ────────────────────────────────────────────────
    content_json_path: str | None = None
    content_md_path: str | None = None
    transcript_path: str | None = None

    # ── Retry tracking + error routing ─────────────────────────────────────
    retry_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Per-loop retry count. Capped at MAX_RETRIES (set in graph.py).",
    )
    error: ErrorInfo | None = None
