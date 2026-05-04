"""Single-node StateGraph wrapping run_probe so uipath-langgraph runtime
can dispatch it. Mirrors the shape of video_add_agent/graph.py but
collapsed to one node — the probe has no pipeline.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from probe.entry import run_probe


class ProbeState(TypedDict, total=False):
    input: dict[str, Any]
    result: dict[str, Any]


def _probe_node(state: ProbeState) -> ProbeState:
    return {"result": run_probe(state.get("input") or {})}


_builder: StateGraph = StateGraph(ProbeState)
_builder.add_node("probe", _probe_node)
_builder.add_edge(START, "probe")
_builder.add_edge("probe", END)

graph = _builder.compile()
