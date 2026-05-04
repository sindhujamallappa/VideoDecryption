# Publishing `video-add-agent` to UiPath Orchestrator

This is the hand-off doc for the senior (or whoever holds tenant publish scopes). The agent is publication-shaped — `langgraph.json`, `plugin_manifest.json`, and `pyproject.toml` are all in place. What's left is running `uipath publish` from a machine that has admin scopes on the staging tenant.

The TS app's dispatch is already wired in the senior's repo (`src/uipath/processDispatch.ts` at https://github.com/keyuluipath/AgentifyProfessionalServicesCodedApp): it calls `Processes.start({processName: 'video-add-agent', inputArguments: ...}, folderId)`. Once the published process exists with that name on the tenant, video uploads in the senior's UI will hit it directly.

## Order of operations

1. **Smoke-test first.** Publish [../video_add_agent_runtime_probe/](../video_add_agent_runtime_probe/) before this one. It's a tiny agent (one file ~200 lines) that just runs `ffmpeg -version` and `whisper.load_model('base')` and writes a JSON report to bucket. If ffmpeg or Whisper aren't available in the `uipath-langgraph` runtime sandbox, we want to know that before spending effort on the real publish. See [../video_add_agent_runtime_probe/README.md](../video_add_agent_runtime_probe/README.md).

2. **Publish this agent.** Once the probe passes, publish from this folder.

3. **Smoke test from the senior's UI.** Upload a video, watch the browser console for the dispatch log line, watch Orchestrator for the job, watch the AgentifyStage row flip.

## Publish command

From `video_add_agent/`:

```sh
# Whatever the tenant uses — typically the uipath CLI shipped with uipath-langchain.
# The CLI reads pyproject.toml + langgraph.json + plugin_manifest.json from cwd.
uipath publish
```

The CLI should:
- Build a wheel (or equivalent artifact) from `pyproject.toml`
- Bundle the `langgraph.json` graph reference
- Bundle the `plugin_manifest.json` (input/output schema, runtime, entrypoint)
- Upload to the tenant
- Register a process named `video-add-agent` at version `1.0.0`

If the CLI prompts for tenant URL / credentials, use the same staging tenant the senior's app deploys to.

## Expected published process

| Field | Value |
|---|---|
| Process name | `video-add-agent` |
| Version | `1.0.0` |
| Runtime | `uipath-langgraph` |
| Entrypoint | `video_add_agent.entry:run_video_agent` |

### Input arguments (`inputSchema` in [plugin_manifest.json](plugin_manifest.json))

8 required top-level fields. The senior's TS app already builds this exact payload in `processDispatch.ts` and passes it to `Processes.start` via `inputArguments: JSON.stringify(input)`.

| Field | Type | Notes |
|---|---|---|
| `projectId` | string | AgentifyProject row Id |
| `stageId` | string | AgentifyStage row Id (kind='add', status='running') |
| `projectName` | string | Falls back to projectId if missing |
| `inputType` | string (enum: `"video"`) | Discriminator |
| `sourceFilename` | string | Basename of the artifact bucketPath |
| `videoArtifact` | object | `{id, kind, bucketPath, mimeType, sizeBytes}` |
| `bucketContext` | object | `{bucketId, folderId}` — passed at dispatch, not env |
| `activeAddTemplate` | object | `{templateId, promptVersion, definition}` — `definition` is JSON string of TemplateDefinition |

### Output arguments (`outputSchema` in [plugin_manifest.json](plugin_manifest.json))

3 required + 5 optional fields. Returned by `run_video_agent` as a dict; the senior's TS layer reads these from the job's `outputArguments` if needed (the agent itself has already updated AgentifyStage + bucket files by this point, so the return is mostly informational).

| Field | Type | Required | Notes |
|---|---|---|---|
| `success` | boolean | yes | true on full pipeline success |
| `projectId` | string | yes | echoed from input |
| `stageId` | string | yes | echoed from input |
| `contentJsonPath` | string | no | Bucket path of generated content.json |
| `contentMdPath` | string | no | Bucket path of generated content.md |
| `screenshotCount` | integer | no | Number of screenshots uploaded |
| `transcriptPath` | string | no | Bucket path of transcript.txt |
| `summary` | string | no | Free-form summary surfaced in logs |
| `error` | string | no | Populated on failure (success=false) |

If you publish under a different name (e.g. `video-add-agent-staging`), set `VITE_VIDEO_AGENT_PROCESS_NAME` in the senior's TS env so his `processDispatch.ts` picks it up.

## Runtime env vars the agent expects

The agent reads these at module-import time:

| Var | Source | Purpose |
|---|---|---|
| `UIPATH_URL` | UiPath runtime injects | Tenant base URL — used for bucket REST calls |
| `UIPATH_ACCESS_TOKEN` | UiPath runtime injects | Bearer token for bucket reads/writes + entity updates |
| `UIPATH_FOLDER_ID` | optional, defaults to `10934` in [video_add_agent/utils/llm.py](video_add_agent/utils/llm.py) | OU/folder for LLM Gateway calls |

By convention the `uipath-langgraph` runtime supplies the first two. If they aren't set the agent fails fast at import — easy to spot in logs.

The bucket id and folder id used during processing come from the **input dict** (`bucketContext`), not env. The senior's TS app passes them at dispatch time.

## Post-publish smoke test (no UI)

Test the published process directly before relying on the UI dispatch path. Pass a known-good input dict:

```sh
# From a machine with the uipath CLI logged into the same tenant
uipath jobs start --process video-add-agent --folder-id 10934 \
  --input @scripts/test_payload.json
```

Use a payload pointing at a project + stage + video artifact you've previously processed successfully via the daemon (re-uploading is fine — the agent overwrites bucket files). Watch the AgentifyStage row in the senior's UI; it should flip to `awaiting_approval` after 5–10 minutes.

If `test_payload.json` doesn't already exist, run `scripts/setup_run.py` against a fresh project — it generates `tests/fixtures/sample_input.json` which is the same payload shape the dispatch path uses.

## Smoke test from the senior's UI

Once the standalone smoke test passes:

1. Open the senior's app in a browser (staging URL).
2. Create a new project with a video artifact (`.mp4` / `.m4a` / `.wav` / `.mp3` / `.webm`).
3. Click Generate ADD.
4. Browser console should log: `[firstStageKickoff] Dispatched video-add-agent for video project <projectId>; jobKey=<guid>.`
5. Watch Orchestrator's Jobs view — the job should be `Running`, then `Successful`.
6. Senior's UI's existing 4-second poll should pick up the AgentifyStage row's `awaiting_approval` transition and render the review screen with sections + screenshots.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `[firstStageKickoff] dispatchVideoAgent failed: Processes.start returned no jobs` | Process name mismatch — TS expects `video-add-agent`, tenant has it under a different name | Set `VITE_VIDEO_AGENT_PROCESS_NAME` in senior's TS env, or republish under the expected name |
| `dispatchVideoAgent failed: ... 401 Unauthorized` | Senior's UI auth token doesn't have `Processes.Start` scope | Senior grants the scope on the OAuth client / PAT used by the SPA |
| Orchestrator job runs, fails immediately with `KeyError: 'UIPATH_URL'` | Runtime env vars not injected | Confirm the runtime sets these for `uipath-langgraph` packages — check tenant docs |
| Orchestrator job hangs / OOM after model load | Whisper model weights download or sandbox memory cap | Probe agent should have caught this. If it slipped through, reduce Whisper model size or pre-bake weights |

## Decommissioning the daemon

Per the Phase 3 plan, the daemon stays in the repo as a documented fallback. **Do not** delete `scripts/poll_daemon.py` or `scripts/poll_daemon.md`. After a stable Phase 3 deployment, the daemon's `poll_daemon.md` already labels it as fallback-only (see header on that file).

## Republishing on changes

Any agent code change → bump `[project] version` in [pyproject.toml](pyproject.toml) → re-run `uipath publish`. The senior's TS code is unaffected unless the input schema in [plugin_manifest.json](plugin_manifest.json) changes (which would be a coordinated change across both codebases).
