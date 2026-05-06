"""Node — score_quality (Fix 5)

Runs after `validate_output` and before `persist_output`. Classifies each
generated section block as grounded / placeholder / suspected_hallucination
based on whether its content can be traced to the transcript or to the
vision-classified screenshots, and emits an overall confidence score.

The result is stored on `state.quality_report` and consumed by
`persist_output` to (a) prepend an "ADD Generation Summary" to content.md
and (b) attach a recommendation flag the senior's review UI can render
as a banner.

Heuristics (intentionally simple — better signal than nothing, not a
full hallucination detector):
- "grounded": block contains specific content (proper nouns, numbers,
  capitalised app names) AND those tokens appear in the transcript or
  in vision-analysis app_name fields.
- "placeholder": block is empty, or every sentence is a Gap marker.
- "suspected_hallucination": block contains a token that appears in
  `state.project_name` (case-insensitive) but does NOT appear in the
  raw transcript or in any vision app_name. This is the exact pattern
  that produced the "UiBank" hallucination in the PLDT SIPOC failure.

`recommendation` is the actionable signal the senior's UI should map
to a colored banner:
  - "acceptable"           when overall_score ≥ 0.4
  - "needs_review"         when 0.15 ≤ overall_score < 0.4
  - "insufficient_grounding" when overall_score < 0.15 (or when the
    upstream insufficient_grounding flag is True)
"""
from __future__ import annotations

import logging
import re
from typing import Any

from video_add_agent.state import VideoAgentState
from video_add_agent.utils.markdown import count_citation_density

logger = logging.getLogger(__name__)

_PROPER_NOUN_RE = re.compile(r"\b[A-Z][A-Za-z0-9]{2,}\b")
_GAP_MARKER_RE = re.compile(r"\bTo be confirmed with SME\b", re.IGNORECASE)


def _normalize_word(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", token.lower())


def _build_transcript_corpus(state: VideoAgentState) -> set[str]:
    """All distinct lowercase alphanum tokens from the raw transcript.

    We use raw_transcript (not filtered_transcript) so a token mentioned
    once in the source — even in a window the filter dropped — still
    counts as grounded. Better to err toward "grounded" than against.
    """
    corpus: set[str] = set()
    for seg in state.raw_transcript:
        for tok in seg.text.split():
            normed = _normalize_word(tok)
            if len(normed) >= 3:
                corpus.add(normed)
    return corpus


def _build_vision_corpus(state: VideoAgentState) -> set[str]:
    """Tokens from vision app_name + ui_state across all screenshots."""
    corpus: set[str] = set()
    for s in state.screenshots:
        if not s.vision_analysis:
            continue
        for field in ("app_name", "ui_state", "visible_data"):
            value = s.vision_analysis.get(field)
            if not isinstance(value, str):
                continue
            for tok in value.split():
                normed = _normalize_word(tok)
                if len(normed) >= 3:
                    corpus.add(normed)
    return corpus


def _project_name_tokens(project_name: str) -> set[str]:
    """Tokens from the projectName, used as a hallucination-source filter."""
    parts = re.split(r"[\s_\-./]+", project_name)
    out: set[str] = set()
    for part in parts:
        normed = _normalize_word(part)
        if len(normed) >= 3 and not normed.isdigit():
            out.add(normed)
    return out


def _proper_nouns_in(text: str) -> list[str]:
    return [
        _normalize_word(m.group(0))
        for m in _PROPER_NOUN_RE.finditer(text)
        if len(_normalize_word(m.group(0))) >= 3
    ]


def _classify_block(
    key: str,
    body: str,
    transcript_tokens: set[str],
    vision_tokens: set[str],
    project_name_tokens: set[str],
) -> tuple[str, str | None]:
    """Return (classification, evidence_or_none)."""
    if not body or not body.strip():
        return "placeholder", None
    # If every non-whitespace line is a gap, treat as placeholder.
    substantive_lines = [
        ln for ln in body.splitlines()
        if ln.strip() and not _GAP_MARKER_RE.search(ln)
    ]
    if not substantive_lines:
        return "placeholder", None

    nouns = set(_proper_nouns_in(body))
    if not nouns:
        # No proper nouns — if it has citations, count as grounded; otherwise
        # weakly grounded (placeholder-ish).
        density = count_citation_density(body)
        if density >= 0.3:
            return "grounded", None
        return "placeholder", None

    # Hallucination heuristic: any noun that appears in projectName but
    # NOT in transcript or vision corpus is suspect.
    suspects: list[str] = []
    grounded_count = 0
    for noun in nouns:
        in_transcript = noun in transcript_tokens
        in_vision = noun in vision_tokens
        in_project = noun in project_name_tokens
        if in_project and not in_transcript and not in_vision:
            suspects.append(noun)
        elif in_transcript or in_vision:
            grounded_count += 1

    if suspects:
        evidence = (
            "contains "
            + ", ".join(f"'{s}'" for s in suspects[:3])
            + " which appear in projectName but not in transcript or vision analysis"
        )
        return "suspected_hallucination", evidence

    if grounded_count >= 1:
        return "grounded", None

    # Has proper nouns but none traceable — weak signal, lean placeholder.
    return "placeholder", None


def _summarise_screenshot_coverage(state: VideoAgentState) -> dict[str, int]:
    counts: dict[str, int] = {
        "total": len(state.screenshots),
        "actually_analyzed_with_vision": 0,
        "screen_recordings_detected": 0,
        "meeting_frames_detected": 0,
        "presentation_slides_detected": 0,
    }
    for s in state.screenshots:
        if not s.vision_analysis:
            continue
        counts["actually_analyzed_with_vision"] += 1
        cls = s.vision_analysis.get("classification")
        if cls == "screen_recording":
            counts["screen_recordings_detected"] += 1
        elif cls == "meeting_frame":
            counts["meeting_frames_detected"] += 1
        elif cls == "presentation_slide":
            counts["presentation_slides_detected"] += 1
    return counts


def _recommendation(score: float, insufficient: bool) -> str:
    if insufficient or score < 0.15:
        return "insufficient_grounding"
    if score < 0.4:
        return "needs_review"
    return "acceptable"


def score_quality_node(state: VideoAgentState) -> dict[str, Any]:
    logger.info("score_quality: start project_id=%s", state.project_id)
    sections = state.sections or {}
    if not sections:
        # Nothing to score — emit an empty report at recommendation
        # 'insufficient_grounding' so persist_output flags it.
        report = {
            "overall_score": 0.0,
            "grounded_blocks": [],
            "placeholder_blocks": [],
            "suspected_hallucination_blocks": [],
            "hallucination_evidence": {},
            "recommendation": "insufficient_grounding",
            "transcript_coverage": {
                "mode": state.transcript_mode,
                "retention_pct": state.transcript_retention_pct,
                "total_words": 0,
            },
            "screenshot_coverage": _summarise_screenshot_coverage(state),
        }
        return {"quality_report": report}

    transcript_tokens = _build_transcript_corpus(state)
    vision_tokens = _build_vision_corpus(state)
    project_tokens = _project_name_tokens(state.project_name)
    total_words = sum(len(seg.text.split()) for seg in state.raw_transcript)

    grounded: list[str] = []
    placeholders: list[str] = []
    hallucinations: list[str] = []
    evidence: dict[str, str] = {}

    for key, body in sections.items():
        classification, ev = _classify_block(
            key, body, transcript_tokens, vision_tokens, project_tokens
        )
        if classification == "grounded":
            grounded.append(key)
        elif classification == "placeholder":
            placeholders.append(key)
        else:  # suspected_hallucination
            hallucinations.append(key)
            if ev:
                evidence[key] = ev

    total_blocks = max(1, len(sections))
    overall_score = len(grounded) / total_blocks
    recommendation = _recommendation(overall_score, state.insufficient_grounding)

    report = {
        "overall_score": round(overall_score, 3),
        "grounded_blocks": grounded,
        "placeholder_blocks": placeholders,
        "suspected_hallucination_blocks": hallucinations,
        "hallucination_evidence": evidence,
        "recommendation": recommendation,
        "transcript_coverage": {
            "mode": state.transcript_mode,
            "retention_pct": round(state.transcript_retention_pct, 3),
            "total_words": total_words,
        },
        "screenshot_coverage": _summarise_screenshot_coverage(state),
    }
    logger.info(
        "score_quality: %d grounded, %d placeholder, %d hallucination — overall=%.2f rec=%s",
        len(grounded), len(placeholders), len(hallucinations),
        overall_score, recommendation,
    )
    return {"quality_report": report}
