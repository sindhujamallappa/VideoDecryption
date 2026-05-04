"""Local Mode-2 runner: load .env, load sample_input.json, call run_video_agent.

Usage (from repo root):
    video_add_agent/.venv/Scripts/python.exe video_add_agent/scripts/run_local.py

Requires:
    - .env populated with UIPATH_URL and UIPATH_ACCESS_TOKEN
    - tests/fixtures/sample_input.json populated with real bucket + stage values
    - ffmpeg on PATH
    - The video at videoArtifact.bucketPath already uploaded to your bucket
    - The stage row at stageId already existing in Data Fabric
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    here = Path(__file__).resolve().parent
    project_root = here.parent

    env_file = project_root / ".env"
    if not env_file.exists():
        sys.stderr.write(f"ERROR: {env_file} not found. Copy .env.example and fill it in.\n")
        return 2
    load_dotenv(env_file, override=True)

    if not os.environ.get("UIPATH_URL") or not os.environ.get("UIPATH_ACCESS_TOKEN"):
        sys.stderr.write("ERROR: UIPATH_URL and UIPATH_ACCESS_TOKEN must be set in .env\n")
        return 2

    fixture = project_root / "tests" / "fixtures" / "sample_input.json"
    if not fixture.exists():
        sys.stderr.write(f"ERROR: {fixture} not found. Create it with your real values.\n")
        return 2
    input_dict = json.loads(fixture.read_text(encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Import after env is loaded so module-level os.environ.get() picks up values.
    from video_add_agent.entry import run_video_agent

    result = run_video_agent(input_dict)
    print(json.dumps(result, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
