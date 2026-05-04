"""Tests for dedupe_screenshots node."""
from __future__ import annotations


from video_add_agent.nodes.dedupe_screenshots import dedupe_screenshots_node
from video_add_agent.state import ScreenshotCandidate


# Bypass image-based hashing — use explicit 64-bit hex hashes with known
# Hamming distances. Solid-color PIL images all collapse to the same pHash.
H_ZERO = "0000000000000000"
H_ONES = "ffffffffffffffff"


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

    result = dedupe_screenshots_node(sample_state)
    assert "error" not in result
    assert len(result["screenshots"]) == 2
    assert len(uploads) == 2
    assert all("/raw/image-" in u["path"] for u in uploads)


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
    result = dedupe_screenshots_node(sample_state)
    assert "error" not in result
    assert result["screenshots"] == []
