"""Download content.json + content.md from the bucket so we can inspect them."""
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
bucket = 3733
project_id = "7c24244c-a3bf-4a82-a6f2-019dfc98aaf2"

out_dir = Path(__file__).resolve().parent / "_run_output"
out_dir.mkdir(exist_ok=True)


def download(name: str, mime: str) -> Path:
    path = f"projects/{project_id}/stages/add/output/{name}"
    h = {"Authorization": f"Bearer {token}", "X-UIPATH-OrganizationUnitId": str(folder)}
    r = httpx.get(
        f"{base}/odata/Buckets({bucket})/UiPath.Server.Configuration.OData.GetReadUri",
        params={"path": path},
        headers=h,
        timeout=30,
    )
    r.raise_for_status()
    uri = r.json()["Uri"]
    blob = httpx.get(uri, follow_redirects=True, timeout=120)
    blob.raise_for_status()
    out = out_dir / name
    out.write_bytes(blob.content)
    print(f"  saved {out.relative_to(Path.cwd())} ({len(blob.content):,} bytes)")
    return out


print("Downloading agent outputs ...")
json_file = download("content.json", "application/json")
md_file = download("content.md", "text/markdown")
download("transcript.txt", "text/plain")

print()
print("=== content.json structure ===")
data = json.loads(json_file.read_text(encoding="utf-8"))
print(f"Total keys (sections): {len(data)}")
for k in list(data.keys())[:30]:
    v = data[k]
    if isinstance(v, str):
        preview = v.replace("\n", " ")[:100]
        print(f"  {k!r:<30} {len(v):5d} chars   {preview!r}")
    else:
        print(f"  {k!r:<30} {type(v).__name__}: {str(v)[:100]!r}")
