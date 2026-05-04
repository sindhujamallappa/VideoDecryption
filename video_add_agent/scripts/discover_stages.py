"""Data Fabric discovery: list projects and their ADD stages.

Reads UIPATH_URL + UIPATH_ACCESS_TOKEN from .env.
Tries the Data Fabric REST surface the agent itself uses
(/dataservice_/api/EntityService/{Entity}) and falls back to entity-id URLs
if entity-name URLs don't resolve on this tenant.

Usage:
    video_add_agent/.venv/Scripts/python.exe video_add_agent/scripts/discover_stages.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# Entity type IDs from the Agentify .env (VITE_UIPATH_ENTITY_*)
ENTITY_TYPE_IDS = {
    "Project": "954e3dd8-dd3f-f111-8ef3-6045bd024144",
    "Stage": "c39b27d4-de3f-f111-8ef3-6045bd024144",
    "Artifact": "9dbb000f-df3f-f111-8ef3-6045bd024144",
    "Template": "daae9269-c542-f111-8ef3-6045bd024144",
}

FOLDER_ID = 10934  # from Agentify .env


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-UIPATH-OrganizationUnitId": str(FOLDER_ID),
        "Accept": "application/json",
    }


def _try_get(url: str, token: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    try:
        resp = httpx.get(url, headers=_headers(token), params=params or {}, timeout=30)
        if resp.headers.get("content-type", "").startswith("application/json"):
            body: Any = resp.json()
        else:
            body = resp.text[:500]
        return resp.status_code, body
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"


def list_entity(base: str, token: str, entity_name: str) -> list[dict] | None:
    """Try multiple URL conventions; return the first one that returns a list."""
    candidates = [
        f"{base}/dataservice_/api/EntityService/{entity_name}",
        f"{base}/dataservice_/api/EntityService/{ENTITY_TYPE_IDS[entity_name]}/read",
        f"{base}/dataservice_/api/EntityService/{ENTITY_TYPE_IDS[entity_name]}",
    ]
    for url in candidates:
        status, body = _try_get(url, token, params={"$top": 50})
        print(f"  [{status}] {url}")
        if status == 200:
            # Body shape varies: {value: [...]} or [...] or {Items: [...]}
            if isinstance(body, list):
                return body
            if isinstance(body, dict):
                for key in ("value", "Items", "items", "data", "Records"):
                    if key in body and isinstance(body[key], list):
                        return body[key]
                # Single object — treat as one-element list
                return [body]
        elif status in (401, 403):
            print(f"    ⚠ auth error for {entity_name}: {body!r:.200}")
            return None
    return None


def main() -> int:
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env", override=True)
    base = os.environ.get("UIPATH_URL", "").rstrip("/")
    token = os.environ.get("UIPATH_ACCESS_TOKEN", "")
    if not base or not token:
        print("ERROR: UIPATH_URL or UIPATH_ACCESS_TOKEN missing from .env")
        return 2

    print(f"Tenant: {base}")
    print(f"Folder: {FOLDER_ID}")
    print()

    print("Discovering Project records ...")
    projects = list_entity(base, token, "Project")
    if projects is None:
        print("\n❌ Could not list Project entity. Check token scopes (DataFabric.Data.Read).")
        return 1
    print(f"  → {len(projects)} project(s)\n")

    print("Discovering Stage records ...")
    stages = list_entity(base, token, "Stage")
    if stages is None:
        print("❌ Could not list Stage entity.")
        return 1
    print(f"  → {len(stages)} stage(s)\n")

    # ── Show projects ────────────────────────────────────────────────────────
    print("=" * 78)
    print("PROJECTS")
    print("=" * 78)
    for p in projects[:20]:
        pid = p.get("Id") or p.get("id") or "?"
        name = p.get("Name") or p.get("name") or p.get("ProjectName") or "?"
        print(f"  {pid}  {name!r}")
    print()

    # ── Show stages ──────────────────────────────────────────────────────────
    print("=" * 78)
    print("STAGES (first 30)")
    print("=" * 78)
    for s in stages[:30]:
        sid = s.get("Id") or s.get("id") or "?"
        kind = s.get("Kind") or s.get("kind") or s.get("StageKind") or "?"
        status = s.get("Status") or s.get("status") or "?"
        proj = s.get("ProjectId") or s.get("projectId") or "?"
        print(f"  {sid}  kind={kind:<5}  status={status:<22}  project={proj}")
    print()

    # ── Filter to ADD/PDD only ───────────────────────────────────────────────
    add_stages = [
        s for s in stages
        if str(s.get("Kind") or s.get("kind") or "").lower() in ("add", "pdd")
    ]
    print("=" * 78)
    print(f"ADD/PDD STAGES ({len(add_stages)} total) — eligible targets")
    print("=" * 78)
    for s in add_stages[:20]:
        sid = s.get("Id") or s.get("id")
        kind = s.get("Kind") or s.get("kind")
        status = s.get("Status") or s.get("status")
        proj = s.get("ProjectId") or s.get("projectId")
        # Find the project name
        proj_name = "?"
        for p in projects:
            if (p.get("Id") or p.get("id")) == proj:
                proj_name = p.get("Name") or p.get("ProjectName") or "?"
                break
        print(f"  stageId={sid}")
        print(f"    project={proj_name!r} ({proj})")
        print(f"    kind={kind}  status={status}")
        print()

    # Persist raw response for debugging
    out = project_root / "scripts" / "_discovery_raw.json"
    out.write_text(json.dumps({"projects": projects, "stages": stages}, indent=2, default=str))
    print(f"Raw response saved to: {out.relative_to(project_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
