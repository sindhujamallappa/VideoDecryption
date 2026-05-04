"""Dry-run the full graph against a local video, mocking bucket + entities.

Use this when the staging tenant is unavailable but you still want to
exercise the full LangGraph topology (fan-out, join, retry loops, etc.)
end-to-end with real Whisper, ffmpeg, and LLM calls. Outputs go to
scripts/_dry_output/.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

LOCAL_VIDEO = Path(r"C:\Users\Sindhuja.M\Desktop\Projects\Initiative\Recording\UiBankProcessWalkthrough.mp4")
DUMP_DIR = PROJECT_ROOT / "scripts" / "_dry_output"
DUMP_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("dry_run")

if not LOCAL_VIDEO.exists():
    logger.error("local video missing: %s", LOCAL_VIDEO)
    sys.exit(2)

VIDEO_BYTES = LOCAL_VIDEO.read_bytes()
logger.info("loaded local video: %d bytes", len(VIDEO_BYTES))

_uploads: list[dict] = []


def _fake_download(*, bucket_id, folder_id, path):
    logger.info("[mock-download] %s", path)
    return VIDEO_BYTES


def _fake_upload(*args, **kwargs):
    # Real signature: upload_bytes(bucket_id, folder_id, path, content, mime_type=...)
    path = kwargs.get("path") or (args[2] if len(args) > 2 else "?")
    content = kwargs.get("content") or (args[3] if len(args) > 3 else b"")
    mime = kwargs.get("mime_type") or kwargs.get("content_type") or (args[4] if len(args) > 4 else "?")
    logger.info("[mock-upload] %s (%d bytes, %s)", path, len(content), mime)
    _uploads.append({"path": path, "size": len(content), "mime": mime})
    if mime in ("text/markdown", "application/json", "text/plain"):
        out = DUMP_DIR / Path(path).name
        out.write_bytes(content)


def _fake_exists(*, bucket_id, folder_id, path):
    logger.info("[mock-exists] %s -> True", path)
    return True


def _fake_update_stage(*, folder_id, stage_id, payload):
    logger.info("[mock-stage] %s ← %s", stage_id, payload)


# Patch at source modules AND at every from-import site
PATCHES = [
    # bucket source + import sites
    patch("video_add_agent.utils.bucket.download_bytes", _fake_download),
    patch("video_add_agent.utils.bucket.upload_bytes", _fake_upload),
    patch("video_add_agent.utils.bucket.check_file_exists", _fake_exists),
    patch("video_add_agent.nodes.validate_artifact.check_file_exists", _fake_exists),
    patch("video_add_agent.nodes.transcribe_raw.download_bytes", _fake_download),
    patch("video_add_agent.nodes.transcribe_raw.upload_bytes", _fake_upload),
    patch("video_add_agent.nodes.extract_keyframes.download_bytes", _fake_download),
    patch("video_add_agent.nodes.dedupe_screenshots.upload_bytes", _fake_upload),
    patch("video_add_agent.nodes.persist_output.upload_bytes", _fake_upload),
    # entities source + import sites
    patch("video_add_agent.utils.entities.update_stage", _fake_update_stage),
    patch("video_add_agent.nodes.validate_artifact.update_stage", _fake_update_stage),
    patch("video_add_agent.nodes.persist_output.update_stage", _fake_update_stage),
    patch("video_add_agent.nodes.handle_error.update_stage", _fake_update_stage),
]


def main() -> int:
    fixture = PROJECT_ROOT / "tests" / "fixtures" / "sample_input.json"
    sample_input = json.loads(fixture.read_text(encoding="utf-8"))
    logger.info("using fixture: %s", fixture.name)

    # Import entry first so all transitive modules are loaded — the
    # from-import patches need their target modules to exist.
    from video_add_agent.entry import run_video_agent

    for p in PATCHES:
        p.start()
    try:
        result = run_video_agent(sample_input)
    finally:
        for p in PATCHES:
            p.stop()

    out = DUMP_DIR / "result.json"
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    logger.info("wrote %s", out)
    logger.info("total mock uploads: %d", len(_uploads))
    print()
    print("=" * 60)
    print(f"success: {result.get('success')}")
    if not result.get("success"):
        print(f"error: {result.get('error')}")
    print(f"output dir: {DUMP_DIR}")
    print("=" * 60)
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
