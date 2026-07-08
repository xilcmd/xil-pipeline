# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU005_discover_SFX.py — local SFX library discovery.

Covers the hierarchical SFX/{slug}/ layout (PR #23): fetch_local_records
must find files both in the flat shared pool and in per-show subdirs.
"""


import pytest

from xil_pipeline.XILU005_discover_SFX import fetch_local_records


def _make_mp3(path):
    from pydub import AudioSegment
    path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.silent(duration=80).export(str(path), format="mp3")


@pytest.fixture
def sfx_tree(tmp_path, monkeypatch):
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    sfx_dir = tmp_path / "SFX"
    sfx_dir.mkdir()
    _make_mp3(sfx_dir / "shared_door.mp3")
    _make_mp3(sfx_dir / "myshow" / "myshow_beat.mp3")
    # noise that must never appear in results
    (sfx_dir / "notes.csv").write_text("a,b,c")
    (sfx_dir / "backup.zip").write_bytes(b"PK\x03\x04")
    return sfx_dir


class TestFetchLocalRecords:
    def test_scans_flat_pool_file(self, sfx_tree):
        records = fetch_local_records(str(sfx_tree))
        flat = [r for r in records if r["filename"] == "shared_door.mp3"]
        assert len(flat) == 1
        assert flat[0]["show"] == ""

    def test_scans_per_show_subdir_file(self, sfx_tree):
        records = fetch_local_records(str(sfx_tree))
        nested = [r for r in records if r["filename"] == "myshow_beat.mp3"]
        assert len(nested) == 1
        assert nested[0]["show"] == "myshow"

    def test_both_flat_and_hierarchical_in_one_call(self, sfx_tree):
        records = fetch_local_records(str(sfx_tree))
        assert len(records) == 2
        shows = {r["show"] for r in records}
        assert shows == {"", "myshow"}

    def test_nonexistent_dir_returns_empty_with_warning(self, tmp_path):
        records = fetch_local_records(str(tmp_path / "nope"))
        assert records == []

    def test_ignores_non_mp3_files(self, sfx_tree):
        records = fetch_local_records(str(sfx_tree))
        filenames = {r["filename"] for r in records}
        assert "notes.csv" not in filenames
        assert "backup.zip" not in filenames

    def test_sfx_dir_pointed_directly_at_show_subdir(self, sfx_tree):
        # --sfx-dir SFX/myshow: relpath of a file directly inside equals
        # "." against that same root — must not double-label as its own name.
        show_dir = sfx_tree / "myshow"
        records = fetch_local_records(str(show_dir))
        assert len(records) == 1
        assert records[0]["show"] == ""

    def test_path_is_absolute_and_correct(self, sfx_tree):
        records = fetch_local_records(str(sfx_tree))
        nested = next(r for r in records if r["filename"] == "myshow_beat.mp3")
        assert nested["path"] == str(sfx_tree / "myshow" / "myshow_beat.mp3")
