# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU006_splice_parsed.py — parsed JSON splice utility."""

import copy
import json
import os

import pytest

from xil_pipeline import XILU006_splice_parsed as splice

# ─── Helpers ───


def _entry(seq, type_="dialogue", section="act1", scene=None, speaker="adam", text="Hello"):
    return {
        "seq": seq,
        "type": type_,
        "section": section,
        "scene": scene,
        "speaker": speaker,
        "direction": None,
        "text": text,
        "direction_type": None,
    }


def _make_entries():
    """Preamble (-2, -1) + body (1..5) with diverse types."""
    return [
        _entry(-2, section="preamble", speaker="tina", text="Preamble voice"),
        _entry(-1, type_="direction", section="preamble", speaker=None, text="INTRO MUSIC"),
        _entry(1, type_="section_header", section="act1", speaker=None, text="ACT ONE"),
        _entry(2, section="act1", speaker="adam", text="Line one"),
        _entry(3, section="act1", speaker="tina", text="Line two"),
        _entry(4, type_="direction", section="act1", speaker="tina", text="BEAT"),
        _entry(5, section="act1", speaker="adam", text="Line three"),
    ]


def _make_parsed_data(entries=None):
    """Wrap entries in a full parsed JSON structure."""
    if entries is None:
        entries = _make_entries()
    return {
        "show": "TEST SHOW",
        "season": 1,
        "episode": 1,
        "title": "Test Episode",
        "source_file": "test.md",
        "entries": entries,
        "stats": {},
    }


# ─── TestRenumberEntries ───


class TestRenumberEntries:
    def test_preamble_preserved(self):
        entries = _make_entries()
        result = splice.renumber_entries(entries)
        preamble = [e for e in result if e["seq"] <= 0]
        assert len(preamble) == 2
        assert preamble[0]["seq"] == -2
        assert preamble[1]["seq"] == -1

    def test_body_gets_contiguous_seq(self):
        entries = [
            _entry(-1, section="preamble"),
            _entry(1, section="act1"),
            _entry(5, section="act1"),  # gap
            _entry(10, section="act1"),  # gap
        ]
        result = splice.renumber_entries(entries)
        body = [e for e in result if e["seq"] > 0]
        assert [e["seq"] for e in body] == [1, 2, 3]

    def test_order_preserved(self):
        entries = _make_entries()
        result = splice.renumber_entries(entries)
        body = [e for e in result if e["seq"] > 0]
        texts = [e["text"] for e in body]
        assert texts == ["ACT ONE", "Line one", "Line two", "BEAT", "Line three"]

    def test_does_not_mutate_input(self):
        entries = _make_entries()
        original = copy.deepcopy(entries)
        splice.renumber_entries(entries)
        assert entries == original


# ─── TestExtractSeqRange ───


class TestExtractSeqRange:
    def test_extracts_correct_range(self):
        entries = _make_entries()
        result = splice.extract_seq_range(entries, 2, 3)
        assert len(result) == 2
        assert result[0]["text"] == "Line one"
        assert result[1]["text"] == "Line two"

    def test_returns_deep_copies(self):
        entries = _make_entries()
        result = splice.extract_seq_range(entries, 2, 2)
        result[0]["text"] = "MUTATED"
        assert entries[3]["text"] == "Line one"  # original unchanged

    def test_empty_range(self):
        entries = _make_entries()
        result = splice.extract_seq_range(entries, 99, 100)
        assert result == []

    def test_single_entry(self):
        entries = _make_entries()
        result = splice.extract_seq_range(entries, 4, 4)
        assert len(result) == 1
        assert result[0]["text"] == "BEAT"


# ─── TestSpliceEntries ───


class TestSpliceEntries:
    def test_mid_stream_insert(self):
        entries = _make_entries()
        new = [_entry(0, text="Inserted A"), _entry(0, text="Inserted B")]
        result = splice.splice_entries(entries, insert_after_seq=3, new_entries=new)
        body = [e for e in result if e["seq"] > 0]
        texts = [e["text"] for e in body]
        assert texts == ["ACT ONE", "Line one", "Line two", "Inserted A", "Inserted B", "BEAT", "Line three"]
        # Check contiguous seq
        assert [e["seq"] for e in body] == [1, 2, 3, 4, 5, 6, 7]

    def test_section_inherited_from_insertion_point(self):
        entries = _make_entries()
        new = [_entry(0, section="WRONG", text="New")]
        result = splice.splice_entries(entries, insert_after_seq=3, new_entries=new)
        inserted = [e for e in result if e["text"] == "New"][0]
        assert inserted["section"] == "act1"  # inherited from seq 3

    def test_section_override(self):
        entries = _make_entries()
        new = [_entry(0, text="New")]
        result = splice.splice_entries(entries, insert_after_seq=3, new_entries=new, section_override="closing")
        inserted = [e for e in result if e["text"] == "New"][0]
        assert inserted["section"] == "closing"

    def test_scene_override(self):
        entries = _make_entries()
        new = [_entry(0, text="New")]
        result = splice.splice_entries(entries, insert_after_seq=3, new_entries=new, scene_override="scene-2")
        inserted = [e for e in result if e["text"] == "New"][0]
        assert inserted["scene"] == "scene-2"

    def test_preamble_untouched(self):
        entries = _make_entries()
        new = [_entry(0, text="New")]
        result = splice.splice_entries(entries, insert_after_seq=1, new_entries=new)
        preamble = [e for e in result if e["seq"] <= 0]
        assert len(preamble) == 2
        assert preamble[0]["seq"] == -2
        assert preamble[1]["seq"] == -1

    def test_insert_after_last_entry(self):
        entries = _make_entries()
        new = [_entry(0, text="Appended")]
        result = splice.splice_entries(entries, insert_after_seq=5, new_entries=new)
        body = [e for e in result if e["seq"] > 0]
        assert body[-1]["text"] == "Appended"
        assert body[-1]["seq"] == 6

    def test_error_on_nonexistent_seq(self):
        entries = _make_entries()
        new = [_entry(0, text="New")]
        with pytest.raises(ValueError, match="not found"):
            splice.splice_entries(entries, insert_after_seq=99, new_entries=new)

    def test_error_on_preamble_seq(self):
        entries = _make_entries()
        new = [_entry(0, text="New")]
        with pytest.raises(ValueError, match="preamble"):
            splice.splice_entries(entries, insert_after_seq=-1, new_entries=new)

    def test_does_not_mutate_inputs(self):
        entries = _make_entries()
        new = [_entry(0, text="New")]
        orig_entries = copy.deepcopy(entries)
        orig_new = copy.deepcopy(new)
        splice.splice_entries(entries, insert_after_seq=3, new_entries=new)
        assert entries == orig_entries
        assert new == orig_new


# ─── TestDeleteEntries ───


class TestDeleteEntries:
    def test_delete_middle_range(self):
        entries = _make_entries()
        result = splice.delete_entries(entries, (3, 4))
        body = [e for e in result if e["seq"] > 0]
        texts = [e["text"] for e in body]
        assert texts == ["ACT ONE", "Line one", "Line three"]
        assert [e["seq"] for e in body] == [1, 2, 3]

    def test_noop_on_empty_range(self):
        entries = _make_entries()
        result = splice.delete_entries(entries, (99, 100))
        body_before = [e for e in entries if e["seq"] > 0]
        body_after = [e for e in result if e["seq"] > 0]
        assert len(body_before) == len(body_after)

    def test_error_on_preamble_range(self):
        entries = _make_entries()
        with pytest.raises(ValueError, match="preamble"):
            splice.delete_entries(entries, (-2, 1))

    def test_does_not_mutate_input(self):
        entries = _make_entries()
        original = copy.deepcopy(entries)
        splice.delete_entries(entries, (2, 3))
        assert entries == original


# ─── TestSpliceAndDelete ───


class TestSpliceAndDelete:
    def test_delete_then_insert(self):
        entries = _make_entries()
        # Delete seq 3-4, then insert after seq 2
        deleted = splice.delete_entries(entries, (3, 4))
        new = [_entry(0, text="Replacement")]
        result = splice.splice_entries(deleted, insert_after_seq=2, new_entries=new)
        body = [e for e in result if e["seq"] > 0]
        texts = [e["text"] for e in body]
        assert texts == ["ACT ONE", "Line one", "Replacement", "Line three"]


# ─── TestUpdateStats ───


class TestUpdateStats:
    def test_recomputes_counts(self):
        data = _make_parsed_data()
        splice.update_stats(data)
        stats = data["stats"]
        assert stats["total_entries"] == 5  # body entries only (seq > 0)
        assert stats["dialogue_lines"] == 3
        assert stats["direction_lines"] == 1
        assert "adam" in stats["speakers"]
        assert "tina" in stats["speakers"]
        assert "act1" in stats["sections"]

    def test_characters_for_tts(self):
        entries = [
            _entry(1, text="Hello world"),  # 11 chars
            _entry(2, text="Goodbye"),  # 7 chars
            _entry(3, type_="direction", text="BEAT"),  # not TTS
        ]
        data = _make_parsed_data(entries)
        splice.update_stats(data)
        assert data["stats"]["characters_for_tts"] == 18


# ─── TestDryRun (file I/O integration) ───


class TestDryRun:
    def test_dry_run_does_not_write(self, tmp_path):
        data = _make_parsed_data()
        target = tmp_path / "parsed.json"
        target.write_text(json.dumps(data))
        original_content = target.read_text()

        splice.run_splice(
            target_path=str(target),
            insert_after_seq=3,
            new_entries=[_entry(0, text="New")],
            dry_run=True,
        )
        assert target.read_text() == original_content
        # No backup file created
        backup = tmp_path / "pre_splice_parsed.json"
        assert not backup.exists()

    def test_write_creates_backup_and_updates(self, tmp_path):
        data = _make_parsed_data()
        target = tmp_path / "parsed.json"
        target.write_text(json.dumps(data))

        backup_path = str(tmp_path / "backup.json")
        splice.run_splice(
            target_path=str(target),
            insert_after_seq=3,
            new_entries=[_entry(0, text="New")],
            dry_run=False,
            backup_path=backup_path,
        )
        # Backup exists with original content
        assert os.path.exists(backup_path)
        with open(backup_path) as f:
            backup_data = json.load(f)
        assert len(backup_data["entries"]) == 7  # original count

        # Target updated with insertion
        with open(str(target)) as f:
            updated = json.load(f)
        assert len(updated["entries"]) == 8
        body_texts = [e["text"] for e in updated["entries"] if e["seq"] > 0]
        assert "New" in body_texts

    def test_no_backup_flag(self, tmp_path):
        data = _make_parsed_data()
        target = tmp_path / "parsed.json"
        target.write_text(json.dumps(data))

        splice.run_splice(
            target_path=str(target),
            insert_after_seq=3,
            new_entries=[_entry(0, text="New")],
            dry_run=False,
            backup_path=None,  # no backup
        )
        # Target updated, no backup
        with open(str(target)) as f:
            updated = json.load(f)
        assert len(updated["entries"]) == 8
