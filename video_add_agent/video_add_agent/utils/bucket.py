"""UiPath Storage Bucket read/write helpers.

Uses the UiPath Orchestrator REST API (pre-signed URI pattern):
  GET /odata/Buckets({Id})/UiPath.Server.Configuration.OData.GetReadUri(path='{path}')
  GET /odata/Buckets({Id})/UiPath.Server.Configuration.OData.GetWriteUri(path='{path}',contentType='{type}')
"""
from __future__ import annotations

import logging
import os

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_UIPATH_URL = os.environ.get("UIPATH_URL", "").rstrip("/")
_ACCESS_TOKEN = os.environ.get("UIPATH_ACCESS_TOKEN", "")

_RETRY = dict(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))


def _headers(folder_id: int) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_ACCESS_TOKEN}",
        "X-UIPATH-OrganizationUnitId": str(folder_id),
    }


def _orchestrator(path: str) -> str:
    return f"{_UIPATH_URL}/{path.lstrip('/')}"


@retry(**_RETRY)
def download_bytes(bucket_id: int, folder_id: int, path: str) -> bytes:
    resp = httpx.get(
        _orchestrator(f"odata/Buckets({bucket_id})/UiPath.Server.Configuration.OData.GetReadUri"),
        params={"path": path},
        headers=_headers(folder_id),
        timeout=30,
    )
    resp.raise_for_status()
    read_uri: str = resp.json()["Uri"]
    download = httpx.get(read_uri, timeout=1800, follow_redirects=True)
    download.raise_for_status()
    logger.debug("bucket download: %s (%d bytes)", path, len(download.content))
    return download.content


@retry(**_RETRY)
def upload_bytes(
    bucket_id: int,
    folder_id: int,
    path: str,
    content: bytes,
    mime_type: str = "application/octet-stream",
) -> None:
    resp = httpx.get(
        _orchestrator(f"odata/Buckets({bucket_id})/UiPath.Server.Configuration.OData.GetWriteUri"),
        params={"path": path, "contentType": mime_type},
        headers=_headers(folder_id),
        timeout=30,
    )
    resp.raise_for_status()
    write_uri: str = resp.json()["Uri"]
    upload = httpx.put(
        write_uri,
        content=content,
        headers={"Content-Type": mime_type, "x-ms-blob-type": "BlockBlob"},
        timeout=1800,
    )
    upload.raise_for_status()
    logger.debug("bucket upload: %s (%d bytes)", path, len(content))


@retry(**_RETRY)
def check_file_exists(bucket_id: int, folder_id: int, path: str) -> bool:
    try:
        resp = httpx.get(
            _orchestrator(
                f"odata/Buckets({bucket_id})/UiPath.Server.Configuration.OData.GetReadUri"
            ),
            params={"path": path},
            headers=_headers(folder_id),
            timeout=15,
        )
        return resp.status_code == 200
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return False
        raise
