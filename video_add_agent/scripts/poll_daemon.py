"""Polling daemon — picks up video ADD jobs from the senior's Coded App
and runs the LangGraph agent to process them.

Architecture:
- Polls AgentifyStage entity every POLL_INTERVAL_S for rows where
  status='running' AND kind='add'.
- For each: looks up the AgentifyArtifact rows for the project and selects
  the one whose mimeType starts with 'video/'. Non-video stages are skipped
  (the senior's in-browser add stage handles those).
- Looks up the active AgentifyTemplate for kind='add', downloads its
  definition.json from the bucket, and assembles an input dict matching
  AgentInput's schema.
- Calls run_video_agent. The agent itself updates the row to
  awaiting_approval / rejected on completion.
- Tracks processed_ids in-memory to avoid double-processing within a
  single daemon run.
- Heartbeat: any row that's been status='running' for more than
  STUCK_THRESHOLD_S without our daemon picking it up gets flipped to
  'rejected' with errorDesc explaining the stuck state. This stops
  the senior's UI from spinning forever if the daemon was offline when
  the upload happened.

Single-instance: a lockfile at LOCK_PATH stores the running PID. A second
daemon won't start while the first is alive.

Trust boundary: identical to scripts/run_local.py — talks only to UiPath
cloud APIs using the PAT in .env. No new external services.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from video_add_agent.entry import run_video_agent
from video_add_agent.utils.bucket import download_bytes
from video_add_agent.utils.entities import update_stage

POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "10"))
STUCK_THRESHOLD_S = int(os.environ.get("STUCK_THRESHOLD_S", "1800"))  # 30 min
STAGE_KIND = "add"
TARGET_STATUS = "running"

# Senior's app stores video artifacts with kind='video' and a mimeType
# starting with 'video/'. We accept either signal as definitive.
VIDEO_ARTIFACT_KIND = "video"
VIDEO_MIME_PREFIX = "video/"

UIPATH_URL = os.environ["UIPATH_URL"].rstrip("/")
TOKEN = os.environ["UIPATH_ACCESS_TOKEN"]
BUCKET_ID = int(os.environ.get("BUCKET_ID", "3733"))
FOLDER_ID = int(os.environ.get("FOLDER_ID", "10934"))

LOCK_PATH = Path(os.environ.get("DAEMON_LOCK", str(Path.home() / ".video_add_agent_daemon.lock")))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("poll_daemon")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}


def _entity_read(entity_name: str, top: int = 100) -> list[dict]:
    r = httpx.get(
        f"{UIPATH_URL}/dataservice_/api/EntityService/{entity_name}/read",
        headers=_headers(),
        params={"$top": top},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("value", [])


def list_running_add_stages() -> list[dict]:
    return [
        row for row in _entity_read("AgentifyStage", top=200)
        if row.get("kind") == STAGE_KIND and row.get("status") == TARGET_STATUS
    ]


def find_video_artifact(project_id: str) -> dict | None:
    for row in _entity_read("AgentifyArtifact", top=200):
        if (row.get("projectId") or "").lower() != project_id.lower():
            continue
        mime = row.get("mimeType") or ""
        kind = row.get("kind") or ""
        if kind == VIDEO_ARTIFACT_KIND or mime.startswith(VIDEO_MIME_PREFIX):
            return row
    return None


def get_project(project_id: str) -> dict | None:
    for row in _entity_read("AgentifyProject", top=200):
        if (row.get("Id") or "").lower() == project_id.lower():
            return row
    return None


def load_active_add_template() -> dict | None:
    """Mirror senior's loadActiveTemplate('add'): query for kind='add' with
    isActive=true AND status='active'; download its definition.json."""
    candidates = [
        row for row in _entity_read("AgentifyTemplate", top=50)
        if row.get("kind") == STAGE_KIND
        and row.get("isActive") is True
        and row.get("status") == "active"
    ]
    if not candidates:
        logger.error("no active AgentifyTemplate row for kind=%s", STAGE_KIND)
        return None
    if len(candidates) > 1:
        candidates.sort(key=lambda r: r.get("updatedAt") or "", reverse=True)
    record = candidates[0]
    bucket_path = record.get("bucketDefinitionPath") or ""
    if not bucket_path:
        logger.error("active template %s has no bucketDefinitionPath", record.get("Id"))
        return None
    try:
        raw = download_bytes(bucket_id=BUCKET_ID, folder_id=FOLDER_ID, path=bucket_path)
        definition_str = raw.decode("utf-8")
        # Validate as JSON early — fail loud if the template is corrupt.
        json.loads(definition_str)
    except Exception as exc:
        logger.error("failed to download/parse template definition (%s): %s", bucket_path, exc)
        return None
    return {
        "templateId": record["Id"],
        "promptVersion": record.get("promptVersion") or "",
        "definition": definition_str,
        "bucketDefinitionPath": bucket_path,
    }


def build_input_dict(
    stage: dict, artifact: dict, project: dict, template: dict,
) -> dict[str, Any]:
    return {
        "projectId": stage["projectId"],
        "stageId": stage["Id"],
        "projectName": project.get("name") or stage.get("projectId", ""),
        "inputType": "video",
        "sourceFilename": Path(artifact.get("bucketPath") or "").name or "video.mp4",
        "videoArtifact": {
            "id": artifact["Id"],
            "kind": "video",
            "bucketPath": artifact["bucketPath"],
            "mimeType": artifact.get("mimeType") or "video/mp4",
            "sizeBytes": int(artifact.get("sizeBytes") or 0),
        },
        "bucketContext": {"bucketId": BUCKET_ID, "folderId": FOLDER_ID},
        "activeAddTemplate": {
            "templateId": template["templateId"],
            "promptVersion": template["promptVersion"],
            "definition": template["definition"],
        },
    }


def reap_stuck_rows(running: list[dict], processed_ids: set[str]) -> None:
    """Flip rows that have been running > STUCK_THRESHOLD_S with no daemon
    pickup to 'rejected'. Avoids leaving the senior's UI spinning forever
    when the daemon was offline at upload time."""
    now = datetime.now(timezone.utc)
    for row in running:
        rid = row["Id"]
        if rid in processed_ids:
            continue
        started_raw = row.get("startedAt") or ""
        if not started_raw:
            continue
        try:
            # Tolerate both "...+00:00" and "...Z" suffixes.
            started = datetime.fromisoformat(started_raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        elapsed = (now - started).total_seconds()
        if elapsed <= STUCK_THRESHOLD_S:
            continue
        msg = (
            f"Daemon found stage running for {int(elapsed)}s without a video "
            f"artifact match or before daemon was online. Re-upload to retry."
        )
        try:
            update_stage(folder_id=FOLDER_ID, stage_id=rid, payload={
                "status": "rejected",
                "errorDesc": msg[:2000],
            })
            logger.warning("reaped stuck row %s after %ds", rid, int(elapsed))
            processed_ids.add(rid)
        except Exception as exc:
            logger.error("failed to reap stuck row %s: %s", rid, exc)


def process_one(stage: dict, processed_ids: set[str]) -> None:
    rid = stage["Id"]
    project_id = stage["projectId"]
    logger.info("processing stage %s (project %s)", rid, project_id)

    artifact = find_video_artifact(project_id)
    if artifact is None:
        # Not a video job — senior's in-browser add stage handles this. Skip.
        logger.info("stage %s has no video artifact, skipping", rid)
        return

    project = get_project(project_id) or {"name": project_id}
    template = load_active_add_template()
    if template is None:
        update_stage(folder_id=FOLDER_ID, stage_id=rid, payload={
            "status": "rejected",
            "errorDesc": "Daemon could not load active ADD template.",
        })
        processed_ids.add(rid)
        return

    input_dict = build_input_dict(stage, artifact, project, template)
    try:
        result = run_video_agent(input_dict)
        if result.get("success"):
            logger.info("stage %s -> awaiting_approval (success)", rid)
        else:
            logger.error("stage %s -> rejected: %s", rid, result.get("error"))
        # The agent already wrote the final stage status, no need to repeat here.
    except Exception as exc:
        # Defensive: any uncaught exception inside the agent. Force-flip the row
        # so the user sees a clear failure state instead of indefinite "running".
        logger.exception("agent crashed processing %s: %s", rid, exc)
        try:
            update_stage(folder_id=FOLDER_ID, stage_id=rid, payload={
                "status": "rejected",
                "errorDesc": f"[poll_daemon] agent crashed: {exc}".replace("\n", " ")[:2000],
            })
        except Exception:
            pass
    finally:
        processed_ids.add(rid)


def acquire_lock() -> None:
    if LOCK_PATH.exists():
        try:
            pid = int(LOCK_PATH.read_text().strip())
        except Exception:
            pid = -1
        if pid > 0:
            # Best-effort liveness check on Windows + POSIX.
            try:
                os.kill(pid, 0)
                raise SystemExit(
                    f"daemon already running (pid={pid}, lock={LOCK_PATH}). "
                    f"Stop it first or delete the lockfile if stale."
                )
            except OSError:
                # Stale — pid no longer alive.
                pass
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")


def release_lock() -> None:
    try:
        LOCK_PATH.unlink()
    except FileNotFoundError:
        pass


def main() -> int:
    acquire_lock()
    signal.signal(signal.SIGINT, lambda *_a: (release_lock(), sys.exit(130)))
    signal.signal(signal.SIGTERM, lambda *_a: (release_lock(), sys.exit(143)))

    logger.info(
        "poll_daemon starting: poll_interval=%ds stuck_threshold=%ds bucket=%d folder=%d",
        POLL_INTERVAL_S, STUCK_THRESHOLD_S, BUCKET_ID, FOLDER_ID,
    )
    processed_ids: set[str] = set()
    try:
        while True:
            try:
                running = list_running_add_stages()
                if running:
                    logger.info("found %d running ADD stage(s)", len(running))
                for row in running:
                    if row["Id"] in processed_ids:
                        continue
                    process_one(row, processed_ids)
                # Reap rows that have been running too long with no pickup.
                reap_stuck_rows(running, processed_ids)
            except Exception as exc:
                logger.error("poll loop error: %s", exc, exc_info=True)
            time.sleep(POLL_INTERVAL_S)
    finally:
        release_lock()


if __name__ == "__main__":
    sys.exit(main())
