"""Find the ADD stage row + active ADD template on this tenant.

Outputs the full records so we can populate sample_input.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
base = os.environ["UIPATH_URL"].rstrip("/")
token = os.environ["UIPATH_ACCESS_TOKEN"]
folder = 10934


def get_records(entity: str) -> list[dict]:
    url = f"{base}/dataservice_/api/EntityService/{entity}/read"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-UIPATH-OrganizationUnitId": str(folder),
        "Accept": "application/json",
    }
    r = httpx.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    body = r.json()
    return body["value"] if isinstance(body, dict) else body


def show(label: str, rec: dict) -> None:
    print(f"--- {label} ---")
    print(json.dumps(rec, indent=2, default=str))
    print()


# 1) Projects
projects = get_records("AgentifyProject")
print(f"# AgentifyProject: {len(projects)} record(s)\n")
for p in projects:
    pid = p.get("Id") or p.get("id")
    print(f"  Id={pid}")
    print(f"    name={p.get('name')!r}  status={p.get('status')}  currentStageKind={p.get('currentStageKind')!r}")
    print(f"    ownerEmail={p.get('ownerEmail')}")
print()

# 2) Stages — full records for ADD or PDD
stages = get_records("AgentifyStage")
print(f"# AgentifyStage: {len(stages)} record(s) total\n")
add_pdd_stages = [
    s for s in stages
    if str(s.get("kind") or "").lower() in ("add", "pdd")
]
print(f"# Of those, {len(add_pdd_stages)} are ADD or PDD\n")
for s in add_pdd_stages:
    show(f"AgentifyStage [kind={s.get('kind')}]", s)

# 3) Active ADD templates
templates = get_records("AgentifyTemplate")
print(f"# AgentifyTemplate: {len(templates)} record(s) total\n")
add_templates = [
    t for t in templates
    if str(t.get("kind") or "").lower() == "add" and t.get("isActive")
]
print(f"# Active ADD templates: {len(add_templates)}\n")
for t in add_templates:
    show("AgentifyTemplate [kind=add isActive=true]", t)

# 4) Existing artifacts (to learn bucketPath conventions)
arts = get_records("AgentifyArtifact")
print(f"# AgentifyArtifact: {len(arts)} record(s)\n")
for a in arts[:5]:
    print(f"  Id={a.get('Id') or a.get('id')}  kind={a.get('kind')}  bucketPath={a.get('bucketPath')!r}")
print()

# Save full dumps for reference
out = Path(__file__).resolve().parent / "_target_dump.json"
out.write_text(
    json.dumps(
        {
            "projects": projects,
            "stages_add_pdd": add_pdd_stages,
            "all_stages": stages,
            "templates_active_add": add_templates,
            "artifacts": arts,
        },
        indent=2,
        default=str,
    )
)
print(f"Full dump saved to: {out}")
