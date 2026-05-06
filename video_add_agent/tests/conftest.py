"""Shared pytest fixtures for the video-add-agent test suite."""
from __future__ import annotations

import copy

import pytest

from video_add_agent.state import (
    AlignedStep,
    Screenshot,
    TranscriptSegment,
    VideoAgentState,
)


SAMPLE_INPUT: dict = {
    "projectId": "proj-123",
    "stageId": "stage-456",
    "projectName": "Invoice Processing Automation",
    "inputType": "video",
    "sourceFilename": "demo.mp4",
    "videoArtifact": {
        "id": "art-789",
        "kind": "video",
        "bucketPath": "projects/proj-123/raw/art-789/demo.mp4",
        "mimeType": "video/mp4",
        "sizeBytes": 10_485_760,
    },
    "bucketContext": {"bucketId": 1, "folderId": 100},
    "activeAddTemplate": {
        "templateId": "tmpl-001",
        "promptVersion": "v1",
        "definition": (
            '{"sections": [{'
            '"heading": {"text": "Purpose", "level": 1},'
            '"blocks": [{"outputKey": "purpose", "kind": "text"}]'
            "}]}"
        ),
    },
}


@pytest.fixture
def sample_input() -> dict:
    return copy.deepcopy(SAMPLE_INPUT)


@pytest.fixture
def sample_state() -> VideoAgentState:
    """A populated VideoAgentState — past transcribe + filter + dedupe + align."""
    return VideoAgentState(
        project_id="proj-123",
        stage_id="stage-456",
        project_name="Invoice Processing Automation",
        source_filename="demo.mp4",
        video_artifact=copy.deepcopy(SAMPLE_INPUT["videoArtifact"]),
        bucket_context=copy.deepcopy(SAMPLE_INPUT["bucketContext"]),
        active_add_template=copy.deepcopy(SAMPLE_INPUT["activeAddTemplate"]),
        raw_transcript=[
            TranscriptSegment(start=0.0, end=5.0, text="User opens the invoice processing app."),
            TranscriptSegment(start=5.0, end=15.0, text="User selects pending invoices queue."),
            TranscriptSegment(start=15.0, end=30.0, text="User reviews invoice INV-001 and approves."),
        ],
        filtered_transcript=[
            TranscriptSegment(start=0.0, end=15.0, text="User opens the invoice processing app. User selects pending invoices queue."),
            TranscriptSegment(start=15.0, end=30.0, text="User reviews invoice INV-001 and approves."),
        ],
        audio_drop_ratio=0.0,
        # Default the multi-mode metadata so generate_sections's
        # grounding gate (Fix 2) doesn't trip in tests that haven't
        # explicitly set up insufficient-grounding scenarios.
        transcript_mode="walkthrough",
        transcript_retention_pct=1.0,
        screenshots=[
            Screenshot(
                timestamp=2.0,
                phash="0123456789abcdef",
                bucket_path="projects/proj-123/raw/image-1/screenshot-01.jpg",
                cluster_size=3,
                vision_analysis={
                    "classification": "screen_recording",
                    "app_name": "Invoice App",
                    "ui_state": "form",
                    "from_cache": False,
                },
            ),
            Screenshot(
                timestamp=20.0,
                phash="fedcba9876543210",
                bucket_path="projects/proj-123/raw/image-2/screenshot-02.jpg",
                cluster_size=2,
                vision_analysis={
                    "classification": "screen_recording",
                    "app_name": "Invoice App",
                    "ui_state": "table",
                    "from_cache": False,
                },
            ),
            Screenshot(
                timestamp=25.0,
                phash="aaaaabbbbbccccc",
                bucket_path="projects/proj-123/raw/image-3/screenshot-03.jpg",
                cluster_size=1,
                vision_analysis={
                    "classification": "screen_recording",
                    "app_name": "Invoice App",
                    "ui_state": "dialog",
                    "from_cache": False,
                },
            ),
        ],
        aligned_steps=[
            AlignedStep(
                timestamp_start=0.0, timestamp_end=15.0,
                transcript="User opens the invoice processing app. User selects pending invoices queue.",
                screenshot_indices=[0],
            ),
            AlignedStep(
                timestamp_start=15.0, timestamp_end=30.0,
                transcript="User reviews invoice INV-001 and approves.",
                screenshot_indices=[1],
            ),
        ],
    )


@pytest.fixture
def state_with_sections(sample_state: VideoAgentState) -> VideoAgentState:
    sample_state.sections = {"purpose": "This process automates invoice approval."}
    return sample_state


@pytest.fixture
def mock_bucket(monkeypatch: pytest.MonkeyPatch):
    """Patch all bucket helpers to no-ops."""

    def _fake_download(*_args, **_kwargs) -> bytes:
        return b"fake-video-content"

    def _fake_upload(*_args, **_kwargs) -> None:
        pass

    def _fake_exists(*_args, **_kwargs) -> bool:
        return True

    monkeypatch.setattr("video_add_agent.utils.bucket.download_bytes", _fake_download)
    monkeypatch.setattr("video_add_agent.utils.bucket.upload_bytes", _fake_upload)
    monkeypatch.setattr("video_add_agent.utils.bucket.check_file_exists", _fake_exists)
    monkeypatch.setattr(
        "video_add_agent.nodes.validate_artifact.check_file_exists", _fake_exists
    )


@pytest.fixture
def mock_entities(monkeypatch: pytest.MonkeyPatch):
    """Patch the Data Fabric stage update to a no-op."""
    monkeypatch.setattr("video_add_agent.utils.entities.update_stage", lambda **_: None)
