"""Set up everything needed for a Mode-2 run against the Test Transcript project.

Steps:
  1. Download active ADD template definition.json from the bucket.
  2. Generate an artifact GUID, upload the local video file to the bucket.
  3. Write tests/fixtures/sample_input.json with all real values.

After this runs, you can execute:
    video_add_agent/.venv/Scripts/python.exe video_add_agent/scripts/run_local.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

UIPATH_URL = os.environ["UIPATH_URL"].rstrip("/")
TOKEN = os.environ["UIPATH_ACCESS_TOKEN"]
FOLDER_ID = 10934
BUCKET_ID = 3733

# Selected target — re-targeted to an existing awaiting_approval ADD stage on
# the staging tenant. Original "Test Transcript" project (FF26C485…) was
# wiped from AgentifyStage. Project name is timestamped per run.
from datetime import datetime as _dt

TARGET = {
    "projectId": "7c24244c-a3bf-4a82-a6f2-019dfc98aaf2",
    "projectName": "UiBank_videoTesting_05062026",
    "stageId": "98ba41d3-5a6e-4d23-bdd5-019dfc98b688",
    "templateId": "DE5B0911-F421-44ED-86AD-019DD48DFF02",
    "templateBucketDefinitionPath": "/templates/5e28b9f7-723d-4e30-bbd9-80f522f8e46c/definition.json",
    "promptVersion": "2026-04-28-default-edited-2026-05-06",
}

LOCAL_VIDEO = Path(r"C:\Users\Sindhuja.M\Desktop\Projects\Initiative\Recording\PLDT_SIPOC_01.mp4")


def _orch_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {TOKEN}",
        "X-UIPATH-OrganizationUnitId": str(FOLDER_ID),
        "Accept": "application/json",
    }


def get_read_uri(path: str) -> str:
    url = f"{UIPATH_URL}/odata/Buckets({BUCKET_ID})/UiPath.Server.Configuration.OData.GetReadUri"
    r = httpx.get(url, params={"path": path}, headers=_orch_headers(), timeout=30)
    r.raise_for_status()
    return r.json()["Uri"]


def get_write_uri(path: str, content_type: str) -> str:
    url = f"{UIPATH_URL}/odata/Buckets({BUCKET_ID})/UiPath.Server.Configuration.OData.GetWriteUri"
    r = httpx.get(
        url,
        params={"path": path, "contentType": content_type},
        headers=_orch_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["Uri"]


# ── Step 1: download template definition ─────────────────────────────────────
print("Step 1: download template definition ...")
read_uri = get_read_uri(TARGET["templateBucketDefinitionPath"])
resp = httpx.get(read_uri, follow_redirects=True, timeout=60)
resp.raise_for_status()
definition_text = resp.text
# Validate it's JSON and round-trip to a compact string
defn_obj = json.loads(definition_text)
sections = defn_obj.get("sections") or defn_obj.get("Sections") or []
print(f"  template definition: {len(definition_text)} chars, {len(sections)} sections")
definition_str = json.dumps(defn_obj, ensure_ascii=False)

# ── Step 2: upload video ─────────────────────────────────────────────────────
print()
print("Step 2: upload video to bucket ...")
if not LOCAL_VIDEO.exists():
    print(f"  ERROR: video file not found: {LOCAL_VIDEO}")
    sys.exit(2)
art_id = str(uuid.uuid4())
filename = LOCAL_VIDEO.name
bucket_path = f"projects/{TARGET['projectId']}/raw/{art_id}/{filename}"
print(f"  artifactId: {art_id}")
print(f"  bucketPath: {bucket_path}")

write_uri = get_write_uri(bucket_path, "video/mp4")
size = LOCAL_VIDEO.stat().st_size
with open(LOCAL_VIDEO, "rb") as fh:
    up = httpx.put(
        write_uri,
        content=fh,
        headers={"Content-Type": "video/mp4", "x-ms-blob-type": "BlockBlob"},
        timeout=1800,
    )
up.raise_for_status()
print(f"  uploaded {size:,} bytes")

# ── Step 3: write fixture ────────────────────────────────────────────────────
print()
print("Step 3: write tests/fixtures/sample_input.json ...")
sample_input = {
    "projectId": TARGET["projectId"],
    "stageId": TARGET["stageId"],
    "projectName": TARGET["projectName"],
    "inputType": "video",
    "sourceFilename": filename,
    "videoArtifact": {
        "id": art_id,
        "kind": "video",
        "bucketPath": bucket_path,
        "mimeType": "video/mp4",
        "sizeBytes": size,
    },
    "bucketContext": {"bucketId": BUCKET_ID, "folderId": FOLDER_ID},
    "activeAddTemplate": {
        "templateId": TARGET["templateId"],
        "promptVersion": TARGET["promptVersion"],
        "definition": definition_str,
    },
}
fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "sample_input.json"
fixture_path.parent.mkdir(parents=True, exist_ok=True)
fixture_path.write_text(json.dumps(sample_input, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"  wrote {fixture_path.relative_to(PROJECT_ROOT)}")
print()
print("Setup complete. To run the agent:")
print("  video_add_agent/.venv/Scripts/python.exe video_add_agent/scripts/run_local.py")
