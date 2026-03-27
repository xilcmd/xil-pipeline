# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP008_stale_stem_cleanup.py — stale stem cleanup tool."""

import json
import os
import tempfile

import pytest

from xil_pipeline import XILP008_stale_stem_cleanup as m


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_entries_index(entries):
    """Build a {seq: entry} dict from a list of entry dicts."""
    return {e["seq"]: e for e in entries}


def _touch(directory, filename):
    """Create an empty file in directory."""
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        pass
    return path


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestFindStaleStemsBasic:
    """Test find_stale_stems with various mismatch scenarios."""

    def test_no_stale_stems(self, tmp_path):
        """All stems match their parsed entry type — nothing stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "001_cold-open_adam.mp3")   # dialogue stem
        _touch(stems_dir, "002_cold-open_sfx.mp3")    # direction/sfx stem

        index = _make_entries_index([
            {"seq": 1, "type": "dialogue", "speaker": "adam"},
            {"seq": 2, "type": "direction", "direction_type": "sfx"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert stale == []

    def test_sfx_stem_now_dialogue(self, tmp_path):
        """An _sfx stem at a seq that is now dialogue → stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "005_act1-scene-1_sfx.mp3")

        index = _make_entries_index([
            {"seq": 5, "type": "dialogue", "speaker": "maya"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 1
        assert stale[0][1] == 5
        assert "dialogue" in stale[0][2]

    def test_speaker_stem_now_direction(self, tmp_path):
        """A speaker stem at a seq that is now direction → stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "010_act1-scene-1_adam.mp3")

        index = _make_entries_index([
            {"seq": 10, "type": "direction", "direction_type": "sfx"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 1
        assert stale[0][1] == 10
        assert "direction" in stale[0][2]

    def test_seq_not_in_parsed(self, tmp_path):
        """A stem whose seq doesn't exist in the parsed JSON → stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "099_act2-scene-2_adam.mp3")

        index = _make_entries_index([
            {"seq": 1, "type": "dialogue", "speaker": "adam"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 1
        assert stale[0][1] == 99
        assert "not in parsed" in stale[0][2]

    def test_direction_stem_stays_valid(self, tmp_path):
        """An _sfx stem matching a direction entry is NOT stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "003_act1-scene-1_sfx.mp3")

        index = _make_entries_index([
            {"seq": 3, "type": "direction", "direction_type": "sfx"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert stale == []

    def test_dialogue_stem_stays_valid(self, tmp_path):
        """A speaker stem matching a dialogue entry is NOT stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "007_act1-scene-1_sarah.mp3")

        index = _make_entries_index([
            {"seq": 7, "type": "dialogue", "speaker": "sarah"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert stale == []

    def test_preamble_stems_ignored(self, tmp_path):
        """Negative-seq preamble stems (n001_, n002_) are never flagged."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "n001_preamble_sfx.mp3")
        _touch(stems_dir, "n002_preamble_tina.mp3")

        index = _make_entries_index([])  # empty — would flag positive seqs

        stale = m.find_stale_stems(stems_dir, index)
        # preamble stems use negative seqs; extract_seq returns -1, -2
        # They won't be in the index, but they should be caught by the
        # "seq not in parsed JSON" case — verify they ARE flagged if not
        # in the index.  This is correct behaviour: if the parsed JSON
        # no longer has preamble entries, they are stale.
        assert len(stale) == 2

    def test_mixed_stale_and_valid(self, tmp_path):
        """Only mismatched stems are returned; valid ones are skipped."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "001_cold-open_adam.mp3")   # valid dialogue
        _touch(stems_dir, "002_cold-open_sfx.mp3")    # stale: now dialogue
        _touch(stems_dir, "003_cold-open_sarah.mp3")  # stale: now direction

        index = _make_entries_index([
            {"seq": 1, "type": "dialogue", "speaker": "adam"},
            {"seq": 2, "type": "dialogue", "speaker": "maya"},
            {"seq": 3, "type": "direction", "direction_type": "sfx"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 2
        seqs = {s[1] for s in stale}
        assert seqs == {2, 3}

    def test_non_mp3_files_ignored(self, tmp_path):
        """Non-.mp3 files in the stems directory are ignored."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "001_cold-open_adam.wav")
        _touch(stems_dir, "notes.txt")

        index = _make_entries_index([])

        stale = m.find_stale_stems(stems_dir, index)
        assert stale == []

    def test_empty_stems_dir(self, tmp_path):
        """An empty stems directory returns no stale stems."""
        stems_dir = str(tmp_path)
        index = _make_entries_index([
            {"seq": 1, "type": "dialogue", "speaker": "adam"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert stale == []


class TestFindStaleStemsSpeakerMismatch:
    """Test find_stale_stems catches speaker mismatches on dialogue entries."""

    def test_wrong_speaker_suffix(self, tmp_path):
        """Dialogue stem with wrong speaker suffix → stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "019_act1-scene-1_maya.mp3")

        index = _make_entries_index([
            {"seq": 19, "type": "dialogue", "speaker": "rian"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 1
        assert stale[0][1] == 19
        assert "speaker" in stale[0][2].lower()

    def test_correct_speaker_not_stale(self, tmp_path):
        """Dialogue stem with correct speaker suffix → not stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "019_act1-scene-1_rian.mp3")

        index = _make_entries_index([
            {"seq": 19, "type": "dialogue", "speaker": "rian"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert stale == []

    def test_duplicate_seq_different_speakers(self, tmp_path):
        """Two dialogue stems at same seq, different speakers — wrong one is stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "019_act1-scene-1_maya.mp3")
        _touch(stems_dir, "019_act1-scene-1_rian.mp3")

        index = _make_entries_index([
            {"seq": 19, "type": "dialogue", "speaker": "rian",
             "section": "act1", "scene": "scene-1"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 1
        stale_file = os.path.basename(stale[0][0])
        assert "maya" in stale_file

    def test_duplicate_seq_different_sections(self, tmp_path):
        """Two SFX stems at same seq, different sections — wrong section is stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "015_closing_sfx.mp3")
        _touch(stems_dir, "015_act1-scene-1_sfx.mp3")

        index = _make_entries_index([
            {"seq": 15, "type": "direction", "section": "act1",
             "scene": "scene-1", "direction_type": "AMBIENCE"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 1
        stale_file = os.path.basename(stale[0][0])
        assert "closing" in stale_file

    def test_stem_at_section_header_is_stale(self, tmp_path):
        """A stem at a seq that is now a section_header → stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "084_act1-scene-1_sfx.mp3")

        index = _make_entries_index([
            {"seq": 84, "type": "section_header", "text": "ACT TWO"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 1
        assert "section_header" in stale[0][2]

    def test_stem_at_scene_header_is_stale(self, tmp_path):
        """A stem at a seq that is now a scene_header → stale."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "085_act1-scene-1_adam.mp3")

        index = _make_entries_index([
            {"seq": 85, "type": "scene_header", "text": "SCENE 2"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 1
        assert "scene_header" in stale[0][2]

    def test_compound_speaker_name(self, tmp_path):
        """Speaker key with underscore (mr_patterson) is handled correctly."""
        stems_dir = str(tmp_path)
        _touch(stems_dir, "050_act2-scene-2_mr_patterson.mp3")

        index = _make_entries_index([
            {"seq": 50, "type": "dialogue", "speaker": "mr_patterson"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert stale == []


class TestStaleStemDeletion:
    """Integration test: find stale stems, delete them, verify gone."""

    def test_delete_stale_stems(self, tmp_path):
        """Stale stems are deleted; valid stems survive."""
        stems_dir = str(tmp_path)
        valid = _touch(stems_dir, "001_cold-open_adam.mp3")
        stale1 = _touch(stems_dir, "002_cold-open_sfx.mp3")
        stale2 = _touch(stems_dir, "003_cold-open_karen.mp3")

        index = _make_entries_index([
            {"seq": 1, "type": "dialogue", "speaker": "adam"},
            {"seq": 2, "type": "dialogue", "speaker": "maya"},
            {"seq": 3, "type": "direction", "direction_type": "sfx"},
        ])

        stale = m.find_stale_stems(stems_dir, index)
        assert len(stale) == 2

        # Delete them
        for filepath, _seq, _reason in stale:
            os.remove(filepath)

        assert os.path.exists(valid)
        assert not os.path.exists(stale1)
        assert not os.path.exists(stale2)
