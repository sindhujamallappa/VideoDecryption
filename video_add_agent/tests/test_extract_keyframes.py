"""Tests for the extract_keyframes node and the keyframes utilities."""
from __future__ import annotations

from pathlib import Path

import imagehash
from PIL import Image

from video_add_agent.utils.keyframes import (
    cluster_by_hash,
    gate_consecutive,
    pick_representative,
    phash_distance,
)
from video_add_agent.state import ScreenshotCandidate
from video_add_agent.nodes.extract_keyframes import extract_keyframes_node


def _h(hex_str: str) -> imagehash.ImageHash:
    """Build an ImageHash with explicit bits — solid-color synthesized PNGs
    all collapse to the same pHash, so we bypass the image step entirely."""
    return imagehash.hex_to_hash(hex_str)


# Two hashes that differ in every bit → distance 64 (max for 64-bit hash)
H_ZERO = "0000000000000000"
H_ONES = "ffffffffffffffff"
# Hashes 1 bit apart
H_ONE_BIT = "0000000000000001"


def test_phash_distance_self_is_zero():
    assert phash_distance(_h(H_ZERO), _h(H_ZERO)) == 0
    assert phash_distance(H_ZERO, H_ZERO) == 0


def test_phash_distance_max():
    assert phash_distance(_h(H_ZERO), _h(H_ONES)) == 64


def test_gate_consecutive_keeps_first_and_drops_close_neighbors():
    items = [
        (Path("a.jpg"), _h(H_ZERO), 0.0),
        (Path("b.jpg"), _h(H_ZERO), 0.5),  # identical
        (Path("c.jpg"), _h(H_ONE_BIT), 1.0),  # 1 bit away — under threshold
        (Path("d.jpg"), _h(H_ONES), 1.5),  # very different
    ]
    kept = gate_consecutive(items, threshold=8)
    assert len(kept) == 2
    assert kept[0].timestamp == 0.0
    assert kept[1].timestamp == 1.5


def test_cluster_by_hash_groups_similar():
    cands = [
        ScreenshotCandidate(timestamp=0.0, phash=H_ZERO, frame_path="a"),
        ScreenshotCandidate(timestamp=1.0, phash=H_ZERO, frame_path="b"),
        ScreenshotCandidate(timestamp=2.0, phash=H_ONES, frame_path="c"),
    ]
    clusters = cluster_by_hash(cands, threshold=5)
    assert len(clusters) == 2
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]


def test_pick_representative_uses_transcript_midpoints():
    cluster = [
        ScreenshotCandidate(timestamp=0.0, phash=H_ZERO, frame_path="a"),
        ScreenshotCandidate(timestamp=10.0, phash=H_ZERO, frame_path="b"),
        ScreenshotCandidate(timestamp=20.0, phash=H_ZERO, frame_path="c"),
    ]
    rep = pick_representative(cluster, transcript_midpoints=[10.5])
    assert rep.timestamp == 10.0


def test_pick_representative_falls_back_to_median():
    cluster = [
        ScreenshotCandidate(timestamp=0.0, phash=H_ZERO, frame_path="a"),
        ScreenshotCandidate(timestamp=5.0, phash=H_ZERO, frame_path="b"),
        ScreenshotCandidate(timestamp=10.0, phash=H_ZERO, frame_path="c"),
    ]
    rep = pick_representative(cluster, transcript_midpoints=None)
    assert rep.timestamp == 5.0


def test_extract_keyframes_node_full(sample_state, monkeypatch, tmp_path):
    """Mock ffmpeg + bucket; verify node returns ScreenshotCandidates."""
    fake_frames = []
    for i in range(4):
        p = tmp_path / f"frame_{i:05d}.jpg"
        # Use distinct content for each frame so phash differs
        Image.new("L", (32, 32), color=i * 60).save(p, format="JPEG")
        fake_frames.append(p)

    monkeypatch.setattr(
        "video_add_agent.nodes.extract_keyframes.download_bytes", lambda **_: b"fake"
    )
    monkeypatch.setattr(
        "video_add_agent.nodes.extract_keyframes.dump_frames", lambda *_a, **_k: fake_frames
    )

    result = extract_keyframes_node(sample_state)
    assert "error" not in result
    cands = result["keyframe_candidates"]
    assert isinstance(cands, list)
    assert len(cands) >= 1
