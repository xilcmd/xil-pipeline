# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU007_mp3_hash.py — recursive MP3 SHA-256 hash utility."""

import hashlib
import json
import os
import sys

from xil_pipeline import XILU007_mp3_hash as mp3hash

# ─── Helpers ───


def _write_mp3(path: str, content: bytes = b"ID3fake") -> bytes:
    """Write fake MP3 bytes and return them."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return content


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ─── Tests: hash_file ───


class TestHashFile:
    def test_known_bytes(self, tmp_path):
        data = b"hello xil-pipeline"
        p = tmp_path / "test.mp3"
        p.write_bytes(data)
        assert mp3hash.hash_file(str(p)) == _sha256(data)

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty.mp3"
        p.write_bytes(b"")
        assert mp3hash.hash_file(str(p)) == _sha256(b"")

    def test_returns_64_char_hex(self, tmp_path):
        p = tmp_path / "x.mp3"
        p.write_bytes(b"data")
        result = mp3hash.hash_file(str(p))
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_files_different_hashes(self, tmp_path):
        a = tmp_path / "a.mp3"
        b = tmp_path / "b.mp3"
        a.write_bytes(b"aaa")
        b.write_bytes(b"bbb")
        assert mp3hash.hash_file(str(a)) != mp3hash.hash_file(str(b))


# ─── Tests: scan_mp3s ───


class TestScanMp3s:
    def test_finds_mp3_in_root(self, tmp_path):
        content = b"ID3"
        _write_mp3(str(tmp_path / "a.mp3"), content)
        results = mp3hash.scan_mp3s(str(tmp_path))
        assert len(results) == 1
        assert results[0][1] == _sha256(content)

    def test_ignores_non_mp3(self, tmp_path):
        _write_mp3(str(tmp_path / "a.mp3"))
        (tmp_path / "b.wav").write_bytes(b"RIFF")
        (tmp_path / "c.txt").write_text("notes")
        results = mp3hash.scan_mp3s(str(tmp_path))
        assert len(results) == 1

    def test_recurses_into_subdirectories(self, tmp_path):
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        _write_mp3(str(tmp_path / "root.mp3"))
        _write_mp3(str(sub / "nested.mp3"))
        results = mp3hash.scan_mp3s(str(tmp_path))
        assert len(results) == 2

    def test_sorted_within_directory(self, tmp_path):
        for name in ("c.mp3", "a.mp3", "b.mp3"):
            _write_mp3(str(tmp_path / name))
        results = mp3hash.scan_mp3s(str(tmp_path))
        basenames = [os.path.basename(r[0]) for r in results]
        assert basenames == sorted(basenames)

    def test_returns_absolute_paths(self, tmp_path):
        _write_mp3(str(tmp_path / "x.mp3"))
        results = mp3hash.scan_mp3s(str(tmp_path))
        assert os.path.isabs(results[0][0])

    def test_empty_directory(self, tmp_path):
        assert mp3hash.scan_mp3s(str(tmp_path)) == []

    def test_case_insensitive_extension(self, tmp_path):
        """*.MP3 and *.Mp3 should also be picked up."""
        (tmp_path / "upper.MP3").write_bytes(b"data")
        (tmp_path / "mixed.Mp3").write_bytes(b"data")
        results = mp3hash.scan_mp3s(str(tmp_path))
        assert len(results) == 2


# ─── Tests: main() ───


class TestMain:
    def test_logs_filename_and_hash(self, tmp_path, caplog):
        content = b"ID3test"
        _write_mp3(str(tmp_path / "demo.mp3"), content)
        sys.argv = ["xil-mp3-hash", str(tmp_path)]
        import logging

        with caplog.at_level(logging.INFO):
            mp3hash.main()
        combined = caplog.text
        assert "demo.mp3" in combined
        assert _sha256(content) in combined

    def test_relative_paths_by_default(self, tmp_path, caplog):
        sub = tmp_path / "sub"
        sub.mkdir()
        _write_mp3(str(sub / "track.mp3"))
        sys.argv = ["xil-mp3-hash", str(tmp_path)]
        import logging

        with caplog.at_level(logging.INFO):
            mp3hash.main()
        # Should show relative path, not full absolute path
        assert str(tmp_path) not in caplog.text.replace(str(tmp_path), "ROOT")
        assert "sub" in caplog.text
        assert "track.mp3" in caplog.text

    def test_absolute_flag(self, tmp_path, caplog):
        _write_mp3(str(tmp_path / "x.mp3"))
        sys.argv = ["xil-mp3-hash", str(tmp_path), "--absolute"]
        import logging

        with caplog.at_level(logging.INFO):
            mp3hash.main()
        assert str(tmp_path) in caplog.text

    def test_output_file_written(self, tmp_path):
        content = b"ID3"
        _write_mp3(str(tmp_path / "a.mp3"), content)
        out = str(tmp_path / "hashes.txt")
        sys.argv = ["xil-mp3-hash", str(tmp_path), "--output", out]
        mp3hash.main()
        text = open(out).read()
        assert "a.mp3" in text
        assert _sha256(content) in text
        assert " : " in text

    def test_json_output(self, tmp_path, capsys):
        content = b"audio"
        _write_mp3(str(tmp_path / "s.mp3"), content)
        sys.argv = ["xil-mp3-hash", str(tmp_path), "--json"]
        mp3hash.main()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["sha256"] == _sha256(content)
        assert "s.mp3" in data[0]["path"]

    def test_no_mp3s_does_not_crash(self, tmp_path, caplog):
        sys.argv = ["xil-mp3-hash", str(tmp_path)]
        import logging

        with caplog.at_level(logging.INFO):
            mp3hash.main()
        assert "No MP3" in caplog.text

    def test_single_file_mode(self, tmp_path, caplog):
        """Passing a single MP3 file path hashes just that file."""
        content = b"single"
        p = tmp_path / "one.mp3"
        p.write_bytes(content)
        sys.argv = ["xil-mp3-hash", str(p)]
        import logging

        with caplog.at_level(logging.INFO):
            mp3hash.main()
        assert "one.mp3" in caplog.text
        assert _sha256(content) in caplog.text

    def test_single_file_json(self, tmp_path, capsys):
        """Single file + --json outputs a one-element array."""
        content = b"solo"
        p = tmp_path / "solo.mp3"
        p.write_bytes(content)
        sys.argv = ["xil-mp3-hash", str(p), "--json"]
        mp3hash.main()
        data = json.loads(capsys.readouterr().out)
        assert len(data) == 1
        assert data[0]["sha256"] == _sha256(content)

    def test_invalid_path_logs_error(self, tmp_path, caplog):
        sys.argv = ["xil-mp3-hash", str(tmp_path / "nonexistent")]
        import logging

        with caplog.at_level(logging.ERROR):
            mp3hash.main()
        assert "Not a file or directory" in caplog.text
