# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for xil_gui helper functions (no Gradio dependency required)."""

import json
import logging
import os

import pytest

import xil_pipeline.xil_gui as xil_gui
from xil_pipeline.xil_gui import (
    _analyze_script_header,
    _ep_choice,
    _ep_meta,
    _parse_choice,
    _read_sfx_grade,
    _refresh_episodes,
    _sanitize_extra_flags,
    _save_script_file,
    _scan_sfx_grades,
    _sfx_choices,
    _sfx_grade_cache,
    _stage_status,
    _write_sfx_grade,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_cast(root, slug, tag, title="", season_title=""):
    cfg_dir = root / "configs" / slug
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"title": title, "season_title": season_title, "cast": {}}
    (cfg_dir / f"cast_{tag}.json").write_text(json.dumps(cfg))


def _write_parsed(root, slug, tag):
    parsed_dir = root / "parsed" / slug
    parsed_dir.mkdir(parents=True, exist_ok=True)
    data = {"show": "THE 413", "entries": [], "stats": {}}
    (parsed_dir / f"parsed_{tag}.json").write_text(json.dumps(data))


# ── _sanitize_extra_flags ─────────────────────────────────────────────────────

class TestSanitizeExtraFlags:
    def test_empty_string_returns_empty_list(self):
        assert _sanitize_extra_flags("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _sanitize_extra_flags("   ") == []

    def test_single_flag(self):
        assert _sanitize_extra_flags("--dry-run") == ["--dry-run"]

    def test_flag_with_value(self):
        assert _sanitize_extra_flags("--gap-ms 600") == ["--gap-ms", "600"]

    def test_multiple_flags(self):
        result = _sanitize_extra_flags("--dry-run --gap-ms 400")
        assert result == ["--dry-run", "--gap-ms", "400"]

    def test_quoted_path_with_spaces(self):
        result = _sanitize_extra_flags('"scripts/my script.md"')
        assert result == ["scripts/my script.md"]

    def test_plain_path(self):
        result = _sanitize_extra_flags("scripts/sample_S01E01.md")
        assert result == ["scripts/sample_S01E01.md"]

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--dry-run; rm -rf /")

    def test_rejects_pipe(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--output /dev/stdout | cat")

    def test_rejects_ampersand(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--episode S01E01 && evil")

    def test_rejects_backtick(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--show `id`")

    def test_rejects_dollar_sign(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--show $SHELL")

    def test_rejects_subshell_parens(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--show $(whoami)")

    def test_rejects_redirect(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--output > /etc/passwd")

    def test_rejects_unbalanced_quote(self):
        with pytest.raises(ValueError, match="Invalid flag syntax"):
            _sanitize_extra_flags("--output 'unclosed")

    def test_flag_with_equals_value(self):
        result = _sanitize_extra_flags("--output=masters/ep.mp3")
        assert result == ["--output=masters/ep.mp3"]

    def test_numeric_value(self):
        result = _sanitize_extra_flags("--start-from 5 --stop-at 10")
        assert result == ["--start-from", "5", "--stop-at", "10"]


# ── _parse_choice ─────────────────────────────────────────────────────────────

class TestParseChoice:
    def test_slug_and_tag_extracted(self):
        assert _parse_choice("the413  S03E03") == ("the413", "S03E03")

    def test_extra_display_label_ignored(self):
        result = _parse_choice("the413  S03E03  [The Architect]  —  The Covered Bridge")
        assert result == ("the413", "S03E03")

    def test_single_part_returns_slug_no_tag(self):
        # Single-token choice is a show stub: slug is preserved, tag is empty
        assert _parse_choice("the413") == ("the413", "")

    def test_empty_string_returns_empty_pair(self):
        assert _parse_choice("") == ("", "")

    def test_leading_and_trailing_whitespace_ignored(self):
        assert _parse_choice("  the413  S01E01  ") == ("the413", "S01E01")


# ── _analyze_script_header ────────────────────────────────────────────────────

class TestAnalyzeScriptHeader:
    def test_full_header_all_fields(self):
        text = 'THE 413 Season 3: Episode 3: "The Covered Bridge" Arc: "The Architect"\n\nCAST:'
        show, season, episode, title, arc, filename = _analyze_script_header(text)
        assert show == "THE 413"
        assert season == "3"
        assert episode == "3"
        assert title == "The Covered Bridge"
        assert arc == "The Architect"
        assert filename == "S03E03_the413_The_Covered_Bridge_v1.md"

    def test_episode_zero_teaser(self):
        text = 'THE 413 Season 6: Episode 0: "Teaser — The Berkshire Ghost Train" Arc: "The Berkshire Ghost Train"'
        show, season, episode, title, arc, filename = _analyze_script_header(text)
        assert show == "THE 413"
        assert season == "6"
        assert episode == "0"
        assert filename == "S06E00_the413_Teaser_The_Berkshire_Ghost_Train_v1.md"

    def test_no_arc_title_returns_empty_arc(self):
        text = 'THE 413 Season 1: Episode 2: "The Return"'
        show, season, episode, title, arc, filename = _analyze_script_header(text)
        assert arc == ""
        assert show == "THE 413"
        assert season == "1"
        assert episode == "2"
        assert title == "The Return"

    def test_unrecognized_header_sets_warning_in_filename(self):
        show, season, episode, title, arc, filename = _analyze_script_header("no episode marker here")
        assert show == ""
        assert season == ""
        assert episode == ""
        assert filename.startswith("⚠️")

    def test_empty_text_returns_all_empty(self):
        result = _analyze_script_header("")
        assert all(v == "" for v in result)

    def test_blank_lines_before_header_are_skipped(self):
        text = "\n\n  \nTHE 413 Season 2: Episode 1: \"The Letters\" Arc: \"The Letters\"\n"
        show, _, _, _, _, _ = _analyze_script_header(text)
        assert show == "THE 413"

    def test_filename_always_has_v1_suffix(self):
        text = 'THE 413 Season 5: Episode 1: "October Mountain" Arc: "The Witness"'
        _, _, _, _, _, filename = _analyze_script_header(text)
        assert filename.endswith("_v1.md")

    def test_special_chars_in_title_sanitized(self):
        text = 'THE 413 Season 5: Episode 1: "Teaser — The Berkshire Ghost Train" Arc: "The Witness"'
        _, _, _, _, _, filename = _analyze_script_header(text)
        assert "/" not in filename
        assert " " not in filename
        assert "—" not in filename

    def test_season_zero_padded_to_two_digits(self):
        text = 'THE 413 Season 1: Episode 2: "Something"'
        _, _, _, _, _, filename = _analyze_script_header(text)
        assert filename.startswith("S01E02_")

    def test_show_slug_lowercased_in_filename(self):
        text = 'THE 413 Season 1: Episode 1: "The Empty Booth" Arc: "The Holiday Shift"'
        _, _, _, _, _, filename = _analyze_script_header(text)
        assert "the413" in filename
        assert "THE413" not in filename


# ── _save_script_file ────────────────────────────────────────────────────────

class TestSaveScriptFile:
    def test_saves_file_and_returns_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        result = _save_script_file("script content", "S01E01_test_v1.md")
        assert result.startswith("✅")
        assert (tmp_path / "scripts" / "S01E01_test_v1.md").read_text() == "script content"

    def test_creates_scripts_dir_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        assert not (tmp_path / "scripts").exists()
        _save_script_file("content", "S01E01_test_v1.md")
        assert (tmp_path / "scripts").is_dir()

    def test_refuses_overwrite_and_preserves_original(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _save_script_file("first version", "S01E01_test_v1.md")
        result = _save_script_file("second version", "S01E01_test_v1.md")
        assert result.startswith("⚠️ Already exists")
        assert (tmp_path / "scripts" / "S01E01_test_v1.md").read_text() == "first version"

    def test_appends_md_extension_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        result = _save_script_file("content", "S01E01_no_ext")
        assert result == "✅ Saved: scripts/S01E01_no_ext.md"
        assert (tmp_path / "scripts" / "S01E01_no_ext.md").exists()

    def test_empty_content_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        result = _save_script_file("   ", "S01E01_test_v1.md")
        assert result.startswith("⚠️ No script content")
        assert not (tmp_path / "scripts" / "S01E01_test_v1.md").exists()

    def test_empty_filename_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        result = _save_script_file("content", "")
        assert result.startswith("⚠️ Filename is empty")

    def test_whitespace_filename_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        result = _save_script_file("content", "   ")
        assert result.startswith("⚠️ Filename is empty")

    def test_v2_file_saves_after_v1_refused(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _save_script_file("v1 content", "S01E01_test_v1.md")
        result = _save_script_file("v2 content", "S01E01_test_v2.md")
        assert result.startswith("✅")
        assert (tmp_path / "scripts" / "S01E01_test_v2.md").exists()


# ── _ep_meta ──────────────────────────────────────────────────────────────────

class TestEpMeta:
    def test_reads_title_and_season_title(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S03E03", "The Covered Bridge", "The Architect")
        title, season_title = _ep_meta("the413", "S03E03")
        assert title == "The Covered Bridge"
        assert season_title == "The Architect"

    def test_missing_config_returns_empty_pair(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        assert _ep_meta("the413", "S99E99") == ("", "")

    def test_config_missing_fields_returns_empty_strings(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        cfg_dir = tmp_path / "configs" / "the413"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "cast_S01E01.json").write_text(json.dumps({"cast": {}}))
        title, season_title = _ep_meta("the413", "S01E01")
        assert title == ""
        assert season_title == ""

    def test_malformed_json_returns_empty_pair(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        cfg_dir = tmp_path / "configs" / "the413"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "cast_S01E01.json").write_text("not json {{{")
        assert _ep_meta("the413", "S01E01") == ("", "")


# ── _ep_choice ────────────────────────────────────────────────────────────────

class TestEpChoice:
    def test_title_and_arc(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S03E03", "The Covered Bridge", "The Architect")
        assert _ep_choice("the413", "S03E03") == "the413  S03E03  [The Architect]  —  The Covered Bridge"

    def test_arc_only_no_em_dash(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S03E03", season_title="The Architect")
        label = _ep_choice("the413", "S03E03")
        assert "[The Architect]" in label
        assert "—" not in label

    def test_title_only_no_brackets(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S03E03", title="The Covered Bridge")
        label = _ep_choice("the413", "S03E03")
        assert "The Covered Bridge" in label
        assert "[" not in label

    def test_no_metadata_returns_slug_and_tag_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S03E03")
        assert _ep_choice("the413", "S03E03") == "the413  S03E03"

    def test_missing_config_returns_slug_and_tag_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        assert _ep_choice("the413", "S99E99") == "the413  S99E99"


# ── _refresh_episodes ─────────────────────────────────────────────────────────

class TestRefreshEpisodes:
    @pytest.fixture(autouse=True)
    def _isolate_cache(self):
        xil_gui._EPISODES_CACHE.clear()
        yield
        xil_gui._EPISODES_CACHE.clear()

    def test_empty_workspace_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        assert _refresh_episodes() == []

    def test_single_episode_row_structure(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S01E01", "The Empty Booth", "The Holiday Shift")
        rows = _refresh_episodes()
        assert len(rows) == 1
        tag, slug, desc, parse, produce, daw, master, overall = rows[0]
        assert tag == "S01E01"
        assert slug == "the413"
        assert "The Empty Booth" in desc
        assert "The Holiday Shift" in desc

    def test_multiple_episodes_sorted_by_slug_then_tag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        for t in ("S03E01", "S01E01", "S02E01"):
            _write_cast(tmp_path, "the413", t)
        tags = [row[0] for row in _refresh_episodes()]
        assert tags == ["S01E01", "S02E01", "S03E01"]

    def test_unparsed_episode_shows_open_circle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S01E01")
        rows = _refresh_episodes()
        assert rows[0][3] == "○"   # parse column

    def test_parsed_episode_shows_checkmark(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S01E01")
        _write_parsed(tmp_path, "the413", "S01E01")
        rows = _refresh_episodes()
        assert rows[0][3] == "✓"   # parse column

    def test_title_arc_combined_in_desc_column(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S01E01", "The Empty Booth", "The Holiday Shift")
        desc = _refresh_episodes()[0][2]
        assert "[The Holiday Shift]" in desc
        assert "The Empty Booth" in desc


class TestRefreshEpisodesCache:
    """Rows are memoised per workspace root so every browser connect
    (demo.load fires _refresh_episodes) doesn't re-stat the whole workspace —
    prohibitive when XIL_PROJECTROOT is a NAS mount."""

    @pytest.fixture(autouse=True)
    def _isolate_cache(self):
        xil_gui._EPISODES_CACHE.clear()
        yield
        xil_gui._EPISODES_CACHE.clear()

    def _count_stage_status(self, monkeypatch):
        calls = []
        real = xil_gui._stage_status

        def counting(slug, tag):
            calls.append((slug, tag))
            return real(slug, tag)

        monkeypatch.setattr(xil_gui, "_stage_status", counting)
        return calls

    def test_second_call_within_ttl_returns_cached_rows(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S01E01")
        calls = self._count_stage_status(monkeypatch)
        first = _refresh_episodes()
        assert len(calls) == 1
        second = _refresh_episodes()
        assert len(calls) == 1          # no rescan
        assert second == first

    def test_force_rescans(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S01E01")
        calls = self._count_stage_status(monkeypatch)
        _refresh_episodes()
        _refresh_episodes(force=True)
        assert len(calls) == 2

    def test_force_result_replaces_cache(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S01E01")
        _refresh_episodes()
        _write_cast(tmp_path, "the413", "S01E02")
        rows = _refresh_episodes(force=True)
        assert len(rows) == 2
        assert len(_refresh_episodes()) == 2   # cached copy is the fresh one

    def test_expired_ttl_rescans(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _write_cast(tmp_path, "the413", "S01E01")
        calls = self._count_stage_status(monkeypatch)
        _refresh_episodes()
        root = str(xil_gui.get_workspace_root())
        ts, rows = xil_gui._EPISODES_CACHE[root]
        xil_gui._EPISODES_CACHE[root] = (ts - xil_gui._EPISODES_TTL_S - 1, rows)
        _refresh_episodes()
        assert len(calls) == 2

    def test_empty_workspace_result_is_cached_too(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        assert _refresh_episodes() == []
        assert str(xil_gui.get_workspace_root()) in xil_gui._EPISODES_CACHE

    def test_different_workspace_roots_are_separate_entries(self, tmp_path, monkeypatch):
        ws_a = tmp_path / "a"
        ws_b = tmp_path / "b"
        monkeypatch.setenv("XIL_PROJECTROOT", str(ws_a))
        _write_cast(ws_a, "the413", "S01E01")
        rows_a = _refresh_episodes()
        monkeypatch.setenv("XIL_PROJECTROOT", str(ws_b))
        _write_cast(ws_b, "other", "S02E02")
        rows_b = _refresh_episodes()
        assert rows_a != rows_b
        assert rows_b[0][0] == "S02E02"


# ── _stage_status (staleness, via the xil-status engine) ───────────────────────


def _touch(path, mtime):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _scaffold_fresh(root, slug, tag, *, base=1000, stems=1):
    """Create a fully fresh parse→master chain with ascending mtimes."""
    _touch(root / "scripts" / f"{tag}_{slug}_v1.md", base + 1)
    _touch(root / "parsed" / slug / f"parsed_{tag}.json", base + 2)
    for i in range(stems):
        _touch(root / "stems" / slug / tag / f"{i + 1:03d}_intro_host.mp3", base + 3)
    _touch(root / "stems" / slug / tag / f"{tag}_stem_manifest.json", base + 3)
    _touch(root / "daw" / slug / tag / f"{tag}_layer_dialogue.wav", base + 4)
    _touch(root / "masters" / f"{tag}_{slug}_2026-06-19.mp3", base + 5)


class TestStageStatus:
    def test_fresh_chain_all_checkmarks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _scaffold_fresh(tmp_path, "the413", "S01E01")
        st = _stage_status("the413", "S01E01")
        assert st["parse"] == "✓"
        assert st["produce"].startswith("✓")
        assert st["daw"] == "✓"
        assert st["master"] == "✓"
        assert st["overall"] == "✓ OK"

    def test_produce_shows_stem_count(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _scaffold_fresh(tmp_path, "the413", "S01E01", stems=3)
        st = _stage_status("the413", "S01E01")
        assert st["produce"] == "✓ 3"

    def test_reparse_makes_stems_stale(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _scaffold_fresh(tmp_path, "the413", "S01E01")
        # parsed re-run after the stems/manifest were produced
        os.utime(tmp_path / "parsed" / "the413" / "parsed_S01E01.json", (9000, 9000))
        st = _stage_status("the413", "S01E01")
        assert st["produce"].startswith("⚠")
        assert st["overall"] == "⚠ stale"

    def test_missing_master_open_circle(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        _scaffold_fresh(tmp_path, "the413", "S01E01")
        (tmp_path / "masters" / "S01E01_the413_2026-06-19.mp3").unlink()
        st = _stage_status("the413", "S01E01")
        assert st["master"] == "○"
        assert st["overall"] == "○ missing"


# ─── Audio Grading (SFX library grades stored in ID3) ───

def _make_mp3(path, ms=120):
    from pydub import AudioSegment
    path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.silent(duration=ms).export(str(path), format="mp3")


class TestSfxGradeTag:
    def test_round_trips_accurate_and_rejected(self, tmp_path):
        mp3 = tmp_path / "a.mp3"
        _make_mp3(mp3)
        assert _read_sfx_grade(str(mp3)) == ""          # ungraded by default
        _write_sfx_grade(str(mp3), "accurate")
        assert _read_sfx_grade(str(mp3)) == "accurate"
        _write_sfx_grade(str(mp3), "rejected")
        assert _read_sfx_grade(str(mp3)) == "rejected"

    def test_clear_grade(self, tmp_path):
        mp3 = tmp_path / "a.mp3"
        _make_mp3(mp3)
        _write_sfx_grade(str(mp3), "accurate")
        _write_sfx_grade(str(mp3), "")
        assert _read_sfx_grade(str(mp3)) == ""

    def test_grade_preserves_other_id3_tags(self, tmp_path):
        from mutagen.id3 import ID3

        from xil_pipeline.sfx_common import tag_mp3
        mp3 = tmp_path / "a.mp3"
        _make_mp3(mp3)
        tag_mp3(str(mp3), show="THE 413", title="Door creak", comments="elevenlabs")
        _write_sfx_grade(str(mp3), "rejected")
        assert _read_sfx_grade(str(mp3)) == "rejected"
        tags = ID3(str(mp3))
        assert str(tags.get("TIT2").text[0]) == "Door creak"      # title intact
        assert str(tags.get("TALB").text[0]) == "THE 413"          # album intact
        assert tags.get("COMM::eng") is not None                   # comment intact

    def test_untagged_mp3_reads_ungraded_then_gradeable(self, tmp_path):
        mp3 = tmp_path / "fresh.mp3"
        _make_mp3(mp3)            # no ID3 header written
        assert _read_sfx_grade(str(mp3)) == ""
        _write_sfx_grade(str(mp3), "accurate")
        assert _read_sfx_grade(str(mp3)) == "accurate"


class TestSfxChoicesAndScan:
    def _seed_cache(self, mapping):
        _sfx_grade_cache.clear()
        _sfx_grade_cache.update(mapping)

    def test_glyph_prefix_per_state(self):
        self._seed_cache({
            "/SFX/ok.mp3": "accurate",
            "/SFX/no.mp3": "rejected",
            "/SFX/maybe.mp3": "",
        })
        labels = dict((lbl.split("  ", 1)[1], lbl[0]) for lbl, _ in _sfx_choices("all"))
        assert labels["ok.mp3"] == "✓"
        assert labels["no.mp3"] == "✗"
        assert labels["maybe.mp3"] == "•"

    def test_filters_select_subset(self):
        self._seed_cache({
            "/SFX/ok.mp3": "accurate",
            "/SFX/no.mp3": "rejected",
            "/SFX/maybe.mp3": "",
        })
        assert [p for _, p in _sfx_choices("ungraded")] == ["/SFX/maybe.mp3"]
        assert [p for _, p in _sfx_choices("accurate")] == ["/SFX/ok.mp3"]
        assert [p for _, p in _sfx_choices("rejected")] == ["/SFX/no.mp3"]
        assert len(_sfx_choices("all")) == 3

    def test_scan_includes_hierarchical_show_subdir_files(self, tmp_path, monkeypatch):
        """SFX/{slug}/ files (PR #23 hierarchical layout) must be found
        alongside the flat shared pool, not silently skipped."""
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "shared.mp3")
        _make_mp3(sfx / "myshow" / "beat.mp3")
        cache = _scan_sfx_grades()
        assert str(sfx / "shared.mp3") in cache
        assert str(sfx / "myshow" / "beat.mp3") in cache
        assert len(cache) == 2

    def test_choices_label_disambiguates_same_named_files_across_shows(self, tmp_path, monkeypatch):
        """Two shows can legitimately use the same filename (confirmed in the
        real library: beat.mp3, intro-music.mp3) — the dropdown label must
        show which show each one is, or they're indistinguishable."""
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "showa" / "beat.mp3")
        _make_mp3(sfx / "showb" / "beat.mp3")
        _scan_sfx_grades()
        labels = [lbl for lbl, _ in _sfx_choices("all")]
        assert any("showa" in lbl and "beat.mp3" in lbl for lbl in labels)
        assert any("showb" in lbl and "beat.mp3" in lbl for lbl in labels)
        assert labels[0] != labels[1]

    def test_choices_label_unchanged_for_flat_pool_synthetic_paths(self):
        """Pre-existing behavior for the flat pool (and for any cache entries
        that aren't actually nested under the real SFX root) must not gain a
        spurious [..] prefix from relpath on unrelated paths."""
        self._seed_cache({"/SFX/ok.mp3": "accurate"})
        labels = dict((lbl.split("  ", 1)[1], lbl[0]) for lbl, _ in _sfx_choices("all"))
        assert "ok.mp3" in labels

    def test_scan_populates_cache_from_disk(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "one.mp3")
        _make_mp3(sfx / "two.mp3")
        _write_sfx_grade(str(sfx / "one.mp3"), "accurate")
        cache = _scan_sfx_grades()
        assert cache[str(sfx / "one.mp3")] == "accurate"
        assert cache[str(sfx / "two.mp3")] == ""
        assert len(cache) == 2


class TestSfxGradeCachePersistence:
    """Grades are memoised in SFX/.xil_grade_cache.json keyed by (size, mtime)
    so a rescan is one directory walk instead of ~842 serial ID3 reads —
    prohibitive when XIL_PROJECTROOT is a NAS mount."""

    def _count_id3_reads(self, monkeypatch):
        calls = []
        real = xil_gui._read_sfx_grade

        def counting(path):
            calls.append(path)
            return real(path)

        monkeypatch.setattr(xil_gui, "_read_sfx_grade", counting)
        return calls

    def _cache_file(self, tmp_path):
        return tmp_path / "SFX" / ".xil_grade_cache.json"

    def test_first_scan_writes_cache_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "one.mp3")
        _make_mp3(sfx / "myshow" / "beat.mp3")
        _write_sfx_grade(str(sfx / "one.mp3"), "accurate")
        _scan_sfx_grades()
        data = json.loads(self._cache_file(tmp_path).read_text())
        assert data["version"] == 1
        assert data["files"]["one.mp3"]["grade"] == "accurate"
        assert data["files"][os.path.join("myshow", "beat.mp3")]["grade"] == ""

    def test_second_scan_does_zero_id3_reads(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "one.mp3")
        _make_mp3(sfx / "two.mp3")
        _write_sfx_grade(str(sfx / "one.mp3"), "rejected")
        _scan_sfx_grades()
        calls = self._count_id3_reads(monkeypatch)
        cache = _scan_sfx_grades()
        assert calls == []
        assert cache[str(sfx / "one.mp3")] == "rejected"
        assert cache[str(sfx / "two.mp3")] == ""

    def test_modified_file_is_the_only_one_reread(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "one.mp3")
        _make_mp3(sfx / "two.mp3")
        _scan_sfx_grades()
        os.utime(sfx / "two.mp3", (12345, 12345))
        calls = self._count_id3_reads(monkeypatch)
        _scan_sfx_grades()
        assert calls == [str(sfx / "two.mp3")]

    def test_deleted_file_dropped_from_cache_and_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "one.mp3")
        _make_mp3(sfx / "two.mp3")
        _scan_sfx_grades()
        (sfx / "two.mp3").unlink()
        cache = _scan_sfx_grades()
        assert str(sfx / "two.mp3") not in cache
        data = json.loads(self._cache_file(tmp_path).read_text())
        assert "two.mp3" not in data["files"]

    def test_corrupt_cache_file_falls_back_to_full_rescan(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "one.mp3")
        _write_sfx_grade(str(sfx / "one.mp3"), "accurate")
        self._cache_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        self._cache_file(tmp_path).write_text("{not json")
        cache = _scan_sfx_grades()
        assert cache[str(sfx / "one.mp3")] == "accurate"
        # and the corrupt file was replaced with a valid one
        data = json.loads(self._cache_file(tmp_path).read_text())
        assert data["files"]["one.mp3"]["grade"] == "accurate"

    def test_update_entry_after_grade_write_avoids_reread(self, tmp_path, monkeypatch):
        """_write_sfx_grade changes the mp3's mtime; _update_grade_cache_entry
        must store the post-write stat so the next scan is still read-free."""
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        sfx = tmp_path / "SFX"
        _make_mp3(sfx / "one.mp3")
        _scan_sfx_grades()
        _write_sfx_grade(str(sfx / "one.mp3"), "rejected")
        xil_gui._update_grade_cache_entry(str(sfx / "one.mp3"), "rejected")
        calls = self._count_id3_reads(monkeypatch)
        cache = _scan_sfx_grades()
        assert calls == []
        assert cache[str(sfx / "one.mp3")] == "rejected"

    def test_update_entry_missing_file_is_silent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        xil_gui._update_grade_cache_entry(str(tmp_path / "SFX" / "gone.mp3"), "accurate")


# ── Local audio cache (NAS workspaces) ───────────────────────────────────────

class TestCachedAudioPath:
    """_cached_audio_path copies workspace audio to a local cache dir with
    chunked progress so gr.Audio never streams straight off a slow NAS."""

    @pytest.fixture(autouse=True)
    def _xdg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))

    def test_copies_and_returns_local_path(self, tmp_path):
        src = tmp_path / "a.mp3"
        src.write_bytes(b"x" * 1000)
        out = xil_gui._cached_audio_path(str(src))
        assert out != str(src)
        assert out.startswith(str(tmp_path / "xdg"))
        assert out.endswith(".mp3")
        with open(out, "rb") as f:
            assert f.read() == b"x" * 1000

    def test_second_call_is_a_hit_without_recopy(self, tmp_path):
        src = tmp_path / "a.mp3"
        src.write_bytes(b"x" * 1000)
        first = xil_gui._cached_audio_path(str(src))
        calls = []
        second = xil_gui._cached_audio_path(str(src), progress_cb=lambda d, t: calls.append(d))
        assert second == first
        assert calls == []          # hit → no copy → no progress

    def test_modified_source_gets_new_entry(self, tmp_path):
        src = tmp_path / "a.mp3"
        src.write_bytes(b"x" * 1000)
        first = xil_gui._cached_audio_path(str(src))
        os.utime(src, (11111, 11111))
        second = xil_gui._cached_audio_path(str(src))
        assert second != first

    def test_progress_is_monotonic_and_ends_complete(self, tmp_path, monkeypatch):
        monkeypatch.setattr(xil_gui, "_AUDIO_COPY_CHUNK", 256)
        src = tmp_path / "a.wav"
        src.write_bytes(b"x" * 1000)
        seen = []
        xil_gui._cached_audio_path(str(src), progress_cb=lambda d, t: seen.append((d, t)))
        assert seen[-1] == (1000, 1000)
        dones = [d for d, _ in seen]
        assert dones == sorted(dones)
        assert len(seen) == 4       # ceil(1000 / 256) chunks

    def test_missing_source_returns_src_unchanged(self, tmp_path):
        missing = str(tmp_path / "nope.mp3")
        assert xil_gui._cached_audio_path(missing) == missing

    def test_eviction_deletes_oldest_beyond_budget(self, tmp_path, monkeypatch):
        monkeypatch.setattr(xil_gui, "_AUDIO_CACHE_MAX_BYTES", 2500)
        cached = []
        for i in range(3):
            src = tmp_path / f"f{i}.mp3"
            src.write_bytes(bytes([i]) * 1000)
            out = xil_gui._cached_audio_path(str(src))
            os.utime(out, (1000 + i, 1000 + i))
            cached.append(out)
        assert not os.path.exists(cached[0])     # oldest evicted
        assert os.path.exists(cached[1])
        assert os.path.exists(cached[2])


class TestConcatenateStemsCache:
    """'▶ All …' output is cached keyed by the input stems' (path,size,mtime)
    signature — repeat clicks must not re-decode, and the old
    NamedTemporaryFile leak is gone (everything lives in the bounded cache)."""

    def _seed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
        stems = tmp_path / "stems" / "the413" / "S01E01"
        _make_mp3(stems / "001_intro_host.mp3")
        _make_mp3(stems / "002_beat_fx.mp3")
        return stems

    def test_concat_lands_in_cache_dir(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        out = xil_gui._concatenate_stems("the413  S01E01", "all")
        assert out is not None
        assert os.path.exists(out)
        assert out.startswith(str(tmp_path / "xdg"))

    def test_repeat_call_is_a_hit_without_redecoding(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        first = xil_gui._concatenate_stems("the413  S01E01", "all")
        calls = []
        second = xil_gui._concatenate_stems(
            "the413  S01E01", "all", progress_cb=lambda i, n: calls.append(i))
        assert second == first
        assert calls == []          # hit → no per-stem decode

    def test_changed_stem_produces_new_output(self, tmp_path, monkeypatch):
        stems = self._seed(tmp_path, monkeypatch)
        first = xil_gui._concatenate_stems("the413  S01E01", "all")
        os.utime(stems / "001_intro_host.mp3", (22222, 22222))
        second = xil_gui._concatenate_stems("the413  S01E01", "all")
        assert second != first

    def test_no_stray_files_outside_cache_dir(self, tmp_path, monkeypatch):
        import tempfile
        self._seed(tmp_path, monkeypatch)
        before = set(os.listdir(tempfile.gettempdir()))
        xil_gui._concatenate_stems("the413  S01E01", "all")
        after = set(os.listdir(tempfile.gettempdir()))
        assert not {f for f in after - before if f.endswith(".mp3")}

    def test_empty_choice_returns_none(self, tmp_path, monkeypatch):
        self._seed(tmp_path, monkeypatch)
        assert xil_gui._concatenate_stems("", "all") is None


# ── _timeline_iframe_html ─────────────────────────────────────────────────────

class TestTimelineIframeHtml:
    def test_iframe_url_is_cache_busted(self, tmp_path):
        """The iframe src must carry the file mtime so a regenerated timeline
        can never be served from the browser cache (stale-cache bug, 2026-07-05)."""
        from xil_pipeline.xil_gui import _timeline_iframe_html

        f = tmp_path / "S01E01_timeline.html"
        f.write_text("<html></html>")
        html = _timeline_iframe_html(str(f))
        v = int(os.path.getmtime(f))
        assert f"?v={v}" in html
        assert f"/gradio_api/file={os.path.abspath(f)}" in html
        assert "<iframe" in html


# ── SFX editor FastAPI routes ─────────────────────────────────────────────────

class TestSfxRoutes:
    """The timeline editor's /xil/get-sfx and /xil/update-sfx routes.

    Registered via _register_sfx_routes(app) — a module-level function so it
    can target the FastAPI app Gradio creates during launch() (launch REPLACES
    demo.app, so routes added before launch are silently discarded — the
    2026-07-05 'editor opens empty / Save fails' bug).
    """

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        cfg_dir = tmp_path / "configs" / "myshow"
        cfg_dir.mkdir(parents=True)
        cfg = {
            "defaults": {"music_volume_percentage": 40, "volume_percentage": 70},
            "effects": {"MUSIC: THEME": {"volume_percentage": 80}},
        }
        (cfg_dir / "sfx_S01E01.json").write_text(json.dumps(cfg))

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from xil_pipeline.xil_gui import _register_sfx_routes

        app = FastAPI()
        _register_sfx_routes(app)
        return TestClient(app)

    def test_get_returns_effect_and_defaults(self, client):
        r = client.get("/xil/get-sfx", params={
            "slug": "myshow", "tag": "S01E01", "key": "MUSIC: THEME"})
        assert r.status_code == 200
        body = r.json()
        assert body["effect"] == {"volume_percentage": 80}
        assert body["defaults"]["music_volume_percentage"] == 40

    def test_get_unknown_key_returns_empty_effect(self, client):
        r = client.get("/xil/get-sfx", params={
            "slug": "myshow", "tag": "S01E01", "key": "SFX: NOPE"})
        assert r.status_code == 200
        assert r.json()["effect"] == {}

    def test_get_missing_config_404s(self, client):
        r = client.get("/xil/get-sfx", params={
            "slug": "ghostshow", "tag": "S09E99", "key": "X"})
        assert r.status_code == 404

    def test_get_returns_natural_s_for_source_cue(self, tmp_path, monkeypatch):
        """The modal previews play_duration as a % of the SOURCE FILE.

        Without the file's true length the preview falls back to duration_seconds
        and under-reports every cue that duration_seconds is clipping — setting
        "Play Duration % = 100" appears to do nothing on exactly the cues it fixes.
        """
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        cfg_dir = tmp_path / "configs" / "myshow"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "sfx_S01E01.json").write_text(json.dumps({
            "defaults": {},
            "effects": {
                "OUTRO MUSIC": {"source": "SFX/outro.mp3", "duration_seconds": 5.0},
                "MUSIC: STING": {"prompt": "a sting", "duration_seconds": 15.0},
                "SFX: GONE": {"source": "SFX/absent.mp3", "duration_seconds": 5.0},
            },
        }))
        monkeypatch.chdir(tmp_path)

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from xil_pipeline import xil_gui
        # 65 s file — far longer than the 5 s duration_seconds clipping it.
        monkeypatch.setattr("xil_pipeline.mix_common._mp3_duration_ms",
                            lambda path: 65_000 if path.endswith("outro.mp3")
                            else (_ for _ in ()).throw(FileNotFoundError(path)))
        app = FastAPI()
        xil_gui._register_sfx_routes(app)
        client = TestClient(app)

        def natural(key):
            r = client.get("/xil/get-sfx", params={
                "slug": "myshow", "tag": "S01E01", "key": key})
            assert r.status_code == 200
            return r.json()["natural_s"]

        assert natural("OUTRO MUSIC") == 65.0        # the value the preview needs
        assert natural("MUSIC: STING") is None       # generated cue — no source
        assert natural("SFX: GONE") is None          # unreadable — must not 500

    def test_post_updates_and_clears_fields(self, client, tmp_path):
        r = client.post("/xil/update-sfx", json={
            "slug": "myshow", "tag": "S01E01", "key": "MUSIC: THEME",
            "volume_percentage": None,       # clear existing override
            "ramp_in_seconds": 2.5,          # set new override
            "ramp_out_seconds": None,
            "play_duration": 66,
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True
        on_disk = json.loads(
            (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text())
        effect = on_disk["effects"]["MUSIC: THEME"]
        assert "volume_percentage" not in effect
        assert effect["ramp_in_seconds"] == 2.5
        assert effect["play_duration"] == 66

    def test_post_creates_effect_entry_when_absent(self, client, tmp_path):
        r = client.post("/xil/update-sfx", json={
            "slug": "myshow", "tag": "S01E01", "key": "SFX: NEW DOOR",
            "volume_percentage": 55,
        })
        assert r.status_code == 200
        on_disk = json.loads(
            (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text())
        assert on_disk["effects"]["SFX: NEW DOOR"] == {"volume_percentage": 55}

    def test_post_missing_config_404s(self, client):
        r = client.post("/xil/update-sfx", json={
            "slug": "ghostshow", "tag": "S09E99", "key": "X",
            "volume_percentage": 10})
        assert r.status_code == 404

    # ── path-traversal rejection (CodeQL py/path-injection, 2026-07-05) ──────
    # slug/tag flow straight into derive_paths() -> a filesystem path. Legit
    # slugs/tags are always alphanumeric (see models.show_slug), so anything
    # else — path separators, "..", null bytes — must be rejected before it
    # ever reaches derive_paths, not merely contained after the fact.

    @pytest.mark.parametrize("bad_slug", [
        "../../etc", "..\\..\\windows", "foo/bar", "foo\\bar",
        "..", ".", "a/../../b", "show\x00name",
    ])
    def test_get_rejects_unsafe_slug(self, client, bad_slug):
        r = client.get("/xil/get-sfx", params={
            "slug": bad_slug, "tag": "S01E01", "key": "X"})
        assert r.status_code == 400

    @pytest.mark.parametrize("bad_tag", [
        "../S01E01", "S01/../E01", "S01E01/../../secrets", ".",
    ])
    def test_get_rejects_unsafe_tag(self, client, bad_tag):
        r = client.get("/xil/get-sfx", params={
            "slug": "myshow", "tag": bad_tag, "key": "X"})
        assert r.status_code == 400

    def test_post_rejects_unsafe_slug(self, client):
        r = client.post("/xil/update-sfx", json={
            "slug": "../../etc", "tag": "S01E01", "key": "X",
            "volume_percentage": 10})
        assert r.status_code == 400

    def test_post_rejects_unsafe_tag(self, client):
        r = client.post("/xil/update-sfx", json={
            "slug": "myshow", "tag": "../../S01E01", "key": "X",
            "volume_percentage": 10})
        assert r.status_code == 400

    def test_post_rejection_does_not_touch_disk(self, client, tmp_path):
        before = (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text()
        client.post("/xil/update-sfx", json={
            "slug": "../../etc", "tag": "S01E01", "key": "X",
            "volume_percentage": 10})
        after = (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text()
        assert before == after

    def test_legit_slug_and_tag_with_hyphen_underscore_still_work(self, client, tmp_path):
        cfg_dir = tmp_path / "configs" / "my-show_2"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "sfx_S01E01-alt.json").write_text(json.dumps({"effects": {}}))
        r = client.get("/xil/get-sfx", params={
            "slug": "my-show_2", "tag": "S01E01-alt", "key": "X"})
        assert r.status_code == 200


# ── CodeQL hardening: _parse_choice slug validation + config-loader guards ───

class TestParseChoiceRejectsUnsafeSlug:
    """_parse_choice is the choke point where GUI dropdown strings become
    slug/tag fed to derive_paths — a traversal slug must come back empty."""

    @pytest.mark.parametrize("bad", [
        "../../etc  S01E01",
        "..  S01E01",
        "foo/bar  S01E01",
        "foo\\bar  S01E01",
    ])
    def test_traversal_slug_returns_empty(self, bad):
        assert _parse_choice(bad) == ("", "")

    def test_legit_choices_unaffected(self):
        assert _parse_choice("the413  S03E03") == ("the413", "S03E03")
        assert _parse_choice("my-show_2  S01E01") == ("my-show_2", "S01E01")


class TestConfigLoadersGuarded:
    """load_*_config read whatever path they are handed; the matching save_*
    functions check the workspace boundary but the loads did not (CodeQL
    py/path-injection, 2026-07-05). Loads must refuse paths outside the
    workspace root without reading them."""

    @pytest.fixture
    def ws(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path / "workspace"))
        (tmp_path / "workspace").mkdir()
        outside = tmp_path / "outside.json"
        outside.write_text('{"secret": true}')
        return outside

    @pytest.mark.parametrize("loader_name", [
        "load_cast_config", "load_speakers_config", "load_sfx_config"])
    def test_loader_refuses_path_outside_workspace(self, ws, loader_name):
        import xil_pipeline.xil_gui as gui
        loader = getattr(gui, loader_name)
        out = loader(str(ws))
        assert "secret" not in out
        assert "outside the workspace" in out

    def test_loader_reads_inside_workspace(self, ws, tmp_path):
        import xil_pipeline.xil_gui as gui
        inside = tmp_path / "workspace" / "cast_S01E01.json"
        inside.write_text('{"cast": {}}')
        assert gui.load_cast_config(str(inside)) == '{"cast": {}}'


class TestSfxRouteJournaling:
    """Every successful /xil/update-sfx save is journaled to
    sfx_{tag}_edits.jsonl so edits survive config regeneration."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        cfg_dir = tmp_path / "configs" / "myshow"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "sfx_S01E01.json").write_text(json.dumps({
            "defaults": {}, "effects": {"MUSIC: THEME": {}}}))
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from xil_pipeline.xil_gui import _register_sfx_routes
        app = FastAPI()
        _register_sfx_routes(app)
        return TestClient(app)

    def _journal(self, tmp_path):
        return tmp_path / "configs" / "myshow" / "sfx_S01E01_edits.jsonl"

    def test_successful_save_appends_one_record(self, client, tmp_path):
        r = client.post("/xil/update-sfx", json={
            "slug": "myshow", "tag": "S01E01", "key": "MUSIC: THEME",
            "volume_percentage": 33, "play_duration": 66})
        assert r.status_code == 200
        lines = self._journal(tmp_path).read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["key"] == "MUSIC: THEME"
        assert rec["fields"]["volume_percentage"] == 33
        assert rec["fields"]["play_duration"] == 66
        assert rec["fields"]["ramp_in_seconds"] is None

    def test_rejected_save_journals_nothing(self, client, tmp_path):
        client.post("/xil/update-sfx", json={
            "slug": "../../etc", "tag": "S01E01", "key": "X",
            "volume_percentage": 10})
        assert not self._journal(tmp_path).exists()


# ── startup workspace banner ──────────────────────────────────────────────────

class TestPrintWorkspaceBanner:
    """A stale/misconfigured XIL_PROJECTROOT (wrong terminal, un-sourced
    .bashrc, typo) must be impossible to miss at xil-gui startup — printed
    to the terminal before the Gradio server even builds."""

    def test_prints_resolved_workspace_path(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        from xil_pipeline.xil_gui import _print_workspace_banner
        _print_workspace_banner()
        out = capsys.readouterr().out
        assert str(tmp_path) in out

    def test_no_warning_when_directory_exists(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        from xil_pipeline.xil_gui import _print_workspace_banner
        _print_workspace_banner()
        out = capsys.readouterr().out
        assert "WARNING" not in out

    def test_warns_when_directory_missing(self, tmp_path, monkeypatch, capsys):
        missing = tmp_path / "does-not-exist"
        monkeypatch.setenv("XIL_PROJECTROOT", str(missing))
        from xil_pipeline.xil_gui import _print_workspace_banner
        _print_workspace_banner()
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert str(missing) in out

    def test_returns_resolved_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        from xil_pipeline.xil_gui import _print_workspace_banner
        result = _print_workspace_banner()
        assert result == tmp_path.resolve()


class TestSfxRouteVerboseLogging:
    """--verbose (DEBUG level) surfaces per-request detail for the Timeline
    audio-properties dialog; the INFO/WARNING summary lines stay visible at
    the default level too. See xil_gui.logger / configure_logging in main()."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        cfg_dir = tmp_path / "configs" / "myshow"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "sfx_S01E01.json").write_text(json.dumps({
            "defaults": {}, "effects": {"MUSIC: THEME": {}}}))
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from xil_pipeline.xil_gui import _register_sfx_routes
        app = FastAPI()
        _register_sfx_routes(app)
        return TestClient(app)

    def test_get_sfx_logs_debug_on_success(self, client, caplog):
        with caplog.at_level(logging.DEBUG, logger="xil_pipeline.xil_gui"):
            r = client.get("/xil/get-sfx", params={
                "slug": "myshow", "tag": "S01E01", "key": "MUSIC: THEME"})
        assert r.status_code == 200
        assert any(
            "MUSIC: THEME" in rec.message and rec.levelno == logging.DEBUG
            for rec in caplog.records
        )

    def test_get_sfx_logs_warning_on_invalid_slug(self, client, caplog):
        with caplog.at_level(logging.DEBUG, logger="xil_pipeline.xil_gui"):
            r = client.get("/xil/get-sfx", params={
                "slug": "../../etc", "tag": "S01E01", "key": "X"})
        assert r.status_code == 400
        assert any(rec.levelno == logging.WARNING for rec in caplog.records)

    def test_update_sfx_logs_debug_body_on_success(self, client, caplog):
        with caplog.at_level(logging.DEBUG, logger="xil_pipeline.xil_gui"):
            r = client.post("/xil/update-sfx", json={
                "slug": "myshow", "tag": "S01E01", "key": "MUSIC: THEME",
                "volume_percentage": 42})
        assert r.status_code == 200
        assert any(
            "volume_percentage" in rec.message and rec.levelno == logging.DEBUG
            for rec in caplog.records
        )

    def test_update_sfx_logs_info_on_success(self, client, caplog):
        # INFO level only — the summary line must be visible without --verbose.
        with caplog.at_level(logging.INFO, logger="xil_pipeline.xil_gui"):
            r = client.post("/xil/update-sfx", json={
                "slug": "myshow", "tag": "S01E01", "key": "MUSIC: THEME",
                "volume_percentage": 42})
        assert r.status_code == 200
        assert any(
            "MUSIC: THEME" in rec.message and rec.levelno == logging.INFO
            for rec in caplog.records
        )
        # No DEBUG-only records leaked through at the INFO threshold.
        assert not any(rec.levelno == logging.DEBUG for rec in caplog.records)

    def test_update_sfx_logs_warning_on_journal_failure(self, client, monkeypatch, caplog):
        def _boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr("xil_pipeline.sfx_common.append_sfx_edit", _boom)
        with caplog.at_level(logging.DEBUG, logger="xil_pipeline.xil_gui"):
            r = client.post("/xil/update-sfx", json={
                "slug": "myshow", "tag": "S01E01", "key": "MUSIC: THEME",
                "volume_percentage": 42})
        # The save itself must still succeed even though the journal failed.
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert any(
            "journal write failed" in rec.message and rec.levelno == logging.WARNING
            for rec in caplog.records
        )


class TestSfxDefaultsRoute:
    """POST /xil/update-sfx-defaults — edits the config's category defaults
    (e.g. music_volume_percentage), the counterpart to the per-cue
    /xil/update-sfx route. Same validate-before-path-build pattern; same
    journal mechanism via append_sfx_defaults_edit (scope: "defaults")."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        cfg_dir = tmp_path / "configs" / "myshow"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "sfx_S01E01.json").write_text(json.dumps({
            "defaults": {"volume_percentage": 20, "music_volume_percentage": 80},
            "effects": {"MUSIC: THEME": {}},
        }))
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from xil_pipeline.xil_gui import _register_sfx_routes
        app = FastAPI()
        _register_sfx_routes(app)
        return TestClient(app)

    def _journal(self, tmp_path):
        return tmp_path / "configs" / "myshow" / "sfx_S01E01_edits.jsonl"

    def test_sets_prefixed_key(self, client, tmp_path):
        r = client.post("/xil/update-sfx-defaults", json={
            "slug": "myshow", "tag": "S01E01", "layer": "music",
            "volume_percentage": 42})
        assert r.status_code == 200
        assert r.json()["ok"] is True
        on_disk = json.loads(
            (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text())
        assert on_disk["defaults"]["music_volume_percentage"] == 42

    def test_null_pops_prefixed_key(self, client, tmp_path):
        client.post("/xil/update-sfx-defaults", json={
            "slug": "myshow", "tag": "S01E01", "layer": "music",
            "volume_percentage": None})
        on_disk = json.loads(
            (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text())
        assert "music_volume_percentage" not in on_disk["defaults"]

    def test_unprefixed_global_default_untouched(self, client, tmp_path):
        # A layer save must only ever write its own prefixed key — never the
        # un-prefixed global fallback, even though one already exists.
        client.post("/xil/update-sfx-defaults", json={
            "slug": "myshow", "tag": "S01E01", "layer": "music",
            "volume_percentage": 42})
        on_disk = json.loads(
            (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text())
        assert on_disk["defaults"]["volume_percentage"] == 20

    def test_other_layers_defaults_untouched(self, client, tmp_path):
        client.post("/xil/update-sfx-defaults", json={
            "slug": "myshow", "tag": "S01E01", "layer": "ambience",
            "volume_percentage": 15})
        on_disk = json.loads(
            (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text())
        assert on_disk["defaults"]["music_volume_percentage"] == 80
        assert on_disk["defaults"]["ambience_volume_percentage"] == 15

    def test_rejects_unsafe_slug(self, client):
        r = client.post("/xil/update-sfx-defaults", json={
            "slug": "../../etc", "tag": "S01E01", "layer": "music",
            "volume_percentage": 10})
        assert r.status_code == 400

    def test_rejects_bad_layer(self, client, tmp_path):
        r = client.post("/xil/update-sfx-defaults", json={
            "slug": "myshow", "tag": "S01E01", "layer": "dialogue",
            "volume_percentage": 10})
        assert r.status_code == 400
        # Rejected request must not touch disk.
        on_disk = json.loads(
            (tmp_path / "configs" / "myshow" / "sfx_S01E01.json").read_text())
        assert "dialogue_volume_percentage" not in on_disk["defaults"]

    def test_missing_config_404s(self, client):
        r = client.post("/xil/update-sfx-defaults", json={
            "slug": "ghostshow", "tag": "S09E99", "layer": "music",
            "volume_percentage": 10})
        assert r.status_code == 404

    def test_journals_defaults_scope_record(self, client, tmp_path):
        client.post("/xil/update-sfx-defaults", json={
            "slug": "myshow", "tag": "S01E01", "layer": "music",
            "volume_percentage": 42, "ramp_in_seconds": 1.5})
        lines = self._journal(tmp_path).read_text().splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["scope"] == "defaults"
        assert "key" not in rec
        assert rec["fields"]["music_volume_percentage"] == 42
        assert rec["fields"]["music_ramp_in_seconds"] == 1.5

    def test_rejected_request_journals_nothing(self, client, tmp_path):
        client.post("/xil/update-sfx-defaults", json={
            "slug": "../../etc", "tag": "S01E01", "layer": "music",
            "volume_percentage": 10})
        assert not self._journal(tmp_path).exists()


class TestAllowedPaths:
    """Gradio only serves files under allowed_paths / cwd / tempdir.

    The audio cache lives under $XDG_CACHE_HOME, which is none of those, so
    omitting it made every cached playback and concat preview raise
    InvalidPathError — the cache exists precisely to avoid streaming off the
    NAS, so it is the path playback always takes.
    """

    @pytest.fixture(autouse=True)
    def _xdg(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))

    def _under_allowed(self, path: str) -> bool:
        return any(
            os.path.commonpath([os.path.realpath(path), os.path.realpath(root)])
            == os.path.realpath(root)
            for root in xil_gui._allowed_paths()
        )

    def test_includes_the_audio_cache_dir(self):
        assert xil_gui._audio_cache_dir() in xil_gui._allowed_paths()

    def test_includes_the_workspace_root(self):
        from xil_pipeline.models import get_workspace_root
        assert str(get_workspace_root()) in xil_gui._allowed_paths()

    def test_honours_xdg_cache_home(self, tmp_path):
        assert any(str(tmp_path / "xdg") in p for p in xil_gui._allowed_paths())

    def test_cached_audio_path_is_servable(self, tmp_path):
        """The regression: a real cached file must fall under an allowed root."""
        src = tmp_path / "a.mp3"
        src.write_bytes(b"x" * 1000)
        assert self._under_allowed(xil_gui._cached_audio_path(str(src)))

    def test_concat_preview_dir_is_servable(self):
        """Concat previews are written into the same cache dir."""
        probe = os.path.join(xil_gui._audio_cache_dir(), "concat_deadbeef.mp3")
        assert self._under_allowed(probe)
