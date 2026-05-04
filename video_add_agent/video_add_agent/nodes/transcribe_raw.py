"""Node — transcribe_raw

Downloads the video, extracts audio, runs local Whisper, uploads the raw
transcript text to the bucket. Sets `state.raw_transcript`.

Frame extraction + screenshot upload moved out — those are now the job of
`extract_keyframes` + `dedupe_screenshots` running in the parallel branch.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from video_add_agent.models.input import BucketContext, VideoArtifact
from video_add_agent.state import ErrorInfo, TranscriptSegment, VideoAgentState
from video_add_agent.utils.bucket import download_bytes, upload_bytes
from video_add_agent.utils.media import (
    extract_audio,
    run_whisper,
    segments_to_timestamped_text,
)

logger = logging.getLogger(__name__)

_WHISPER_MODEL = "base"


def transcribe_raw_node(state: VideoAgentState) -> dict:
    logger.info("transcribe_raw: start project_id=%s", state.project_id)
    try:
        artifact = VideoArtifact.model_validate(state.video_artifact)
        bucket_ctx = BucketContext.model_validate(state.bucket_context)
        project_id = state.project_id

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            logger.info("transcribe_raw: downloading %s", artifact.bucketPath)
            video_bytes = download_bytes(
                bucket_id=bucket_ctx.bucketId,
                folder_id=bucket_ctx.folderId,
                path=artifact.bucketPath,
            )
            video_path = tmp_path / state.source_filename
            video_path.write_bytes(video_bytes)

            logger.info("transcribe_raw: extracting audio")
            audio_path = extract_audio(video_path, tmp_path / "audio.wav")

            logger.info("transcribe_raw: running Whisper model=%s", _WHISPER_MODEL)
            segments = run_whisper(audio_path, model_size=_WHISPER_MODEL)
            transcript_text = segments_to_timestamped_text(segments)

            transcript_path = f"projects/{project_id}/stages/add/output/transcript.txt"
            upload_bytes(
                bucket_id=bucket_ctx.bucketId,
                folder_id=bucket_ctx.folderId,
                path=transcript_path,
                content=transcript_text.encode("utf-8"),
                mime_type="text/plain",
            )

        raw_transcript = [
            TranscriptSegment(start=s.start, end=s.end, text=s.text) for s in segments
        ]
        logger.info("transcribe_raw: done segments=%d", len(raw_transcript))
        return {
            "raw_transcript": raw_transcript,
            "transcript_path": transcript_path,
        }

    except Exception as exc:
        logger.error("transcribe_raw: failed — %s", exc, exc_info=True)
        return {"error": ErrorInfo(message=str(exc), failed_node="transcribe_raw")}
