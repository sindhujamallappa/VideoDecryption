# VideoDecryption — `video-add-agent` standalone repo

UiPath LangGraph coded agent that turns a screen-recording walkthrough into an Agent Design Document (ADD) for the Agentify Coded App. Published to UiPath Orchestrator as a `uipath-langgraph` process; the senior's TS app dispatches it directly via `Processes.start`.

## Layout

| Path | Purpose |
|---|---|
| [video_add_agent/](video_add_agent/) | The agent itself — publishable to UiPath Orchestrator. Pipeline: Whisper → ffmpeg → pHash dedup → AI Fabric LLM → markdown validation. |
| [video_add_agent/PUBLISH.md](video_add_agent/PUBLISH.md) | Hand-off doc for the senior — order of operations, declared in/out arguments, env vars, smoke tests, failure modes. |
| [video_add_agent_runtime_probe/](video_add_agent_runtime_probe/) | Throwaway smoke-test agent. Runs `ffmpeg -version` + `whisper.load_model('base')` and writes a JSON report to bucket. Senior publishes this **first** to confirm the runtime sandbox can host our binary deps before the real agent is published. Delete after the probe report is captured. |
| [video_add_agent/scripts/poll_daemon.py](video_add_agent/scripts/poll_daemon.py) | Polling daemon — kept as a documented fallback for when Orchestrator is unavailable or you're iterating locally. **Not** the production path. See [video_add_agent/scripts/poll_daemon.md](video_add_agent/scripts/poll_daemon.md). |

## Integration with the senior's app

The senior's TS Coded App at https://github.com/keyuluipath/AgentifyProfessionalServicesCodedApp (his branch) handles BRD/PDF/transcript ADDs in-browser, **video ADDs are dispatched here**. His `firstStageKickoff` calls `Processes.start({processName: 'video-add-agent', inputArguments: ...}, folderId)` for any project where `inputType='video'`. The published process runs this agent end-to-end and updates the AgentifyStage row to `awaiting_approval` — the senior's existing 4-second poll surfaces the result in his review screen with no UI changes.

## Quick start (development)

```sh
cd video_add_agent
python -m venv .venv
.venv/Scripts/pip install -e .
cp .env.example .env
# edit .env: UIPATH_URL + UIPATH_ACCESS_TOKEN
.venv/Scripts/pytest.exe tests/ -q
```

`ffmpeg` must be on PATH.

## Publishing

See [video_add_agent/PUBLISH.md](video_add_agent/PUBLISH.md). Short version: publish the runtime-probe first, confirm ffmpeg + Whisper work in the sandbox, then `uipath publish` from `video_add_agent/`.

## Trust boundary

All UiPath data stays in UiPath cloud. When deployed to Orchestrator, video and audio bytes never transit through a developer machine. The fallback daemon is the only path with local-machine transit (Whisper + ffmpeg run locally), and it's only used when explicitly invoked.
