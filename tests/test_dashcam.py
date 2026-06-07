"""Regression tests for dashcam crash-segment tagging.

A crash near a segment boundary could land in the segment ffmpeg just
finalised rather than the one it's now writing, so both most-recent segments
are tagged.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, 'src')

import dashcam


def _mk(path, mtime):
    path.write_bytes(b'\x00')
    os.utime(path, (mtime, mtime))


def test_tags_two_most_recent_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(dashcam, 'DASHCAM_DIR', tmp_path)
    _mk(tmp_path / 'seg_a.mp4', 1000)
    _mk(tmp_path / 'seg_b.mp4', 2000)
    _mk(tmp_path / 'seg_c.mp4', 3000)  # newest

    target = dashcam._tag_latest_segment('crash:test')
    assert target.name == 'seg_c.mp4'  # published pointer = latest

    # Two newest tagged, oldest not.
    assert (tmp_path / 'seg_c.tag.json').exists()
    assert (tmp_path / 'seg_b.tag.json').exists()
    assert not (tmp_path / 'seg_a.tag.json').exists()
    tag = json.loads((tmp_path / 'seg_c.tag.json').read_text())
    assert tag['reason'] == 'crash:test'


def test_tag_no_segments_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(dashcam, 'DASHCAM_DIR', tmp_path)
    assert dashcam._tag_latest_segment('crash:test') is None
