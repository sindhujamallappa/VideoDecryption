# video-add-agent

A UiPath LangGraph coded agent that converts a video walkthrough into a fully-formed **Agent Design Document (ADD)** — ready for review and export in the Agentify Professional Services app.

## What it does

1. Downloads the video artifact from the UiPath Storage Bucket.
2. Transcribes audio with **local Whisper** (no external API key).
3. Extracts key frames and uploads them as screenshots.
4. Sends transcript + frames to a **vision-capable model via UiPath AI Fabric** (GPT-4o) to extract structured process context.
5. Generates every ADD section driven by the **active template definition** — two parallel LLM chunks via UiPath AI Fabric (Claude).
6. Validates markdown tables, Mermaid diagrams, and per-section character caps.
7. Writes `content.json` and `content.md` to the bucket and sets the stage to `awaiting_approval`.

All LLM calls go through **UiPath AI Fabric** (no direct OpenAI/Anthropic API keys). All file I/O uses **UiPath Storage Bucket APIs**.

---

## Pipeline

```
validate_artifact
      ↓
  transcribe          ← local Whisper + ffmpeg frames
      ↓
extract_context       ← GPT-4o vision via AI Fabric
      ↓
generate_sections     ← 2× parallel Claude via AI Fabric (template-driven)
      ↓
validate_output       ← Mermaid / pipe-table / char-cap enforcement
      ↓
 persist_output        ← upload content.json + content.md, update stage row
      ↓
     END

Any node failure → handle_error → stage set to rejected → END
```

---

## Project structure

```
video_add_agent/
├── plugin_manifest.json          # TypeScript interop: name, version, schemas
├── langgraph.json                # UiPath runtime entry: graph.py:graph
├── pyproject.toml
├── video_add_agent/
│   ├── entry.py                  # run_video_agent(input) -> dict
│   ├── graph.py                  # StateGraph compilation
│   ├── state.py                  # VideoAgentState TypedDict
│   ├── nodes/
│   │   ├── validate_artifact.py
│   │   ├── transcribe.py
│   │   ├── extract_context.py
│   │   ├── generate_sections.py
│   │   ├── validate_output.py
│   │   ├── persist_output.py
│   │   └── handle_error.py
│   ├── models/
│   │   ├── input.py              # AgentInput, VideoArtifact, BucketContext, ActiveAddTemplate
│   │   ├── template.py           # TemplateDefinition, TemplateBlock, BlockKind
│   │   └── output.py             # AgentOutput, AgentFailure
│   └── utils/
│       ├── bucket.py             # Orchestrator REST pre-signed URI helpers
│       ├── entities.py           # Data Fabric stage row update
│       ├── llm.py                # UiPathChat wrappers with tenacity retry
│       ├── media.py              # ffmpeg + Whisper helpers
│       ├── markdown.py           # Table/Mermaid validation, section stitching
│       └── prompt.py             # Per-block prompt builders
└── tests/
    ├── conftest.py
    ├── test_validate_artifact.py
    ├── test_transcribe.py
    ├── test_extract_context.py
    ├── test_generate_sections.py
    ├── test_validate_output.py
    ├── test_persist_output.py
    └── test_handle_error.py
```

---

## Requirements

| Requirement | Notes |
|---|---|
| Python ≥ 3.11 | |
| `ffmpeg` on PATH | Audio/frame extraction — install via `apt-get install ffmpeg` or robot dependency |
| UiPath Storage Bucket (Orchestrator) | `UIPATH_URL` + `UIPATH_ACCESS_TOKEN` env vars |
| UiPath Data Fabric | Same credentials — used for stage row updates |
| UiPath AI Fabric | GPT-4o (vision) + Claude (section generation) — billed as Agent Units, no direct API keys |

---

## Environment variables

```bash
UIPATH_URL=https://cloud.uipath.com/{org}/{tenant}
UIPATH_ACCESS_TOKEN=<robot_access_token>
```

Copy `.env.example` to `.env` and fill in your values for local development.

---

## Local development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests (all nodes mocked — no external calls)
pytest tests/ -v

# Run with a local input file
python -c "
from video_add_agent.entry import run_video_agent
import json, pathlib
result = run_video_agent(json.loads(pathlib.Path('tests/fixtures/sample_input.json').read_text()))
print(json.dumps(result, indent=2))
"
```

---

## UiPath packaging and deployment

```bash
# Initialise UiPath project metadata
uipath init

# Pack as .nupkg
uipath pack

# Publish to Orchestrator
uipath publish
```

Once deployed, the TypeScript layer invokes the agent via the Orchestrator `StartJobs` API:

```typescript
const job = await orchestratorClient.jobs.start({
  processName: "video-add-agent",
  inputArguments: JSON.stringify(agentInput),  // matches plugin_manifest.json inputSchema
});
const result = await pollJobResult(job.id);    // matches plugin_manifest.json outputSchema
```

---

## Output paths (bucket)

| File | Path |
|---|---|
| ADD sections JSON | `projects/{projectId}/stages/pdd/output/content.json` |
| ADD stitched markdown | `projects/{projectId}/stages/pdd/output/content.md` |
| Transcript | `projects/{projectId}/stages/pdd/output/transcript.txt` |
| Frames / screenshots | `projects/{projectId}/raw/image-{N}/screenshot-{NN}.jpg` |

---

## Markdown requirements enforced

- **Tables** — valid markdown pipe tables (`| col | col |` + `|---|---|` separator).
- **Process maps** — fenced ` ```mermaid\nflowchart TD ``` ` only. `flowchart LR` is automatically converted to `TD`.
- **Screenshot refs** — `![alt](image://{projectId}/screenshot-NN.jpg)` — only in keystroke/steps sections.
- **Section cap** — 2 000 characters max per section.
- **No invented data** — missing values use `> **Gap:** To be confirmed with SME`.

---

## Acceptance criteria

- Uploading a video creates a project and ADD stage in Agentify.
- The agent writes `content.json` and `content.md` to the correct bucket paths.
- The ADD stage status becomes `awaiting_approval`.
- The existing ADD review screen displays all generated sections.
- Users can edit sections and autosave normally.
- Word preview/export works using the active ADD template.
- Screenshot references render in review and export.
- ASDD, Test Cases, and Code Gen can continue after ADD approval.
