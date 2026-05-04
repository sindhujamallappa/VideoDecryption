# video-add-agent

A Python LangGraph agent that processes **video walkthroughs** into Agent Design Documents (ADDs) for the Agentify Coded App. Lives as a standalone repo so it can be published to UiPath Orchestrator as a `uipath-langgraph` process; the senior's TS app dispatches it directly.

The senior's TS app (https://github.com/keyuluipath/AgentifyProfessionalServicesCodedApp) handles BRD/PDF/transcript inputs in-browser. **Video inputs are handled here.**

**Production dispatch (Phase 3):** the senior's TS app dispatches the published `video-add-agent` Orchestrator process directly via `Processes.start` (see his repo's `src/uipath/processDispatch.ts`). See [PUBLISH.md](PUBLISH.md) for how this agent is published.

**Fallback:** the polling daemon at [scripts/poll_daemon.py](scripts/poll_daemon.py) is kept in this repo as a dev / Orchestrator-down fallback. You shouldn't need to run it for normal operation.

## Why a separate Python project?

- **Whisper** (local audio transcription), **ffmpeg** (frame extraction), and **imagehash** (perceptual-hash dedup) all rely on the Python ecosystem. Porting them to TypeScript would be a multi-week rewrite for no functional gain.
- The senior's TS app is browser-only and can't run Python directly. Publishing this agent to UiPath as a process gives the TS app a clean dispatch target.
- All data stays inside UiPath: bucket reads/writes, Data Fabric entity updates, and LLM calls all go to the same UiPath cloud the senior's app uses.

## Pipeline (11 LangGraph nodes)

```
validate_artifact
      ↓
transcribe_raw                    ← local Whisper
      ├──────────────────────────────┐
      ▼                              ▼
filter_transcript          screenshot_pipeline   ← ffmpeg dump + pHash gate
      │                              │              + cluster + dedup + bucket upload
      └────────────┬─────────────────┘
                   ▼
              align_steps  ◄────────┐
                   ▼                 │ (gaps & retries left)
            coverage_check ──────────┘
                   ▼
          generate_sections  ◄──────┐
                   ▼                 │ (validation_errors & retries left)
           validate_output ──────────┘
                   ▼
           persist_output            ← content.json + content.md + stage update
                   ▼
                  END

Any node setting state.error → handle_error → END
```

All LLM calls go through **UiPath AI Fabric LLM Gateway** (Claude Opus 4.7 by default). No direct OpenAI/Anthropic API keys.

## How the senior's TS app dispatches video jobs

When a user uploads a video via the senior's app's upload form:

1. The senior's TS app creates `AgentifyProject` (`inputType='video'`), `AgentifyArtifact` (`kind='video'`), and `AgentifyStage` (`kind='add'`, `status='running'`) rows in Data Fabric.
2. The senior's `firstStageKickoff` (in his TS app's `src/uipath/dataAdapter.ts`) detects `inputType='video'`, **skips** the in-browser `runStage`, and instead calls `dispatchVideoAgent` (his `src/uipath/processDispatch.ts`) which kicks off the published `video-add-agent` Orchestrator process via `Processes.start`. The stage row is left at `status='running'` for the Orchestrator job to pick up.
3. The published agent (this codebase, deployed to UiPath via `uipath publish` — see [PUBLISH.md](PUBLISH.md)) processes the video: Whisper → filter → screenshots → LLM → validate. It writes `content.json` + `content.md` to the bucket, uploads ~16 screenshots to `/projects/{projectId}/raw/image-{N}/screenshot-{NN}.jpg`, and updates the row to `status='awaiting_approval'`.
4. The senior's review screen's existing 4-second poll picks up the row transition and renders content from the bucket.

**Fallback path:** when the daemon is run instead (Orchestrator unavailable or local development), it polls `AgentifyStage` every 10s for `running` rows with a video artifact and runs `run_video_agent(input_dict)` in-process. Identical downstream effect.

## Setup (first-time only)

```sh
cd video_add_agent
python -m venv .venv
.venv/Scripts/pip install -e .
cp .env.example .env
# edit .env: set UIPATH_URL and UIPATH_ACCESS_TOKEN to match the senior's tenant
```

`ffmpeg` must be on PATH. On Windows: `winget install Gyan.FFmpeg` or download from https://ffmpeg.org and add to PATH.

## Running the daemon (fallback path only)

For normal production use the senior's TS app dispatches the published Orchestrator process — you should not need to run the daemon. Run it only when Orchestrator is unavailable or you're iterating on the agent locally without re-publishing each time.

```sh
cd video_add_agent
.venv/Scripts/python.exe scripts/poll_daemon.py
```

Leave the terminal open. The daemon stays alive until `Ctrl+C`. See [scripts/poll_daemon.md](scripts/poll_daemon.md) for env vars, single-instance constraint, log lines, and troubleshooting.

## One-shot manual run (for development)

```sh
.venv/Scripts/python.exe scripts/setup_run.py     # uploads a local test video + writes a fixture
.venv/Scripts/python.exe scripts/run_local.py     # runs the agent against the live tenant
.venv/Scripts/python.exe scripts/run_local_dry.py # runs the agent end-to-end with bucket + entities mocked
```

## Tests

```sh
.venv/Scripts/pytest.exe tests/ -q
```

66 tests covering every node, routing function, template parsing, and stitching logic.

## Trust boundary

| Data | Stays in | Transits through user's machine? |
|---|---|---|
| Video bytes | UiPath bucket | yes — briefly, while ffmpeg + Whisper run |
| Audio extracted from video | local temp file → deleted | yes — briefly |
| Transcript, frames, screenshots, ADD content | UiPath bucket | no — generated locally then uploaded |
| LLM calls | UiPath AI Fabric Gateway | no — never to OpenAI/Anthropic directly |
| Entity updates | UiPath Data Fabric | no |

**Phase 3 (deployed path)**: this agent is published to UiPath's `uipath-langgraph` runtime via `uipath publish` (see [PUBLISH.md](PUBLISH.md)) and dispatched from the senior's TS app. Video and audio bytes stay in UiPath cloud — no laptop transit, no daemon process. The fallback daemon's local-machine transit only applies when the daemon is run.
