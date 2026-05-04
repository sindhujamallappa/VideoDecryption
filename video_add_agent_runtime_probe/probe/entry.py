"""Throwaway runtime probe — verifies the UiPath uipath-langgraph runtime
can host ffmpeg and Whisper before we spend effort publishing the real
video-add-agent.

Single entry point `run_probe(input)`:
  1. Calls `ffmpeg -version` via subprocess.
  2. Calls `whisper.load_model("base")` (this triggers the ~140 MB model
     weights download on first use).
  3. Captures Python + platform info.
  4. Writes a JSON report to `probe/runtime-report-{ts}.json` in the
     bucket so it can be fetched and inspected after the run.
  5. Returns a small summary dict.

Reads UIPATH_URL + UIPATH_ACCESS_TOKEN from env (UiPath runtime supplies
these by convention). Bucket id + folder id come from the input dict.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _bucket_upload(
    bucket_id: int,
    folder_id: int,
    path: str,
    content: bytes,
    mime_type: str = "application/json",
) -> None:
    """Mirror video_add_agent/utils/bucket.py upload_bytes — kept inline so
    the probe deploys without dragging the real agent's package along."""
    base = os.environ["UIPATH_URL"].rstrip("/")
    token = os.environ["UIPATH_ACCESS_TOKEN"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-UIPATH-OrganizationUnitId": str(folder_id),
    }
    r = httpx.get(
        f"{base}/odata/Buckets({bucket_id})/UiPath.Server.Configuration.OData.GetWriteUri",
        params={"path": path, "contentType": mime_type},
        headers=headers,
        timeout=30,
    )
    r.raise_for_status()
    write_uri = r.json()["Uri"]
    up = httpx.put(
        write_uri,
        content=content,
        headers={"Content-Type": mime_type, "x-ms-blob-type": "BlockBlob"},
        timeout=600,
    )
    up.raise_for_status()


def _check_ffmpeg() -> dict[str, Any]:
    try:
        r = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=15
        )
        return {
            "ok": r.returncode == 0,
            "returncode": r.returncode,
            "stdout": r.stdout[:2000],
            "stderr": r.stderr[:2000],
        }
    except FileNotFoundError:
        return {"ok": False, "error": "ffmpeg binary not found on PATH"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _check_libx264() -> dict[str, Any]:
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "encoder=libx264"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {
            "ok": r.returncode == 0 and "libx264" in (r.stdout + r.stderr),
            "returncode": r.returncode,
            "stdout": r.stdout[:1000],
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _check_whisper() -> dict[str, Any]:
    """Loading 'base' downloads ~140 MB of weights on first use — captures
    cold-start cost the real agent would otherwise surprise us with."""
    try:
        import whisper

        t0 = time.time()
        model = whisper.load_model("base")
        elapsed = time.time() - t0
        return {
            "ok": True,
            "load_seconds": round(elapsed, 2),
            "device": str(getattr(model, "device", "unknown")),
            "model_class": type(model).__name__,
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def run_probe(input: dict[str, Any]) -> dict[str, Any]:
    bucket_ctx = input.get("bucketContext") or {}
    bucket_id = int(bucket_ctx["bucketId"])
    folder_id = int(bucket_ctx["folderId"])
    label = (input.get("label") or "default").replace("/", "_")

    logger.info("runtime probe starting (label=%s, bucket=%d, folder=%d)", label, bucket_id, folder_id)

    ffmpeg = _check_ffmpeg()
    libx264 = _check_libx264()
    whisper_res = _check_whisper()

    report = {
        "label": label,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": sys.version,
        "platform": platform.platform(),
        "ffmpeg": ffmpeg,
        "libx264": libx264,
        "whisper": whisper_res,
        "env": {
            "UIPATH_URL_set": bool(os.environ.get("UIPATH_URL")),
            "UIPATH_ACCESS_TOKEN_set": bool(os.environ.get("UIPATH_ACCESS_TOKEN")),
            "PATH_first_4": (os.environ.get("PATH") or "").split(os.pathsep)[:4],
        },
    }

    timestamp_safe = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    report_path = f"probe/runtime-report-{label}-{timestamp_safe}.json"
    body = json.dumps(report, indent=2).encode("utf-8")

    upload_error: str | None = None
    try:
        _bucket_upload(bucket_id, folder_id, report_path, body, "application/json")
    except Exception as exc:
        upload_error = f"{type(exc).__name__}: {exc}"
        logger.error("failed to upload report: %s", upload_error)

    summary = {
        "success": ffmpeg.get("ok", False) and whisper_res.get("ok", False),
        "reportPath": report_path,
        "ffmpegOk": ffmpeg.get("ok", False),
        "whisperOk": whisper_res.get("ok", False),
    }
    if upload_error:
        summary["error"] = f"report upload failed: {upload_error}"

    logger.info(
        "runtime probe done: ffmpeg=%s whisper=%s -> %s",
        ffmpeg.get("ok"),
        whisper_res.get("ok"),
        report_path,
    )
    return summary
