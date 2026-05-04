"""Probe more update patterns + use OPTIONS to discover allowed methods."""
from __future__ import annotations

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

base = os.environ["UIPATH_URL"].rstrip("/")
token = os.environ["UIPATH_ACCESS_TOKEN"]
folder = 10934
STAGE_ID = "9C0ACD6A-E82C-4756-A4B4-019DD8384FC0"


def hit(url: str, label: str, *, method: str = "POST", body=None) -> None:
    h = {
        "Authorization": f"Bearer {token}",
        "X-UIPATH-OrganizationUnitId": str(folder),
        "Accept": "application/json",
    }
    if body is not None:
        h["Content-Type"] = "application/json"
    try:
        r = httpx.request(method, url, headers=h, json=body, timeout=20)
        text = r.text[:200]
        allowed = r.headers.get("allow") or r.headers.get("Allow")
        suffix = f"  Allow: {allowed}" if allowed else ""
        print(f"  [{r.status_code}] {method} {label}{suffix}")
        if r.status_code not in (200, 204):
            print(f"      body: {text!r}")
        else:
            print(f"      ok: {text[:150]!r}")
    except Exception as exc:
        print(f"  [ERR] {label}: {exc}")


# 1) OPTIONS on /AgentifyStage variants — reveals allowed methods
print("=== OPTIONS to find allowed methods ===")
for u in [
    f"{base}/dataservice_/api/EntityService/AgentifyStage",
    f"{base}/dataservice_/api/EntityService/AgentifyStage/{STAGE_ID}",
    f"{base}/dataservice_/api/EntityService/AgentifyStage/read",
    f"{base}/dataservice_/api/EntityService/AgentifyStage/update",
]:
    hit(u, u.replace(base, ""), method="OPTIONS")

print()
print("=== OData parenthesized syntax ===")
hit(
    f"{base}/dataservice_/api/EntityService/AgentifyStage({STAGE_ID})",
    "PATCH /AgentifyStage(<id>) odata-style",
    method="PATCH",
    body={"status": "running"},
)
hit(
    f"{base}/dataservice_/api/EntityService/AgentifyStage('{STAGE_ID}')",
    "PATCH /AgentifyStage('<id>') quoted",
    method="PATCH",
    body={"status": "running"},
)

print()
print("=== More action names ===")
for action in ["edit", "save", "modify", "patch", "post", "write", "save_record", "edit_record", "patch_record"]:
    hit(
        f"{base}/dataservice_/api/EntityService/AgentifyStage/{action}",
        f"POST /AgentifyStage/{action}",
        method="POST",
        body={"Id": STAGE_ID, "status": "running"},
    )

print()
print("=== Records namespace ===")
hit(
    f"{base}/dataservice_/api/Records/AgentifyStage/{STAGE_ID}",
    "PATCH /Records/AgentifyStage/<id>",
    method="PATCH",
    body={"status": "running"},
)
hit(
    f"{base}/dataservice_/api/EntityRecord/AgentifyStage/{STAGE_ID}",
    "PATCH /EntityRecord/AgentifyStage/<id>",
    method="PATCH",
    body={"status": "running"},
)
