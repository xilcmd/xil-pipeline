# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU003_csv_sfx_join.py — CSV + SFX/Cast join utility."""

import csv
import io
import json
import os
from pathlib import Path

import pytest

from xil_pipeline.XILU003_csv_sfx_join import (
    annotate_csv,
    derive_paths,
    join_cast,
    join_sfx,
)

# ---------------------------------------------------------------------------
# Helpers — build in-memory CSV / JSON fixtures
# ---------------------------------------------------------------------------

INPUT_COLS = [
    "md_line_num", "md_raw", "seq", "type", "section", "scene",
    "speaker", "direction", "text", "direction_type",
]


def make_csv(rows: list[dict]) -> str:
    """Render a list of row dicts into a CSV string."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=INPUT_COLS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        full = {c: "" for c in INPUT_COLS}
        full.update(row)
        writer.writerow(full)
    return buf.getvalue()


def make_sfx_json(effects: dict, default_influence: float = 0.3) -> dict:
    return {
        "show": "THE 413",
        "season": 2,
        "episode": 3,
        "defaults": {"prompt_influence": default_influence},
        "effects": effects,
    }


def make_cast_json(cast: dict) -> dict:
    return {
        "show": "THE 413",
        "season": 2,
        "episode": 3,
        "title": "Test Episode",
        "cast": cast,
    }


SAMPLE_EFFECTS = {
    "BEAT": {"type": "silence", "duration_seconds": 1.0},
    "AMBIENCE: RADIO BOOTH": {
        "prompt": "Radio booth hum",
        "duration_seconds": 30.0,
        "loop": True,
        "prompt_influence": 0.5,
    },
    "SFX: PHONE BUZZ": {
        "prompt": "Phone vibrating",
        "duration_seconds": 2.0,
    },
}

SAMPLE_CAST = {
    "adam": {
        "full_name": "Adam Santos",
        "voice_id": "voice123",
        "pan": 0.0,
        "filter": False,
        "role": "Host/Narrator",
    },
    "dez": {
        "full_name": "Dez Williams",
        "voice_id": "voice456",
        "pan": -0.15,
        "filter": True,
        "role": "Supporting",
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_files(tmp_path):
    """Write sample CSV, SFX JSON, and cast JSON to a temp dir."""
    csv_rows = [
        {"seq": "1", "type": "section_header", "text": "COLD OPEN"},
        {"seq": "2", "type": "direction", "text": "BEAT", "direction_type": "BEAT"},
        {"seq": "3", "type": "direction", "text": "AMBIENCE: RADIO BOOTH", "direction_type": "AMBIENCE"},
        {"seq": "4", "type": "dialogue", "speaker": "adam", "text": "Hello world."},
        {"seq": "5", "type": "dialogue", "speaker": "dez", "text": "Hey there."},
        {"seq": "6", "type": "direction", "text": "SFX: UNKNOWN SOUND", "direction_type": "SFX"},
        {"seq": "7", "type": "dialogue", "speaker": "ghost", "text": "Who am I?"},
    ]
    csv_path = tmp_path / "parsed.csv"
    csv_path.write_text(make_csv(csv_rows), encoding="utf-8")

    sfx_path = tmp_path / "sfx.json"
    sfx_path.write_text(json.dumps(make_sfx_json(SAMPLE_EFFECTS)), encoding="utf-8")

    cast_path = tmp_path / "cast.json"
    cast_path.write_text(json.dumps(make_cast_json(SAMPLE_CAST)), encoding="utf-8")

    out_path = tmp_path / "annotated.csv"

    return str(csv_path), str(sfx_path), str(cast_path), str(out_path)


def read_annotated(out_path: str) -> list[dict]:
    with open(out_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Unit tests: join_sfx
# ---------------------------------------------------------------------------

class TestJoinSfx:
    def _effects(self):
        return SAMPLE_EFFECTS

    def test_sfx_join_matched_beat(self):
        row = {"type": "direction", "text": "BEAT"}
        result = join_sfx(row, self._effects(), 0.3)
        assert result["sfx_matched"] == "TRUE"
        assert result["sfx_type"] == "silence"
        assert result["sfx_duration_seconds"] == 1.0
        assert result["sfx_slug"] == "beat"

    def test_sfx_join_matched_ambience(self):
        row = {"type": "direction", "text": "AMBIENCE: RADIO BOOTH"}
        result = join_sfx(row, self._effects(), 0.3)
        assert result["sfx_matched"] == "TRUE"
        assert result["sfx_loop"] == "TRUE"
        assert result["sfx_prompt"] == "Radio booth hum"
        assert result["sfx_prompt_influence"] == 0.5  # entry-level override

    def test_sfx_join_unmatched_direction(self):
        row = {"type": "direction", "text": "SFX: UNKNOWN SOUND"}
        result = join_sfx(row, self._effects(), 0.3)
        assert result["sfx_matched"] == "FALSE"
        assert result["sfx_prompt"] == ""

    def test_dialogue_row_sfx_blank(self):
        row = {"type": "dialogue", "text": "BEAT"}  # same text, wrong type
        result = join_sfx(row, self._effects(), 0.3)
        assert result["sfx_matched"] == "FALSE"
        assert all(result[c] == "" for c in ["sfx_type", "sfx_prompt", "sfx_slug"])

    def test_section_header_sfx_blank(self):
        row = {"type": "section_header", "text": "COLD OPEN"}
        result = join_sfx(row, self._effects(), 0.3)
        assert result["sfx_matched"] == "FALSE"

    def test_prompt_influence_fallback(self):
        """Effect without prompt_influence should fall back to defaults value."""
        row = {"type": "direction", "text": "SFX: PHONE BUZZ"}
        result = join_sfx(row, self._effects(), default_influence=0.7)
        assert result["sfx_prompt_influence"] == 0.7

    def test_prompt_influence_entry_overrides_default(self):
        row = {"type": "direction", "text": "AMBIENCE: RADIO BOOTH"}
        result = join_sfx(row, self._effects(), default_influence=0.99)
        assert result["sfx_prompt_influence"] == 0.5  # entry wins

    def test_sfx_type_default_when_absent(self):
        """Effect with no explicit 'type' field should default to 'sfx'."""
        row = {"type": "direction", "text": "SFX: PHONE BUZZ"}
        result = join_sfx(row, self._effects(), 0.3)
        assert result["sfx_type"] == "sfx"

    def test_slug_column(self):
        from xil_pipeline.sfx_common import slugify_effect_key
        row = {"type": "direction", "text": "AMBIENCE: RADIO BOOTH"}
        result = join_sfx(row, self._effects(), 0.3)
        assert result["sfx_slug"] == slugify_effect_key("AMBIENCE: RADIO BOOTH")

    def test_loop_false_gives_empty_string(self):
        """Effects without loop:true should produce an empty sfx_loop cell."""
        row = {"type": "direction", "text": "SFX: PHONE BUZZ"}
        result = join_sfx(row, self._effects(), 0.3)
        assert result["sfx_loop"] == ""


# ---------------------------------------------------------------------------
# Unit tests: join_cast
# ---------------------------------------------------------------------------

class TestJoinCast:
    def test_cast_join_matched(self):
        row = {"type": "dialogue", "speaker": "adam"}
        result = join_cast(row, SAMPLE_CAST)
        assert result["cast_matched"] == "TRUE"
        assert result["cast_full_name"] == "Adam Santos"
        assert result["cast_voice_id"] == "voice123"
        assert result["cast_pan"] == 0.0
        assert result["cast_filter"] == "FALSE"
        assert result["cast_role"] == "Host/Narrator"

    def test_cast_filter_true(self):
        row = {"type": "dialogue", "speaker": "dez"}
        result = join_cast(row, SAMPLE_CAST)
        assert result["cast_filter"] == "TRUE"

    def test_cast_join_unknown_speaker(self):
        row = {"type": "dialogue", "speaker": "ghost"}
        result = join_cast(row, SAMPLE_CAST)
        assert result["cast_matched"] == "FALSE"
        assert all(result[c] == "" for c in ["cast_full_name", "cast_voice_id"])

    def test_cast_join_empty_speaker(self):
        row = {"type": "dialogue", "speaker": ""}
        result = join_cast(row, SAMPLE_CAST)
        assert result["cast_matched"] == "FALSE"

    def test_direction_row_cast_blank(self):
        row = {"type": "direction", "speaker": "adam"}  # speaker field present but wrong type
        result = join_cast(row, SAMPLE_CAST)
        assert result["cast_matched"] == "FALSE"

    def test_section_header_cast_blank(self):
        row = {"type": "section_header", "speaker": ""}
        result = join_cast(row, SAMPLE_CAST)
        assert result["cast_matched"] == "FALSE"


# ---------------------------------------------------------------------------
# Integration tests: annotate_csv
# ---------------------------------------------------------------------------

class TestAnnotateCsv:
    def test_total_row_count(self, tmp_files):
        csv_path, sfx_path, cast_path, out_path = tmp_files
        total, *_ = annotate_csv(csv_path, sfx_path, cast_path, out_path)
        rows = read_annotated(out_path)
        assert total == 7
        assert len(rows) == 7

    def test_all_input_columns_preserved(self, tmp_files):
        csv_path, sfx_path, cast_path, out_path = tmp_files
        annotate_csv(csv_path, sfx_path, cast_path, out_path)
        rows = read_annotated(out_path)
        for col in INPUT_COLS:
            assert col in rows[0], f"Missing column: {col}"

    def test_sfx_columns_added(self, tmp_files):
        csv_path, sfx_path, cast_path, out_path = tmp_files
        annotate_csv(csv_path, sfx_path, cast_path, out_path)
        rows = read_annotated(out_path)
        for col in ["sfx_type", "sfx_prompt", "sfx_matched"]:
            assert col in rows[0], f"Missing SFX column: {col}"

    def test_cast_columns_added(self, tmp_files):
        csv_path, sfx_path, cast_path, out_path = tmp_files
        annotate_csv(csv_path, sfx_path, cast_path, out_path)
        rows = read_annotated(out_path)
        for col in ["cast_full_name", "cast_voice_id", "cast_matched"]:
            assert col in rows[0], f"Missing cast column: {col}"

    def test_beat_row_sfx_populated(self, tmp_files):
        csv_path, sfx_path, cast_path, out_path = tmp_files
        annotate_csv(csv_path, sfx_path, cast_path, out_path)
        rows = read_annotated(out_path)
        beat = next(r for r in rows if r["text"] == "BEAT")
        assert beat["sfx_matched"] == "TRUE"
        assert beat["sfx_type"] == "silence"

    def test_adam_dialogue_cast_populated(self, tmp_files):
        csv_path, sfx_path, cast_path, out_path = tmp_files
        annotate_csv(csv_path, sfx_path, cast_path, out_path)
        rows = read_annotated(out_path)
        adam = next(r for r in rows if r["speaker"] == "adam")
        assert adam["cast_matched"] == "TRUE"
        assert adam["cast_full_name"] == "Adam Santos"

    def test_section_header_both_blank(self, tmp_files):
        csv_path, sfx_path, cast_path, out_path = tmp_files
        annotate_csv(csv_path, sfx_path, cast_path, out_path)
        rows = read_annotated(out_path)
        header = next(r for r in rows if r["type"] == "section_header")
        assert header["sfx_matched"] == "FALSE"
        assert header["cast_matched"] == "FALSE"

    def test_summary_counts(self, tmp_files):
        csv_path, sfx_path, cast_path, out_path = tmp_files
        total, n_dir, sfx_hit, n_dlg, cast_hit = annotate_csv(
            csv_path, sfx_path, cast_path, out_path
        )
        # 3 direction rows: BEAT (match), AMBIENCE (match), SFX:UNKNOWN (no match)
        assert n_dir == 3
        assert sfx_hit == 2
        # 3 dialogue rows: adam (match), dez (match), ghost (no match)
        assert n_dlg == 3
        assert cast_hit == 2

    def test_prompt_influence_fallback_in_output(self, tmp_files):
        """SFX: PHONE BUZZ has no entry-level prompt_influence — should use default 0.3."""
        csv_path, sfx_path, cast_path, out_path = tmp_files
        # Add SFX: PHONE BUZZ row to input
        rows_extra = [
            {"seq": "99", "type": "direction", "text": "SFX: PHONE BUZZ", "direction_type": "SFX"},
        ]
        extra_csv = tmp_path_for_extra(tmp_files, rows_extra)
        annotate_csv(extra_csv, sfx_path, cast_path, out_path)
        rows = read_annotated(out_path)
        buzz = next(r for r in rows if r["text"] == "SFX: PHONE BUZZ")
        assert buzz["sfx_prompt_influence"] == "0.3"


def tmp_path_for_extra(tmp_files, extra_rows):
    """Write a one-row CSV to the same temp dir as tmp_files."""
    csv_path = tmp_files[0]
    parent = os.path.dirname(csv_path)
    new_path = os.path.join(parent, "extra.csv")
    Path(new_path).write_text(make_csv(extra_rows), encoding="utf-8")
    return new_path


# ---------------------------------------------------------------------------
# Path derivation test
# ---------------------------------------------------------------------------

class TestDerivePaths:
    def test_episode_path_derivation(self):
        csv_p, sfx_p, cast_p, out_p = derive_paths("S02E03")
        assert csv_p == "parsed/parsed_sample_S02E03.csv"
        assert sfx_p == "sfx_sample_S02E03.json"
        assert cast_p == "cast_sample_S02E03.json"
        assert out_p == "parsed/parsed_sample_S02E03_annotated.csv"

    def test_s01e01_derivation(self):
        csv_p, sfx_p, cast_p, out_p = derive_paths("S01E01")
        assert "S01E01" in csv_p
        assert "S01E01" in sfx_p
        assert "S01E01" in cast_p
        assert out_p.endswith("_annotated.csv")

    def test_out_path_differs_from_csv(self):
        csv_p, _, _, out_p = derive_paths("S02E03")
        assert os.path.abspath(out_p) != os.path.abspath(csv_p)
