# `poll_daemon.py` — operations guide

A long-running Python script that polls the senior's Agentify Coded App tenant for new video ADD jobs and runs `video_add_agent` against them. Lets the senior's app trigger us from the UI without any code changes on the senior's side.

## How it works

1. Polls `AgentifyStage` entity every `POLL_INTERVAL_S` (default 10s) via REST.
2. For each row where `kind='add'` AND `status='running'`:
   - Looks up `AgentifyArtifact` rows for that project; selects the one with `kind='video'` or `mimeType` starting with `video/`.
   - Skips the row if no video artifact found — the senior's in-browser `add` stage handles non-video jobs (BRD/SOP/transcript).
   - Loads the active `AgentifyTemplate` for `kind='add'` (`isActive=true AND status='active'`); downloads `definition.json` from its bucket path. Same lookup the senior's app uses.
   - Builds an `AgentInput`-shaped dict and calls `run_video_agent(input_dict)`.
3. The agent itself updates the row to `awaiting_approval` (success) or `rejected` (failure).
4. Stuck-row reaper: any row in `running` for more than `STUCK_THRESHOLD_S` (default 30 min) without daemon pickup is flipped to `rejected` with an explanatory `errorDesc`. Stops the senior's UI from spinning forever if the daemon was offline at upload time.

## Trust boundary

Same as `scripts/run_local.py`. All UiPath data stays in UiPath cloud — only video/audio bytes transit briefly through the user's machine for Whisper + ffmpeg processing. No new external endpoint, no third-party API.

## How to start

From the workspace root:

```
video_add_agent/.venv/Scripts/python.exe video_add_agent/scripts/poll_daemon.py
```

Leave the terminal open. The daemon stays alive until you press `Ctrl+C` or close the terminal.

## Environment variables

Read from `video_add_agent/.env` automatically:

| Var | Required | Default | Purpose |
|---|---|---|---|
| `UIPATH_URL` | yes | — | Tenant URL, e.g. `https://staging.uipath.com/ps_india/ProfServ` |
| `UIPATH_ACCESS_TOKEN` | yes | — | PAT with bucket + entity write scopes |
| `BUCKET_ID` | no | `3733` | UiPath Storage Bucket ID |
| `FOLDER_ID` | no | `10934` | UiPath Folder/OU ID |
| `POLL_INTERVAL_S` | no | `10` | Seconds between polls |
| `STUCK_THRESHOLD_S` | no | `1800` | Seconds before a stuck row is reaped |
| `DAEMON_LOCK` | no | `~/.video_add_agent_daemon.lock` | Single-instance lockfile path |

For development you can override individual values inline:

```
POLL_INTERVAL_S=3 video_add_agent/.venv/Scripts/python.exe video_add_agent/scripts/poll_daemon.py
```

## Single-instance constraint

The daemon writes its PID to `~/.video_add_agent_daemon.lock` on startup and refuses to start if the lockfile already exists with a live PID. **Do not run multiple daemons against the same tenant** — they'll race on the same `running` rows.

If the daemon was killed without cleanup (e.g., closed terminal forcefully) the lockfile may persist. Symptoms: next start fails with `daemon already running (pid=...)`. Resolution: verify no python process with that PID is alive (Task Manager / `ps`), then delete the lockfile manually:

```
rm ~/.video_add_agent_daemon.lock        # bash / git-bash
del %USERPROFILE%\.video_add_agent_daemon.lock   # cmd
```

## Logs

Stdout. Format: `YYYY-MM-DD HH:MM:SS,sss [LEVEL] logger_name: message`. Pipe to a file or tee:

```
video_add_agent/.venv/Scripts/python.exe video_add_agent/scripts/poll_daemon.py 2>&1 | tee /tmp/daemon.log
```

Key log lines to watch for:

| Line | Meaning |
|---|---|
| `poll_daemon starting: poll_interval=...` | Daemon up |
| `found N running ADD stage(s)` | Pickup — at least one row is being looked at this cycle |
| `processing stage {id} (project {id})` | Daemon picked up a row |
| `stage {id} has no video artifact, skipping` | Non-video job — senior's in-browser add stage owns it, daemon ignores |
| `stage {id} -> awaiting_approval (success)` | Agent finished successfully |
| `stage {id} -> rejected: ...` | Agent failed; reason in errorDesc |
| `reaped stuck row {id} after {n}s` | Stuck-row reaper kicked in |
| `poll loop error: ...` | Transient error inside the polling loop; daemon continues to next cycle |

## End-to-end test (recommended after first start)

1. Start the daemon in a terminal.
2. Open the senior's app at `https://ps_india.staging.uipath.host/agentifydelivery/`, create a project, upload a video walkthrough (`.mp4`/`.m4a`/`.wav`/`.mp3`/`.webm`), click **Generate ADD**.
3. Within `POLL_INTERVAL_S` the daemon should log:
   ```
   found 1 running ADD stage(s)
   processing stage <id> (project <id>)
   ```
4. The agent's pipeline logs follow (transcribe_raw → filter_transcript → screenshot_pipeline → align_steps → coverage_check → generate_sections → validate_output → persist_output).
5. Total wall-clock: 3–5 min for a 5–10 min video. End log: `stage <id> -> awaiting_approval (success)`.
6. Refresh the senior's app's review screen — the ADD should render with substantive content under template `block_N` keys.

## Stopping the daemon

Press `Ctrl+C` in the terminal. The daemon catches `SIGINT`/`SIGTERM`, releases its lockfile, and exits. If the terminal closes unexpectedly the lockfile may persist — see the single-instance section above.

## What the daemon does NOT do

- It does NOT transcode, summarise, or modify videos beyond what the agent already does.
- It does NOT modify projects, templates, or any non-`AgentifyStage` entity rows except the in-flight stage row it's processing.
- It does NOT process non-video jobs (those flow through the senior's in-browser `add` stage as before).
- It does NOT auto-restart the senior's UI or trigger downstream stages — once the row is `awaiting_approval`, the senior's app's existing approval flow takes over.

## Future direction (Phase 3, deferred)

Long-term we'd deploy `video_add_agent` as a UiPath Orchestrator process (it's already shaped for this — see `langgraph.json` + `plugin_manifest.json`). The senior's app would dispatch via the `@uipath/uipath-typescript` SDK and the daemon would no longer be needed. For now the daemon is the simplest path to "trigger from UI" without modifying the senior's code.
