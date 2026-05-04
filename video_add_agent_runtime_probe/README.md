# video-add-agent runtime probe

A **throwaway** probe agent that confirms the UiPath `uipath-langgraph`
runtime can host the binary dependencies the real `video-add-agent`
needs:

- `ffmpeg` (CLI on PATH)
- `whisper.load_model("base")` (downloads ~140 MB model weights)

Once a single run completes successfully on the staging tenant, this
folder can be deleted — it has no other purpose.

## What it does

1. Calls `ffmpeg -version` and `ffmpeg -h encoder=libx264` via
   `subprocess.run`.
2. Calls `whisper.load_model("base")` and times the load.
3. Captures Python version, platform string, presence of UiPath env
   vars, and the first few PATH entries.
4. Writes a JSON report to bucket at
   `probe/runtime-report-{label}-{ts}.json`.
5. Returns a summary dict (`success`, `ffmpegOk`, `whisperOk`,
   `reportPath`).

## Input schema

```json
{
  "bucketContext": { "bucketId": 3733, "folderId": 10934 },
  "label": "first-probe"
}
```

`label` is optional; defaults to `default`. It's surfaced in the report
filename so multiple probe runs don't clobber each other.

## How to interpret the result

| Outcome | Meaning | Next step |
|---|---|---|
| `success: true`, both `ffmpegOk` and `whisperOk` true | Phase 3 viable as designed | Proceed to publish the real `video-add-agent` |
| `ffmpegOk: false` | Sandbox doesn't include ffmpeg | Escalate: ask UiPath whether a custom-runtime / pre-installed-binary path exists. Phase 3 may need a different runtime. |
| `whisperOk: false` with download / timeout error | Sandbox has no egress for model weights | Pre-bake `~/.cache/whisper/base.pt` (~140 MB) into the deployment artifact, or switch to UiPath-hosted speech-to-text |
| `whisperOk: false` with import error | Whisper deps didn't install correctly | Check `pyproject.toml` deps were honoured by the runtime |

## Publishing

Same `uipath publish` command (or whatever your tenant uses) the real
agent will use, pointed at this folder. Run it once, fetch the report,
make the call, then delete this folder.
