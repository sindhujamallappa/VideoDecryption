"""Tunable thresholds for the multi-mode filter_transcript.

Centralised here so they can be adjusted without re-publishing the agent
package — point a tenant asset or env var at one of these and override.

Mode A = walkthrough relevance (UI actions: clicking, typing, navigating)
Mode B = discussion relevance (process talk: SIPOC, "the way it works",
         "first then", supplier/input/output/customer)
Mode C = fallback (return full transcript unfiltered)

The single LLM scorer per window emits BOTH scores (walkthrough_score,
discussion_score). The window's effective score is `max(A, B)` so a
mixed-mode video (half demo, half SME explanation) keeps both kinds of
content. A window passes when its effective score ≥ KEEP_FLOOR.
"""
from __future__ import annotations

import os


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Window length in seconds. Longer windows give more context per call but
# blur boundaries on dense walkthroughs.
WINDOW_SECONDS: float = _env_float("FILTER_WINDOW_SECONDS", 20.0)

# Maximum windows scored per LLM call. 30 windows ≈ 10 min audio with a
# 20s window — fits a single Claude Opus call comfortably.
BATCH_SIZE: int = int(_env_float("FILTER_BATCH_SIZE", 30))

# Per-window keep threshold on max(walkthrough_score, discussion_score).
# 0.4 chosen lower than the original 0.5 so the discussion-dominated PLDT
# transcripts don't get culled when the LLM scores them in the 0.4–0.6 range.
KEEP_FLOOR: float = _env_float("FILTER_KEEP_FLOOR", 0.4)

# Floor below which Mode C kicks in (pass full transcript with
# `transcript_mode="unfiltered"`). 0.10 = "if we kept less than 10% of
# audio, the filter is the bug — better to ship the unfiltered transcript
# and let downstream nodes carry the uncertainty forward."
FALLBACK_RETENTION_FLOOR: float = _env_float("FILTER_FALLBACK_RETENTION_FLOOR", 0.10)

# Mode dominance threshold for the `transcript_mode` metadata: we look at
# the average of (walkthrough_score) vs (discussion_score) across kept
# windows. Whichever is larger by ≥ this margin sets the mode label;
# otherwise the mode is "combined". 0.05 keeps the noise floor narrow.
MODE_DOMINANCE_MARGIN: float = _env_float("FILTER_MODE_DOMINANCE_MARGIN", 0.05)
