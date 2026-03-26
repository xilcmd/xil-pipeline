"""Tests for XILP010_studio_import.py."""

import io
import json
import os
import zipfile

import pytest

from xil_pipeline.XILP010_studio_import import _parse_zip_seq, extract_stems


# --- Helpers ---


def _make_zip(members: dict[str, bytes]) -> str:
    """Create an in-memory ZIP and write it to a temp file.

    Args:
        members: Mapping of filename → content bytes.

    Returns:
        Path to the temporary ZIP file.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf


def _minimal_parsed(entries):
    """Wrap entries in a minimal parsed dict."""
    return {
        "show": "THE 413",
        "season": 2,
        "episode": 2,
        "title": "What We Carry",
        "season_title": "The Letters",
        "entries": entries,
        "stats": {
            "total_entries": len(entries),
            "dialogue_lines": sum(1 for e in entries if e["type"] == "dialogue"),
            "direction_lines": sum(1 for e in entries if e["type"] == "direction"),
            "characters_for_tts": 0,
            "speakers": [],
            "sections": [],
        },
    }


def _entry(seq, entry_type, speaker=None, section="cold-open", scene=None,
           text="", direction_type=None):
    """Build a minimal parsed entry dict."""
    return {
        "seq": seq,
        "type": entry_type,
        "section": section,
        "scene": scene,
        "speaker": speaker,
        "direction": None,
        "text": text,
        "direction_type": direction_type,
    }


def _write_zip(tmp_path, members):
    """Write a ZIP from members dict and return the path."""
    buf = _make_zip(members)
    zip_path = str(tmp_path / "export.zip")
    with open(zip_path, "wb") as f:
        f.write(buf.getvalue())
    return zip_path


# --- _parse_zip_seq tests ---


class TestParseZipSeq:
    def test_standard_format(self):
        assert _parse_zip_seq("042_Chapter 1.mp3") == 42

    def test_leading_zeros(self):
        assert _parse_zip_seq("001_Chapter 1.mp3") == 1

    def test_three_digit(self):
        assert _parse_zip_seq("240_Chapter 1.mp3") == 240

    def test_no_match(self):
        assert _parse_zip_seq("readme.txt") is None

    def test_nested_path(self):
        assert _parse_zip_seq("subdir/003_Chapter 1.mp3") == 3


# --- extract_stems tests ---


class TestExtractStems:
    def test_extracts_dialogue_only(self, tmp_path):
        """Only dialogue entries are extracted by default."""
        entries = [
            _entry(1, "section_header", text="COLD OPEN"),
            _entry(2, "direction", text="SFX: RADIO STATIC", direction_type="SFX"),
            _entry(3, "dialogue", speaker="adam", text="Hello world"),
            _entry(4, "dialogue", speaker="maya", text="Hi there"),
        ]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {
            "001_Chapter 1.mp3": b"header-audio",
            "002_Chapter 1.mp3": b"sfx-audio",
            "003_Chapter 1.mp3": b"adam-audio",
            "004_Chapter 1.mp3": b"maya-audio",
        })

        stems_dir = str(tmp_path / "stems")
        stats = extract_stems(zip_path, parsed, stems_dir)

        assert stats["extracted"] == 2
        assert stats["skipped_header"] == 1
        assert stats["skipped_type"] == 1

        assert os.path.isfile(os.path.join(stems_dir, "003_cold-open_adam.mp3"))
        assert os.path.isfile(os.path.join(stems_dir, "004_cold-open_maya.mp3"))
        assert not os.path.isfile(os.path.join(stems_dir, "002_cold-open_sfx.mp3"))

    def test_skip_existing(self, tmp_path):
        """Existing stems are skipped unless --force."""
        entries = [_entry(1, "dialogue", speaker="adam", text="Hello")]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {"001_Chapter 1.mp3": b"new-audio"})

        stems_dir = str(tmp_path / "stems")
        os.makedirs(stems_dir)
        existing = os.path.join(stems_dir, "001_cold-open_adam.mp3")
        with open(existing, "wb") as f:
            f.write(b"old-audio")

        stats = extract_stems(zip_path, parsed, stems_dir)
        assert stats["skipped_exists"] == 1
        assert stats["extracted"] == 0

        with open(existing, "rb") as f:
            assert f.read() == b"old-audio"

    def test_force_overwrites(self, tmp_path):
        """--force overwrites existing stems."""
        entries = [_entry(1, "dialogue", speaker="adam", text="Hello")]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {"001_Chapter 1.mp3": b"new-audio"})

        stems_dir = str(tmp_path / "stems")
        os.makedirs(stems_dir)
        existing = os.path.join(stems_dir, "001_cold-open_adam.mp3")
        with open(existing, "wb") as f:
            f.write(b"old-audio")

        stats = extract_stems(zip_path, parsed, stems_dir, force=True)
        assert stats["extracted"] == 1

        with open(existing, "rb") as f:
            assert f.read() == b"new-audio"

    def test_dry_run_no_files(self, tmp_path):
        """Dry run does not create files or directories."""
        entries = [_entry(1, "dialogue", speaker="adam", text="Hello")]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {"001_Chapter 1.mp3": b"audio-data"})

        stems_dir = str(tmp_path / "stems")
        stats = extract_stems(zip_path, parsed, stems_dir, dry_run=True)
        assert stats["extracted"] == 1
        assert not os.path.exists(stems_dir)

    def test_all_types_includes_directions(self, tmp_path):
        """include_dtypes with all types extracts direction entries as _sfx stems."""
        entries = [
            _entry(1, "section_header", text="COLD OPEN"),
            _entry(2, "direction", text="SFX: RADIO STATIC", direction_type="SFX"),
            _entry(3, "dialogue", speaker="adam", text="Hello"),
        ]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {
            "001_Chapter 1.mp3": b"header",
            "002_Chapter 1.mp3": b"sfx",
            "003_Chapter 1.mp3": b"adam",
        })

        stems_dir = str(tmp_path / "stems")
        stats = extract_stems(
            zip_path, parsed, stems_dir,
            include_dtypes={"SFX", "MUSIC", "BEAT", "AMBIENCE"},
        )

        assert stats["extracted"] == 2  # dialogue + direction
        assert stats["skipped_header"] == 1  # header still skipped
        assert os.path.isfile(os.path.join(stems_dir, "002_cold-open_sfx.mp3"))
        assert os.path.isfile(os.path.join(stems_dir, "003_cold-open_adam.mp3"))

    def test_scene_in_filename(self, tmp_path):
        """Scene slug is included in the stem filename when present."""
        entries = [
            _entry(14, "dialogue", speaker="maya", section="act1",
                   scene="scene-1", text="You're quiet."),
        ]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {"014_Chapter 1.mp3": b"maya-audio"})

        stems_dir = str(tmp_path / "stems")
        stats = extract_stems(zip_path, parsed, stems_dir)
        assert stats["extracted"] == 1
        assert os.path.isfile(
            os.path.join(stems_dir, "014_act1-scene-1_maya.mp3")
        )

    def test_missing_seq_reported(self, tmp_path):
        """ZIP entries with seq not in parsed JSON are reported."""
        entries = [_entry(1, "dialogue", speaker="adam", text="Hello")]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {
            "001_Chapter 1.mp3": b"audio",
            "099_Chapter 1.mp3": b"orphan",
        })

        stems_dir = str(tmp_path / "stems")
        stats = extract_stems(zip_path, parsed, stems_dir)
        assert stats["missing_seq"] == 1
        assert stats["extracted"] == 1

    def test_gen_sfx_only(self, tmp_path):
        """include_dtypes={'SFX'} extracts SFX but skips MUSIC/BEAT/AMBIENCE."""
        entries = [
            _entry(1, "direction", text="SFX: PHONE BUZZ", direction_type="SFX"),
            _entry(2, "direction", text="MUSIC: THEME", direction_type="MUSIC"),
            _entry(3, "direction", text="BEAT", direction_type="BEAT"),
            _entry(4, "direction", text="AMBIENCE: DINER", direction_type="AMBIENCE"),
            _entry(5, "dialogue", speaker="adam", text="Hello"),
        ]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {
            "001_Chapter 1.mp3": b"sfx",
            "002_Chapter 1.mp3": b"music",
            "003_Chapter 1.mp3": b"beat",
            "004_Chapter 1.mp3": b"ambience",
            "005_Chapter 1.mp3": b"adam",
        })

        stems_dir = str(tmp_path / "stems")
        stats = extract_stems(
            zip_path, parsed, stems_dir,
            include_dtypes={"SFX"},
        )

        assert stats["extracted"] == 2  # SFX + dialogue
        assert stats["skipped_type"] == 3  # MUSIC + BEAT + AMBIENCE
        assert os.path.isfile(os.path.join(stems_dir, "001_cold-open_sfx.mp3"))
        assert os.path.isfile(os.path.join(stems_dir, "005_cold-open_adam.mp3"))
        assert not os.path.isfile(os.path.join(stems_dir, "002_cold-open_sfx.mp3"))

    def test_gen_music_and_beats(self, tmp_path):
        """include_dtypes={'MUSIC', 'BEAT'} extracts both, skips SFX/AMBIENCE."""
        entries = [
            _entry(1, "direction", text="SFX: PHONE BUZZ", direction_type="SFX"),
            _entry(2, "direction", text="MUSIC: THEME", direction_type="MUSIC"),
            _entry(3, "direction", text="BEAT", direction_type="BEAT"),
            _entry(4, "direction", text="AMBIENCE: DINER", direction_type="AMBIENCE"),
        ]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {
            "001_Chapter 1.mp3": b"sfx",
            "002_Chapter 1.mp3": b"music",
            "003_Chapter 1.mp3": b"beat",
            "004_Chapter 1.mp3": b"ambience",
        })

        stems_dir = str(tmp_path / "stems")
        stats = extract_stems(
            zip_path, parsed, stems_dir,
            include_dtypes={"MUSIC", "BEAT"},
        )

        assert stats["extracted"] == 2  # MUSIC + BEAT
        assert stats["skipped_type"] == 2  # SFX + AMBIENCE
        assert os.path.isfile(os.path.join(stems_dir, "002_cold-open_sfx.mp3"))
        assert os.path.isfile(os.path.join(stems_dir, "003_cold-open_sfx.mp3"))
        assert not os.path.isfile(os.path.join(stems_dir, "001_cold-open_sfx.mp3"))

    def test_empty_include_dtypes_skips_all_directions(self, tmp_path):
        """Empty include_dtypes set extracts only dialogue."""
        entries = [
            _entry(1, "direction", text="SFX: PHONE BUZZ", direction_type="SFX"),
            _entry(2, "dialogue", speaker="adam", text="Hello"),
        ]
        parsed = _minimal_parsed(entries)
        zip_path = _write_zip(tmp_path, {
            "001_Chapter 1.mp3": b"sfx",
            "002_Chapter 1.mp3": b"adam",
        })

        stems_dir = str(tmp_path / "stems")
        stats = extract_stems(zip_path, parsed, stems_dir, include_dtypes=set())

        assert stats["extracted"] == 1
        assert stats["skipped_type"] == 1
