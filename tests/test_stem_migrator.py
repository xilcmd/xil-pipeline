# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP007_stem_migrator.py — stem migration tool."""



from xil_pipeline import XILP007_stem_migrator as m

# ── Helpers ────────────────────────────────────────────────────────────────────

def _dialogue(seq, section, scene, speaker, text):
    return {"seq": seq, "type": "dialogue", "section": section, "scene": scene,
            "speaker": speaker, "text": text}

def _direction(seq, section, scene, text):
    return {"seq": seq, "type": "direction", "section": section, "scene": scene,
            "speaker": None, "text": text}

def _header(seq, section):
    return {"seq": seq, "type": "section_header", "section": section,
            "scene": None, "speaker": None, "text": section.upper()}


# ── normalize_text ─────────────────────────────────────────────────────────────

class TestNormalizeText:
    def test_none_returns_empty(self):
        assert m.normalize_text(None) == ""

    def test_strips_whitespace(self):
        assert m.normalize_text("  hello  ") == "hello"

    def test_collapses_internal_spaces(self):
        assert m.normalize_text("hello   world") == "hello world"

    def test_fuzzy_emdash(self):
        assert m.normalize_text("a\u2014b") == "a - b"
        assert m.normalize_text("a \u2014 b") == "a - b"

    def test_fuzzy_ellipsis(self):
        assert m.normalize_text("wait\u2026") == "wait..."

    def test_fuzzy_curly_quotes(self):
        assert m.normalize_text("\u2018hi\u2019") == "'hi'"
        assert m.normalize_text("\u201chello\u201d") == '"hello"'

    def test_strict_preserves_emdash(self):
        result = m.normalize_text("a\u2014b", strict=True)
        assert "\u2014" in result

    def test_strict_preserves_ellipsis(self):
        result = m.normalize_text("wait\u2026", strict=True)
        assert "\u2026" in result


# ── make_stem_name ─────────────────────────────────────────────────────────────

class TestMakeStemName:
    def test_dialogue_no_scene(self):
        entry = _dialogue(5, "cold-open", None, "adam", "Some text")
        assert m.make_stem_name(entry) == "005_cold-open_adam.mp3"

    def test_dialogue_with_scene(self):
        entry = _dialogue(19, "act1", "scene-1", "maya", "Some text")
        assert m.make_stem_name(entry) == "019_act1-scene-1_maya.mp3"

    def test_direction_no_scene(self):
        entry = _direction(6, "cold-open", None, "BEAT")
        assert m.make_stem_name(entry) == "006_cold-open_sfx.mp3"

    def test_direction_with_scene(self):
        entry = _direction(18, "act1", "scene-1", "SFX: COFFEE POURED")
        assert m.make_stem_name(entry) == "018_act1-scene-1_sfx.mp3"

    def test_negative_seq_preamble(self):
        entry = {"seq": -2, "type": "dialogue", "section": "preamble",
                 "scene": None, "speaker": "tina", "text": "Hello"}
        assert m.make_stem_name(entry) == "n002_preamble_tina.mp3"

    def test_negative_seq_sfx(self):
        entry = {"seq": -1, "type": "direction", "section": "preamble",
                 "scene": None, "speaker": None, "text": "INTRO MUSIC"}
        assert m.make_stem_name(entry) == "n001_preamble_sfx.mp3"


# ── build_old_index ────────────────────────────────────────────────────────────

class TestBuildOldIndex:
    def test_dialogue_indexed_by_text_and_speaker(self, tmp_path):
        entry = _dialogue(5, "cold-open", None, "adam", "Hello world")
        stem = tmp_path / "005_cold-open_adam.mp3"
        stem.write_bytes(b"fake")
        exact, _ = m.build_old_index([entry], str(tmp_path))
        key = (m.normalize_text("Hello world"), "adam")
        assert key in exact
        assert exact[key]["exists"] is True

    def test_direction_indexed_as_sfx_role(self, tmp_path):
        entry = _direction(6, "cold-open", None, "BEAT")
        stem = tmp_path / "006_cold-open_sfx.mp3"
        stem.write_bytes(b"fake")
        exact, _ = m.build_old_index([entry], str(tmp_path))
        key = (m.normalize_text("BEAT"), "sfx")
        assert key in exact

    def test_text_only_index_populated(self, tmp_path):
        entry = _dialogue(5, "cold-open", None, "adam", "Hello world")
        (tmp_path / "005_cold-open_adam.mp3").write_bytes(b"fake")
        _, text_idx = m.build_old_index([entry], str(tmp_path))
        assert m.normalize_text("Hello world") in text_idx

    def test_missing_file_marked_not_exists(self, tmp_path):
        entry = _dialogue(5, "cold-open", None, "adam", "Hello world")
        # file NOT created
        exact, _ = m.build_old_index([entry], str(tmp_path))
        key = (m.normalize_text("Hello world"), "adam")
        assert exact[key]["exists"] is False

    def test_duplicate_text_keeps_first(self, tmp_path):
        e1 = _direction(6, "cold-open", None, "BEAT")
        e2 = _direction(10, "cold-open", None, "BEAT")
        (tmp_path / "006_cold-open_sfx.mp3").write_bytes(b"a")
        (tmp_path / "010_cold-open_sfx.mp3").write_bytes(b"b")
        exact, _ = m.build_old_index([e1, e2], str(tmp_path))
        key = (m.normalize_text("BEAT"), "sfx")
        assert exact[key]["entry"]["seq"] == 6

    def test_section_headers_skipped(self, tmp_path):
        entry = _header(1, "cold-open")
        exact, text_idx = m.build_old_index([entry], str(tmp_path))
        assert len(exact) == 0
        assert len(text_idx) == 0


# ── plan_migration ─────────────────────────────────────────────────────────────

class TestPlanMigration:
    def _make_stems(self, tmp_path, entries):
        """Write fake stem files for a list of entries."""
        for e in entries:
            if e.get("type") in m.STEM_TYPES:
                (tmp_path / m.make_stem_name(e)).write_bytes(b"fake")

    def test_unchanged_entry_gets_copy(self, tmp_path):
        old = [_dialogue(5, "cold-open", None, "adam", "Hello world")]
        new = [_dialogue(5, "cold-open", None, "adam", "Hello world")]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path))
        stem_actions = [a for a in actions if a.status != m.SKIP]
        assert len(stem_actions) == 1
        assert stem_actions[0].status == m.COPY

    def test_same_filename_copy_no_rename(self, tmp_path):
        """When old and new seq are identical the copy is a no-op rename."""
        old = [_dialogue(5, "cold-open", None, "adam", "Hello")]
        new = [_dialogue(5, "cold-open", None, "adam", "Hello")]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path))
        copy = [a for a in actions if a.status == m.COPY][0]
        assert copy.old_stem == copy.new_stem

    def test_seq_shift_produces_copy_with_different_names(self, tmp_path):
        """A deleted line before this one shifts its seq — should still COPY."""
        old = [_dialogue(20, "act1", "scene-1", "adam", "Hello")]
        new = [_dialogue(19, "act1", "scene-1", "adam", "Hello")]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path))
        copy = [a for a in actions if a.status == m.COPY][0]
        assert copy.old_stem == "020_act1-scene-1_adam.mp3"
        assert copy.new_stem == "019_act1-scene-1_adam.mp3"

    def test_changed_text_gets_new(self, tmp_path):
        old = [_dialogue(22, "act1", "scene-1", "adam", "I went to the quarry.")]
        new = [_dialogue(21, "act1", "scene-1", "adam", "I went to the station.")]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path))
        assert any(a.status == m.NEW for a in actions)

    def test_speaker_change_gets_speaker_status(self, tmp_path):
        old = [_dialogue(24, "act1", "scene-1", "sarah", "Elena wrapped her hands.")]
        new = [_dialogue(23, "act1", "scene-1", "adam", "Elena wrapped her hands.")]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path))
        assert any(a.status == m.SPEAKER for a in actions)

    def test_deleted_old_entry_not_in_new(self, tmp_path):
        """Old entry with no new counterpart should not appear in actions."""
        old = [
            _dialogue(5, "cold-open", None, "adam", "Line one"),
            _dialogue(7, "cold-open", None, "adam", "Line deleted"),
        ]
        new = [_dialogue(5, "cold-open", None, "adam", "Line one")]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path))
        stem_actions = [a for a in actions if a.status != m.SKIP]
        # Only the one new entry should appear — the deleted old entry is absent
        assert len(stem_actions) == 1
        assert stem_actions[0].status == m.COPY

    def test_new_entry_not_in_old(self, tmp_path):
        old = [_dialogue(5, "cold-open", None, "adam", "Hello")]
        new = [
            _dialogue(5, "cold-open", None, "adam", "Hello"),
            _dialogue(6, "cold-open", None, "adam", "Completely new line"),
        ]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path))
        assert any(a.status == m.NEW for a in actions)

    def test_missing_old_stem_file_gets_missing(self, tmp_path):
        old = [_dialogue(5, "cold-open", None, "adam", "Hello")]
        new = [_dialogue(5, "cold-open", None, "adam", "Hello")]
        # Do NOT create the stem file
        actions = m.plan_migration(old, new, str(tmp_path))
        assert any(a.status == m.MISSING for a in actions)

    def test_section_headers_get_skip(self, tmp_path):
        old = []
        new = [_header(1, "cold-open")]
        actions = m.plan_migration(old, new, str(tmp_path))
        assert all(a.status == m.SKIP for a in actions)

    def test_fuzzy_emdash_matches(self, tmp_path):
        """Em-dash formatting variant should still match in fuzzy mode."""
        old = [_dialogue(37, "act1", "scene-1", "adam", "hum of the diner around us\u2014the clink")]
        new = [_dialogue(35, "act1", "scene-1", "adam", "hum of the diner around us \u2014 the clink")]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path), strict=False)
        assert any(a.status == m.COPY for a in actions)

    def test_strict_emdash_does_not_match(self, tmp_path):
        """In strict mode, em-dash formatting difference should be treated as different."""
        old = [_dialogue(37, "act1", "scene-1", "adam", "hum\u2014the clink")]
        new = [_dialogue(35, "act1", "scene-1", "adam", "hum \u2014 the clink")]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path), strict=True)
        assert any(a.status == m.NEW for a in actions)

    def test_repeated_beats_each_get_own_action(self, tmp_path):
        """Multiple BEAT entries — first reuses old stem, subsequent are NEW."""
        old = [_direction(6, "cold-open", None, "BEAT")]
        new = [
            _direction(6, "cold-open", None, "BEAT"),
            _direction(10, "cold-open", None, "BEAT"),
        ]
        self._make_stems(tmp_path, old)
        actions = m.plan_migration(old, new, str(tmp_path))
        stem_acts = [a for a in actions if a.status != m.SKIP]
        statuses = {a.status for a in stem_acts}
        assert m.COPY in statuses
        assert m.NEW in statuses


# ── execute_migration ──────────────────────────────────────────────────────────

class TestExecuteMigration:
    def test_dry_run_does_not_copy(self, tmp_path):
        src = tmp_path / "005_cold-open_adam.mp3"
        src.write_bytes(b"audio")
        action = m.MigrationAction(
            status=m.COPY, new_seq=4, new_stem="004_cold-open_adam.mp3",
            old_seq=5, old_stem="005_cold-open_adam.mp3",
        )
        m.execute_migration([action], str(tmp_path), dry_run=True)
        assert not (tmp_path / "004_cold-open_adam.mp3").exists()

    def test_real_copy_creates_file(self, tmp_path):
        src = tmp_path / "005_cold-open_adam.mp3"
        src.write_bytes(b"audio")
        action = m.MigrationAction(
            status=m.COPY, new_seq=4, new_stem="004_cold-open_adam.mp3",
            old_seq=5, old_stem="005_cold-open_adam.mp3",
        )
        m.execute_migration([action], str(tmp_path), dry_run=False)
        dst = tmp_path / "004_cold-open_adam.mp3"
        assert dst.exists()
        assert dst.read_bytes() == b"audio"

    def test_same_filename_no_error(self, tmp_path):
        """COPY where old == new stem should be a no-op, not error."""
        src = tmp_path / "005_cold-open_adam.mp3"
        src.write_bytes(b"audio")
        action = m.MigrationAction(
            status=m.COPY, new_seq=5, new_stem="005_cold-open_adam.mp3",
            old_seq=5, old_stem="005_cold-open_adam.mp3",
        )
        counts = m.execute_migration([action], str(tmp_path), dry_run=False)
        assert counts[m.COPY] == 1
        assert src.read_bytes() == b"audio"

    def test_counts_all_statuses(self, tmp_path):
        actions = [
            m.MigrationAction(status=m.COPY, new_seq=1, new_stem="a.mp3",
                              old_seq=2, old_stem="a.mp3"),
            m.MigrationAction(status=m.NEW, new_seq=3, new_stem="b.mp3"),
            m.MigrationAction(status=m.SPEAKER, new_seq=4, new_stem="c.mp3"),
            m.MigrationAction(status=m.MISSING, new_seq=5, new_stem="d.mp3"),
            m.MigrationAction(status=m.SKIP, new_seq=6, new_stem=""),
        ]
        counts = m.execute_migration(actions, str(tmp_path), dry_run=True)
        assert counts[m.COPY] == 1
        assert counts[m.NEW] == 1
        assert counts[m.SPEAKER] == 1
        assert counts[m.MISSING] == 1
        assert counts[m.SKIP] == 1
