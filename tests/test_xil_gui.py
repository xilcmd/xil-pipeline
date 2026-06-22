# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for xil_gui helper functions (no Gradio dependency required)."""

import json
import os

import pytest

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
