"""Tests for dedupe_screenshots node.

After Fix 4 the node also calls the vision LLM and uploads a vision.json
cache sibling per screenshot. Tests mock build_llm + call_llm_multimodal
+ the bucket cache lookup so they don't hit network.
"""
from __future__ import annotations

import json

from video_add_agent.nodes.dedupe_screenshots import dedupe_screenshots_node
from video_add_agent.state import ScreenshotCandidate


# Bypass image-based hashing — use explicit 64-bit hex hashes with known
# Hamming distances. Solid-color PIL images all collapse to the same pHash.
H_ZERO = "0000000000000000"
H_ONES = "ffffffffffffffff"


def _mock_vision(monkeypatch):
    """Mock the vision LLM machinery so dedupe doesn't hit AI Fabric."""
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.build_llm", lambda **_k: None
    )

    async def _fake_call(*_args, **_kwargs):
        return json.dumps({
            "classification": "screen_recording",
            "app_name": "TestApp",
            "ui_state": "form",
            "visible_data": "redacted",
            "likely_action": "click",
            "screen_recording_confidence": 0.95,
        })

    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.call_llm_multimodal", _fake_call
    )

    # Cache miss for every vision.json lookup so the node hits the LLM
    # path. The cache write itself is captured by the upload patch.
    def _miss(*_args, **_kwargs):
        raise RuntimeError("cache miss")

    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.download_bytes", _miss
    )


def test_uploads_one_per_cluster(sample_state, monkeypatch, tmp_path):
    frames: list[ScreenshotCandidate] = []
    for i, h in enumerate([H_ZERO, H_ZERO, H_ONES, H_ONES]):
        p = tmp_path / f"f{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xd9")
        frames.append(ScreenshotCandidate(timestamp=float(i), phash=h, frame_path=str(p)))
    sample_state.keyframe_candidates = frames

    uploads: list[dict] = []
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.upload_bytes",
        lambda **kw: uploads.append(kw),
    )
    _mock_vision(monkeypatch)

    result = dedupe_screenshots_node(sample_state)
    assert "error" not in result
    # 2 clusters → 2 screenshot uploads + 2 vision.json cache uploads.
    assert len(result["screenshots"]) == 2
    image_uploads = [u for u in uploads if "/raw/image-" in u["path"] and u["path"].endswith(".jpg")]
    vision_uploads = [u for u in uploads if u["path"].endswith("vision.json")]
    assert len(image_uploads) == 2
    assert len(vision_uploads) == 2
    # Each screenshot has the mocked vision_analysis attached.
    for s in result["screenshots"]:
        assert s.vision_analysis is not None
        assert s.vision_analysis["classification"] == "screen_recording"


def test_no_candidates_short_circuits(sample_state, monkeypatch):
    sample_state.keyframe_candidates = []
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.upload_bytes",
        lambda **_: (_ for _ in ()).throw(AssertionError("upload should not happen")),
    )
    result = dedupe_screenshots_node(sample_state)
    assert result["screenshots"] == []


def test_missing_frame_file_skips_cluster(sample_state, monkeypatch):
    sample_state.keyframe_candidates = [
        ScreenshotCandidate(timestamp=0.0, phash=H_ZERO, frame_path="/does/not/exist.jpg"),
    ]
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.upload_bytes", lambda **_: None
    )
    _mock_vision(monkeypatch)
    result = dedupe_screenshots_node(sample_state)
    assert "error" not in result
    assert result["screenshots"] == []


def test_cluster_count_capped_when_exceeds_max(sample_state, monkeypatch, tmp_path):
    """When cluster count exceeds MAX_CLUSTERS, the node downsamples
    chronologically — keeping evenly spaced representatives across the
    full timeline rather than concentrating around any one region."""
    # Pairwise-distinct pHashes (Hamming distance ≥ 8, well above
    # DEDUPE_THRESHOLD=5) so each frame becomes its own cluster.
    distinct_phashes = [
        "0000000000000000",
        "00000000000000ff",
        "000000000000ff00",
        "0000000000ff0000",
        "00000000ff000000",
        "000000ff00000000",
        "0000ff0000000000",
        "00ff000000000000",
        "ff00000000000000",
        "ffff000000000000",
    ]
    frames: list[ScreenshotCandidate] = []
    for i, h in enumerate(distinct_phashes):
        p = tmp_path / f"f{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xd9")
        frames.append(ScreenshotCandidate(timestamp=float(i), phash=h, frame_path=str(p)))
    sample_state.keyframe_candidates = frames

    # Cap at 5 — expect uniform stride of 2 across timestamps 0..9.
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.MAX_CLUSTERS", 5
    )

    uploads: list[dict] = []
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.upload_bytes",
        lambda **kw: uploads.append(kw),
    )
    _mock_vision(monkeypatch)

    result = dedupe_screenshots_node(sample_state)
    assert "error" not in result
    assert len(result["screenshots"]) == 5

    # Time-uniform: with 10 clusters and cap=5, step=2 → keep
    # timestamps at indices 0, 2, 4, 6, 8.
    kept_timestamps = sorted(s.timestamp for s in result["screenshots"])
    assert kept_timestamps == [0.0, 2.0, 4.0, 6.0, 8.0]


def test_vision_cache_hit_skips_llm_call(sample_state, monkeypatch, tmp_path):
    """When the vision.json sibling already exists in bucket, the cached
    classification is returned and call_llm_multimodal is NOT invoked."""
    frame_path = tmp_path / "f.jpg"
    frame_path.write_bytes(b"\xff\xd8\xff\xd9")
    sample_state.keyframe_candidates = [
        ScreenshotCandidate(timestamp=0.0, phash=H_ZERO, frame_path=str(frame_path)),
    ]

    cached_payload = {
        "classification": "meeting_frame",
        "app_name": None,
        "ui_state": "video call",
        "from_cache_marker": "yes",
    }

    def _hit(**kwargs):
        return json.dumps(cached_payload).encode("utf-8")

    async def _explode(*_a, **_k):
        raise AssertionError("vision LLM should not be called on cache hit")

    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.upload_bytes", lambda **_: None
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.build_llm", lambda **_k: None
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.download_bytes", _hit
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.dedupe_screenshots.call_llm_multimodal", _explode
    )

    result = dedupe_screenshots_node(sample_state)
    assert len(result["screenshots"]) == 1
    s = result["screenshots"][0]
    assert s.vision_analysis is not None
    assert s.vision_analysis["classification"] == "meeting_frame"
    assert s.vision_analysis["from_cache"] is True
