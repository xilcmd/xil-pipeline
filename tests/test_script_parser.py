# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP001_script_parser.py — markdown production script parser."""

import csv as csv_module
import json
import os
import unittest.mock

import pytest

from xil_pipeline import XILP001_script_parser as parser
from xil_pipeline import models

# ─── Unit Tests: strip_markdown_escapes ───

class TestStripMarkdownEscapes:
    def test_brackets(self):
        assert parser.strip_markdown_escapes("\\[SFX: DOOR\\]") == "[SFX: DOOR]"

    def test_dividers(self):
        assert parser.strip_markdown_escapes("\\===") == "==="

    def test_periods_and_tildes(self):
        assert parser.strip_markdown_escapes("1972\\.") == "1972."
        assert parser.strip_markdown_escapes("\\~30 minutes") == "~30 minutes"

    def test_no_escapes(self):
        assert parser.strip_markdown_escapes("plain text") == "plain text"

    def test_multiple_escapes_in_one_line(self):
        result = parser.strip_markdown_escapes("\\[BEAT\\] and \\[SFX\\]")
        assert result == "[BEAT] and [SFX]"


# ─── Unit Tests: classify_direction ───

class TestClassifyDirection:
    def test_sfx(self):
        assert parser.classify_direction("SFX: DOOR OPENS") == "SFX"

    def test_music(self):
        assert parser.classify_direction("MUSIC: THEME BEGINS, LOW") == "MUSIC"

    def test_ambience(self):
        assert parser.classify_direction("AMBIENCE: DINER – COFFEE") == "AMBIENCE"

    def test_beat(self):
        assert parser.classify_direction("BEAT") == "BEAT"

    def test_long_beat(self):
        assert parser.classify_direction("LONG BEAT") == "BEAT"

    def test_unknown(self):
        assert parser.classify_direction("EVERYONE TURNS") is None

    def test_whitespace(self):
        assert parser.classify_direction("  SFX: PHONE BUZZING  ") == "SFX"


# ─── Unit Tests: try_match_speaker ───

class TestTryMatchSpeaker:
    def test_simple_dialogue(self):
        result = parser.try_match_speaker("ADAM What do you mean?")
        assert result == ("adam", None, "What do you mean?")

    def test_dialogue_with_direction(self):
        result = parser.try_match_speaker("ADAM (narration) Morrison's Diner has been open...")
        assert result == ("adam", "narration", "Morrison's Diner has been open...")

    def test_multi_word_speaker(self):
        result = parser.try_match_speaker("MR. PATTERSON Long enough.")
        assert result == ("mr_patterson", None, "Long enough.")

    def test_multi_word_with_direction(self):
        result = parser.try_match_speaker("MR. PATTERSON (older man's voice) That's because she had.")
        assert result == ("mr_patterson", "older man's voice", "That's because she had.")

    def test_accented_speaker(self):
        result = parser.try_match_speaker("RÍAN (entering) Okay, I'm here.")
        assert result == ("rian", "entering", "Okay, I'm here.")

    def test_no_match(self):
        assert parser.try_match_speaker("Some random text") is None

    def test_partial_name_no_match(self):
        # "ADAMS" should not match "ADAM"
        assert parser.try_match_speaker("ADAMS went home") is None

    def test_speaker_with_parenthetical_only(self):
        result = parser.try_match_speaker("DEZ (uneasy) I know.")
        assert result == ("dez", "uneasy", "I know.")


# ─── Unit Tests: line classifiers ───

class TestLineClassifiers:
    def test_is_stage_direction(self):
        assert parser.is_stage_direction("[SFX: DOOR OPENS]") is True
        assert parser.is_stage_direction("[BEAT]") is True
        assert parser.is_stage_direction("ADAM Hello") is False

    def test_is_section_header(self):
        assert parser.is_section_header("COLD OPEN") is True
        assert parser.is_section_header("ACT ONE") is True
        assert parser.is_section_header("SCENE 1: DINER") is False

    def test_is_scene_header(self):
        assert parser.is_scene_header("SCENE 1: THE DINER") is True
        assert parser.is_scene_header("SCENE 12: FINALE") is True
        assert parser.is_scene_header("COLD OPEN") is False

    def test_is_divider(self):
        assert parser.is_divider("===") is True
        assert parser.is_divider("  ===  ") is True
        assert parser.is_divider("== =") is False

    def test_is_metadata_section(self):
        assert parser.is_metadata_section("PRODUCTION NOTES:") is True
        assert parser.is_metadata_section("MUSIC CUES:") is True
        assert parser.is_metadata_section("ACT ONE") is False


class TestParseSceneHeader:
    def test_simple(self):
        num, name = parser.parse_scene_header("SCENE 1: THE DINER – INTERIOR")
        assert num == 1
        assert name == "THE DINER – INTERIOR"

    def test_no_match(self):
        num, name = parser.parse_scene_header("COLD OPEN")
        assert num is None
        assert name is None


# ─── Unit Tests: parse_script_header ───

class TestParseScriptHeader:
    FULL = 'THE 413 Season 1: Episode 1: "The Empty Booth" Arc: "The Holiday Shift" (1 of 3) Runtime: ~30 minutes'
    MINIMAL = "THE 413 Season 1: Episode 1: Test"
    NO_SEASON = "THE 413 Episode 2: Another Episode"

    def test_full_header_extracts_show(self):
        show, _, _, _ = parser.parse_script_header(self.FULL)
        assert show == "THE 413"

    def test_full_header_extracts_season(self):
        _, season, _, _ = parser.parse_script_header(self.FULL)
        assert season == 1

    def test_full_header_extracts_episode(self):
        _, _, episode, _ = parser.parse_script_header(self.FULL)
        assert episode == 1

    def test_full_header_extracts_episode_title(self):
        """First quoted string after 'Episode N:' is the episode title, not the arc."""
        _, _, _, title = parser.parse_script_header(self.FULL)
        assert title == "The Empty Booth"

    def test_arc_title_not_used_as_episode_title(self):
        header = 'THE 413 Season 1: Episode 2: "Reel to Real" Arc: "The Holiday Shift" (2 of 3) Runtime: ~30 minutes'
        _, _, _, title = parser.parse_script_header(header)
        assert title == "Reel to Real"

    def test_minimal_header_extracts_season(self):
        _, season, _, _ = parser.parse_script_header(self.MINIMAL)
        assert season == 1

    def test_minimal_header_extracts_title_after_episode(self):
        _, _, _, title = parser.parse_script_header(self.MINIMAL)
        assert title == "Test"

    def test_no_season_returns_none(self):
        _, season, _, _ = parser.parse_script_header(self.NO_SEASON)
        assert season is None

    def test_no_season_extracts_episode(self):
        _, _, episode, _ = parser.parse_script_header(self.NO_SEASON)
        assert episode == 2


# ─── Integration Test: parse_script with minimal fixture ───

MINIMAL_SCRIPT = """\
THE 413 Season 1: Episode 1: Test

CAST:

* ADAM SANTOS (Host)
* DEZ WILLIAMS (Supporting)

===

COLD OPEN

[AMBIENCE: RADIO STATION]

ADAM (on-air voice) It's 2:47 AM.

[BEAT]

ADAM (continuing) If you're listening right now, you're awake.

But let me back up.

===

ACT ONE

SCENE 1: THE DINER [AMBIENCE: DINER]

===

DEZ Adam. Over here.

ADAM (approaching) What's going on?

MR. PATTERSON (gravel-rough) That's because she had.

[SFX: DOOR OPENS] [MUSIC: THEME]

===

END OF EPISODE 1

===

PRODUCTION NOTES:

* Should not appear in output
"""


class TestParseScriptIntegration:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "test_script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_top_level_metadata(self, parsed):
        assert parsed["show"] == "THE 413"
        assert parsed["episode"] == 1
        assert parsed["season"] == 1

    def test_dialogue_count(self, parsed):
        assert parsed["stats"]["dialogue_lines"] == 5  # ADAM x3, DEZ x1, MR. PATTERSON x1

    def test_all_speakers_found(self, parsed):
        assert set(parsed["stats"]["speakers"]) == {"adam", "dez", "mr_patterson"}

    def test_sections_tracked(self, parsed):
        assert "cold-open" in parsed["stats"]["sections"]
        assert "act1" in parsed["stats"]["sections"]

    def test_direction_extracted(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        adam_first = dialogue[0]
        assert adam_first["speaker"] == "adam"
        assert adam_first["direction"] == "on-air voice"
        assert adam_first["text"] == "It's 2:47 AM."

    def test_continuation_line_merged(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        # Second ADAM line should have continuation appended
        adam_second = dialogue[1]
        assert "But let me back up." in adam_second["text"]
        assert adam_second["text"].startswith("If you're listening")

    def test_scene_context(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        dez_line = [d for d in dialogue if d["speaker"] == "dez"][0]
        assert dez_line["scene"] == "scene-1"
        assert dez_line["section"] == "act1"

    def test_mr_patterson_parsed(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        mp = [d for d in dialogue if d["speaker"] == "mr_patterson"][0]
        assert mp["direction"] == "gravel-rough"
        assert mp["text"] == "That's because she had."

    def test_multi_direction_line(self, parsed):
        directions = [e for e in parsed["entries"] if e["type"] == "direction"]
        sfx_dirs = [d for d in directions if d["direction_type"] == "SFX"]
        music_dirs = [d for d in directions if d["direction_type"] == "MUSIC"]
        assert len(sfx_dirs) >= 1
        assert len(music_dirs) >= 1

    def test_scene_header_ambience_split(self, parsed):
        """Scene header with embedded [AMBIENCE:...] produces two entries."""
        scene_headers = [e for e in parsed["entries"] if e["type"] == "scene_header"]
        assert len(scene_headers) == 1
        # Scene header text should be clean (no brackets)
        assert "[" not in scene_headers[0]["text"]
        assert scene_headers[0]["text"] == "SCENE 1: THE DINER"
        # A separate AMBIENCE direction entry should follow
        ambience_dirs = [e for e in parsed["entries"]
                         if e["type"] == "direction" and e["direction_type"] == "AMBIENCE"]
        diner_ambience = [d for d in ambience_dirs if "DINER" in d["text"]]
        assert len(diner_ambience) >= 1
        assert diner_ambience[0]["scene"] == "scene-1"

    def test_metadata_excluded(self, parsed):
        all_text = " ".join(e["text"] for e in parsed["entries"] if e["text"])
        assert "Should not appear in output" not in all_text

    def test_cast_section_excluded(self, parsed):
        all_text = " ".join(e["text"] for e in parsed["entries"] if e["text"])
        assert "ADAM SANTOS (Host)" not in all_text

    def test_tts_character_count(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        expected = sum(len(d["text"]) for d in dialogue)
        assert parsed["stats"]["characters_for_tts"] == expected

    def test_sequence_numbers_ascending(self, parsed):
        seqs = [e["seq"] for e in parsed["entries"]]
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs))  # all unique


# ─── Integration Test: parse full production script ───

FULL_SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "scripts",
    "Full Production Script THE 413 Season 1 _ Episode 1_ _The Empty Booth_ Arc_ _The Holiday Shift_ 1_11_26 CLAUDE.AI PROJECT THE 413.md"
)


@pytest.mark.skipif(not os.path.exists(FULL_SCRIPT_PATH), reason="Full production script not present")
class TestParseFullScript:
    @pytest.fixture
    def parsed(self):
        return parser.parse_script(FULL_SCRIPT_PATH)

    def test_all_seven_speakers(self, parsed):
        expected = {"adam", "ava", "dez", "frank", "maya", "mr_patterson", "rian"}
        assert set(parsed["stats"]["speakers"]) == expected

    def test_dialogue_line_count_in_range(self, parsed):
        # Should be approximately 127 lines (verified in prior runs)
        assert 120 <= parsed["stats"]["dialogue_lines"] <= 140

    def test_tts_chars_in_range(self, parsed):
        # Should be approximately 12,463 chars
        assert 10000 <= parsed["stats"]["characters_for_tts"] <= 15000

    def test_five_sections(self, parsed):
        assert len(parsed["stats"]["sections"]) == 5

    def test_no_backslash_escapes_in_dialogue(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        for d in dialogue:
            assert "\\" not in d["text"], f"Backslash in seq {d['seq']}: {d['text'][:50]}"

    def test_no_stage_directions_in_dialogue_text(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        for d in dialogue:
            assert not d["text"].startswith("["), f"Bracket in seq {d['seq']}: {d['text'][:50]}"

    def test_season_is_one(self, parsed):
        assert parsed["season"] == 1


# ─── Tests: scene header ambience splitting ───

SCRIPT_SCENE_NO_BRACKETS = """\
THE 413 Season 1: Episode 1: Test

===

ACT ONE

SCENE 1: THE DINER

ADAM Hello.

===

END OF EPISODE 1
"""

SCRIPT_SCENE_MULTI_BRACKETS = """\
THE 413 Season 1: Episode 1: Test

===

ACT ONE

SCENE 1: THE DINER [AMBIENCE: DINER SOUNDS] [SFX: DOOR OPENS]

ADAM Hello.

===

END OF EPISODE 1
"""


class TestSceneHeaderAmbienceSplit:
    """Test splitting embedded directions out of scene headers."""

    def test_scene_no_brackets_single_entry(self, tmp_path):
        """Scene header without brackets produces only a scene_header entry."""
        f = tmp_path / "script.md"
        f.write_text(SCRIPT_SCENE_NO_BRACKETS, encoding="utf-8")
        parsed = parser.parse_script(str(f))
        scene_headers = [e for e in parsed["entries"] if e["type"] == "scene_header"]
        assert len(scene_headers) == 1
        assert scene_headers[0]["text"] == "SCENE 1: THE DINER"
        # No direction entries from the scene header line
        directions = [e for e in parsed["entries"] if e["type"] == "direction"]
        assert len(directions) == 0

    def test_scene_multi_brackets_split(self, tmp_path):
        """Scene header with multiple brackets produces scene_header + N direction entries."""
        f = tmp_path / "script.md"
        f.write_text(SCRIPT_SCENE_MULTI_BRACKETS, encoding="utf-8")
        parsed = parser.parse_script(str(f))
        scene_headers = [e for e in parsed["entries"] if e["type"] == "scene_header"]
        assert len(scene_headers) == 1
        assert "[" not in scene_headers[0]["text"]
        assert scene_headers[0]["text"] == "SCENE 1: THE DINER"
        # Two direction entries from the brackets
        directions = [e for e in parsed["entries"] if e["type"] == "direction"]
        assert len(directions) == 2
        types = {d["direction_type"] for d in directions}
        assert "AMBIENCE" in types
        assert "SFX" in types

    def test_scene_ambience_has_correct_scene_context(self, tmp_path):
        """Split direction entries inherit the scene context."""
        f = tmp_path / "script.md"
        f.write_text(SCRIPT_SCENE_MULTI_BRACKETS, encoding="utf-8")
        parsed = parser.parse_script(str(f))
        directions = [e for e in parsed["entries"] if e["type"] == "direction"]
        for d in directions:
            assert d["scene"] == "scene-1"
            assert d["section"] == "act1"

    def test_sequence_numbers_still_ascending(self, tmp_path):
        """Sequence numbers remain unique and ascending after split."""
        f = tmp_path / "script.md"
        f.write_text(SCRIPT_SCENE_MULTI_BRACKETS, encoding="utf-8")
        parsed = parser.parse_script(str(f))
        seqs = [e["seq"] for e in parsed["entries"]]
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs))


# ─── Tests: script without season in header ───

SCRIPT_WITHOUT_SEASON = """\
THE 413 Episode 2: Another Episode

CAST:

* ADAM SANTOS (Host)

===

COLD OPEN

ADAM Hello.

===

END OF EPISODE 2
"""


class TestParseScriptNoSeason:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "no_season.md"
        script_file.write_text(SCRIPT_WITHOUT_SEASON, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_season_is_none_when_not_in_header(self, parsed):
        assert parsed["season"] is None

    def test_episode_still_extracted(self, parsed):
        assert parsed["episode"] == 2


# ─── Tests: print_summary and print_dialogue_preview ───

class TestPrintSummary:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_prints_show_title(self, parsed, caplog):
        parser.print_summary(parsed)
        assert "THE 413" in caplog.text
        assert "Test" in caplog.text  # title from MINIMAL_SCRIPT header

    def test_prints_dialogue_line_count(self, parsed, caplog):
        parser.print_summary(parsed)
        assert "Dialogue lines" in caplog.text
        assert "5" in caplog.text

    def test_prints_per_speaker_stats(self, parsed, caplog):
        parser.print_summary(parsed)
        assert "adam" in caplog.text
        assert "dez" in caplog.text
        assert "mr_patterson" in caplog.text

    def test_prints_tts_character_count(self, parsed, caplog):
        parser.print_summary(parsed)
        assert "TTS characters" in caplog.text


class TestPrintDialoguePreview:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_prints_all_lines_by_default(self, parsed, caplog):
        parser.print_dialogue_preview(parsed)
        assert "It's 2:47 AM." in caplog.text
        assert "That's because she had." in caplog.text

    def test_limit_restricts_output(self, parsed, caplog):
        parser.print_dialogue_preview(parsed, limit=1)
        assert "It's 2:47 AM." in caplog.text
        assert "That's because she had." not in caplog.text

    def test_shows_speaker_name(self, parsed, caplog):
        parser.print_dialogue_preview(parsed)
        assert "adam" in caplog.text
        assert "dez" in caplog.text

    def test_shows_direction(self, parsed, caplog):
        parser.print_dialogue_preview(parsed)
        assert "on-air voice" in caplog.text


# ─── Tests: metadata section path (lines 173-174, 178) ───

SCRIPT_WITH_METADATA_BEFORE_END = """\
THE 413 Episode 1: Test

===

COLD OPEN

ADAM Hello.

PRODUCTION NOTES:

* This should be excluded

DEZ Should not appear either.

===

END OF EPISODE 1
"""


class TestMetadataSectionPath:
    def test_metadata_lines_excluded(self, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text(SCRIPT_WITH_METADATA_BEFORE_END, encoding="utf-8")
        parsed = parser.parse_script(str(script_file))
        all_text = " ".join(e["text"] for e in parsed["entries"] if e.get("text"))
        assert "Should not appear either." not in all_text

    def test_dialogue_before_metadata_included(self, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text(SCRIPT_WITH_METADATA_BEFORE_END, encoding="utf-8")
        parsed = parser.parse_script(str(script_file))
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        assert any("Hello." in d["text"] for d in dialogue)


# ─── Contract Tests: parse_script output validates against Pydantic models ───


class TestParseScriptModelContract:
    """Verify parse_script output is valid against Pydantic models."""

    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "test_script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_entries_are_valid_script_entry_models(self, parsed):
        for entry in parsed["entries"]:
            models.ScriptEntry(**entry)

    def test_stats_is_valid_script_stats_model(self, parsed):
        models.ScriptStats(**parsed["stats"])

    def test_full_output_is_valid_parsed_script_model(self, parsed):
        models.ParsedScript(**parsed)


# ─── Tests: --debug CSV output ───


class TestDebugCSV:
    """Tests for write_debug_csv and parse_script debug_output parameter."""

    SCRIPT = """\
THE 413 Season 1: Episode 1: Test

CAST:

* ADAM SANTOS (Host)

===

COLD OPEN

[AMBIENCE: RADIO STATION]

ADAM (on-air voice) It's 2:47 AM.

===

END OF EPISODE 1
"""

    def _parse_with_debug(self, tmp_path):
        script_file = tmp_path / "debug_script.md"
        script_file.write_text(self.SCRIPT, encoding="utf-8")
        csv_path = str(tmp_path / "debug.csv")
        parser.parse_script(str(script_file), debug_output=csv_path)
        return csv_path

    def _read_csv(self, csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            return list(csv_module.DictReader(f))

    def test_debug_csv_created(self, tmp_path):
        csv_path = self._parse_with_debug(tmp_path)
        assert os.path.exists(csv_path)

    def test_no_csv_without_debug_output(self, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text(self.SCRIPT, encoding="utf-8")
        default_csv = str(tmp_path / "parsed.csv")
        parser.parse_script(str(script_file))
        assert not os.path.exists(default_csv)

    def test_csv_has_expected_columns(self, tmp_path):
        csv_path = self._parse_with_debug(tmp_path)
        rows = self._read_csv(csv_path)
        assert rows, "CSV should have at least one data row"
        expected = {"md_line_num", "md_raw", "seq", "type", "section",
                    "scene", "speaker", "direction", "text", "direction_type"}
        assert expected == set(rows[0].keys())

    def test_direction_entry_row(self, tmp_path):
        csv_path = self._parse_with_debug(tmp_path)
        rows = self._read_csv(csv_path)
        direction_rows = [r for r in rows if r["type"] == "direction"]
        assert direction_rows, "Should have at least one direction row"
        row = direction_rows[0]
        assert row["direction_type"] == "AMBIENCE"
        assert "AMBIENCE: RADIO STATION" in row["text"]
        assert "[AMBIENCE: RADIO STATION]" in row["md_raw"]

    def test_dialogue_entry_row(self, tmp_path):
        csv_path = self._parse_with_debug(tmp_path)
        rows = self._read_csv(csv_path)
        dialogue_rows = [r for r in rows if r["type"] == "dialogue"]
        assert dialogue_rows, "Should have at least one dialogue row"
        row = dialogue_rows[0]
        assert row["speaker"] == "adam"
        assert "2:47" in row["text"]
        assert row["direction"] == "on-air voice"

    def test_md_line_num_is_1_based(self, tmp_path):
        csv_path = self._parse_with_debug(tmp_path)
        rows = self._read_csv(csv_path)
        line_nums = [int(r["md_line_num"]) for r in rows]
        assert all(n >= 1 for n in line_nums), "Line numbers must be 1-based"

    def test_text_truncated_at_200_chars(self, tmp_path):
        long_line = "A" * 250
        script = f"THE 413 Season 1: Episode 1: Test\n\n===\n\nCOLD OPEN\n\nADAM {long_line}\n\n===\n\nEND OF EPISODE 1\n"
        script_file = tmp_path / "long_script.md"
        script_file.write_text(script, encoding="utf-8")
        csv_path = str(tmp_path / "long_debug.csv")
        parser.parse_script(str(script_file), debug_output=csv_path)
        rows = self._read_csv(csv_path)
        dialogue_rows = [r for r in rows if r["type"] == "dialogue"]
        assert dialogue_rows
        assert len(dialogue_rows[0]["text"]) <= 200

    def test_md_raw_truncated_at_200_chars(self, tmp_path):
        long_text = "B" * 250
        script = f"THE 413 Season 1: Episode 1: Test\n\n===\n\nCOLD OPEN\n\nADAM {long_text}\n\n===\n\nEND OF EPISODE 1\n"
        script_file = tmp_path / "long_script2.md"
        script_file.write_text(script, encoding="utf-8")
        csv_path = str(tmp_path / "long_debug2.csv")
        parser.parse_script(str(script_file), debug_output=csv_path)
        rows = self._read_csv(csv_path)
        for row in rows:
            assert len(row["md_raw"]) <= 200

    def test_section_header_row_present(self, tmp_path):
        csv_path = self._parse_with_debug(tmp_path)
        rows = self._read_csv(csv_path)
        section_rows = [r for r in rows if r["type"] == "section_header"]
        assert section_rows, "Should have a section_header row"
        assert section_rows[0]["section"] == "cold-open"


# ─── Tests: --episode validation ───

class TestEpisodeValidation:
    @pytest.fixture
    def script_file(self, tmp_path):
        script_file = tmp_path / "test_script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return str(script_file)

    def test_episode_matches_header(self, script_file, tmp_path):
        """No error when --episode matches script header."""
        output = str(tmp_path / "out.json")
        with unittest.mock.patch("sys.argv", [
            "XILP001", script_file, "--episode", "S01E01",
            "--output", output, "--quiet",
        ]):
            parser.main()
        assert os.path.exists(output)

    def test_episode_mismatch_exits(self, script_file, tmp_path):
        """SystemExit when --episode doesn't match script header."""
        output = str(tmp_path / "out.json")
        with pytest.raises(SystemExit):
            with unittest.mock.patch("sys.argv", [
                "XILP001", script_file, "--episode", "S99E99",
                "--output", output, "--quiet",
            ]):
                parser.main()

    def test_no_episode_arg_works(self, script_file, tmp_path):
        """Parser works normally without --episode."""
        output = str(tmp_path / "out.json")
        with unittest.mock.patch("sys.argv", [
            "XILP001", script_file, "--output", output, "--quiet",
        ]):
            parser.main()
        assert os.path.exists(output)


# ─── Tests: generate_cast_config ───

class TestGenerateCastConfig:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "test_script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_creates_cast_file_when_absent(self, parsed, tmp_path):
        cast_path = str(tmp_path / "cast_the413_S01E01.json")
        parser.generate_cast_config(parsed, cast_path)
        assert os.path.exists(cast_path)

    def test_cast_has_correct_speakers(self, parsed, tmp_path):
        cast_path = str(tmp_path / "cast_the413_S01E01.json")
        parser.generate_cast_config(parsed, cast_path)
        with open(cast_path, encoding="utf-8") as f:
            config = json.load(f)
        assert "adam" in config["cast"]
        assert "dez" in config["cast"]
        assert "mr_patterson" in config["cast"]

    def test_cast_has_tbd_voice_ids(self, parsed, tmp_path):
        cast_path = str(tmp_path / "cast_the413_S01E01.json")
        parser.generate_cast_config(parsed, cast_path)
        with open(cast_path, encoding="utf-8") as f:
            config = json.load(f)
        for member in config["cast"].values():
            assert member["voice_id"] == "TBD"

    def test_cast_metadata_from_script(self, parsed, tmp_path):
        cast_path = str(tmp_path / "cast_the413_S01E01.json")
        parser.generate_cast_config(parsed, cast_path)
        with open(cast_path, encoding="utf-8") as f:
            config = json.load(f)
        assert config["show"] == "THE 413"
        assert config["season"] == 1
        assert config["episode"] == 1

    def test_cast_member_defaults(self, parsed, tmp_path):
        cast_path = str(tmp_path / "cast.json")
        parser.generate_cast_config(parsed, cast_path)
        with open(cast_path, encoding="utf-8") as f:
            config = json.load(f)
        member = config["cast"]["adam"]
        assert member["pan"] == 0.0
        assert member["filter"] is False
        assert member["role"] == "TBD"

    def test_skips_when_cast_exists(self, parsed, tmp_path, caplog):
        """main() should not overwrite existing cast config."""
        script_file = tmp_path / "test_script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        cast_path = tmp_path / "cast_the413_S01E01.json"
        cast_path.write_text('{"existing": true}', encoding="utf-8")
        output = str(tmp_path / "out.json")
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILP001", str(script_file), "--episode", "S01E01",
                "--output", output, "--quiet",
            ]):
                parser.main()
        finally:
            os.chdir(original_cwd)
        with open(str(cast_path), encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"existing": True}


# ─── Tests: generate_sfx_config ───

class TestGenerateSfxConfig:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "test_script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_creates_sfx_file_when_absent(self, parsed, tmp_path):
        sfx_path = str(tmp_path / "sfx_the413_S01E01.json")
        parser.generate_sfx_config(parsed, sfx_path)
        assert os.path.exists(sfx_path)

    def test_beat_is_silence_type(self, parsed, tmp_path):
        sfx_path = str(tmp_path / "sfx.json")
        parser.generate_sfx_config(parsed, sfx_path)
        with open(sfx_path, encoding="utf-8") as f:
            config = json.load(f)
        assert config["effects"]["BEAT"]["type"] == "silence"
        assert config["effects"]["BEAT"]["duration_seconds"] == 1.0

    def test_sfx_has_prompt(self, parsed, tmp_path):
        sfx_path = str(tmp_path / "sfx.json")
        parser.generate_sfx_config(parsed, sfx_path)
        with open(sfx_path, encoding="utf-8") as f:
            config = json.load(f)
        assert "prompt" in config["effects"]["SFX: DOOR OPENS"]

    def test_ambience_has_loop_true(self, parsed, tmp_path):
        sfx_path = str(tmp_path / "sfx.json")
        parser.generate_sfx_config(parsed, sfx_path)
        with open(sfx_path, encoding="utf-8") as f:
            config = json.load(f)
        ambience = config["effects"]["AMBIENCE: RADIO STATION"]
        assert ambience["loop"] is True
        assert ambience["duration_seconds"] == 30.0

    def test_music_duration(self, parsed, tmp_path):
        sfx_path = str(tmp_path / "sfx.json")
        parser.generate_sfx_config(parsed, sfx_path)
        with open(sfx_path, encoding="utf-8") as f:
            config = json.load(f)
        assert config["effects"]["MUSIC: THEME"]["duration_seconds"] == 15.0

    def test_sfx_metadata_from_script(self, parsed, tmp_path):
        sfx_path = str(tmp_path / "sfx.json")
        parser.generate_sfx_config(parsed, sfx_path)
        with open(sfx_path, encoding="utf-8") as f:
            config = json.load(f)
        assert config["show"] == "THE 413"
        assert config["season"] == 1
        assert config["episode"] == 1
        assert config["defaults"]["prompt_influence"] == 0.3

    def test_skips_when_sfx_exists(self, parsed, tmp_path, caplog):
        """main() should not overwrite existing sfx config."""
        script_file = tmp_path / "test_script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        sfx_path = tmp_path / "sfx_the413_S01E01.json"
        sfx_path.write_text('{"existing": true}', encoding="utf-8")
        # Also need cast to exist to avoid creating it
        cast_path = tmp_path / "cast_the413_S01E01.json"
        cast_path.write_text('{"existing": true}', encoding="utf-8")
        output = str(tmp_path / "out.json")
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILP001", str(script_file), "--episode", "S01E01",
                "--output", output, "--quiet",
            ]):
                parser.main()
        finally:
            os.chdir(original_cwd)
        with open(str(sfx_path), encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"existing": True}

    def test_unique_effects_only(self, parsed, tmp_path):
        """Duplicate direction entries should produce one effect."""
        sfx_path = str(tmp_path / "sfx.json")
        parser.generate_sfx_config(parsed, sfx_path)
        with open(sfx_path, encoding="utf-8") as f:
            config = json.load(f)
        # AMBIENCE: DINER appears via scene header split
        effect_keys = list(config["effects"].keys())
        assert len(effect_keys) == len(set(effect_keys))


# ─── Tests: strip_markdown_formatting ───

class TestStripMarkdownFormatting:
    def test_removes_bold_markers(self):
        assert parser.strip_markdown_formatting("**COLD OPEN**") == "COLD OPEN"

    def test_removes_h2_prefix(self):
        assert parser.strip_markdown_formatting("## **COLD OPEN**") == "COLD OPEN"

    def test_removes_h3_prefix(self):
        assert parser.strip_markdown_formatting("### **SCENE 1: THE THEATER**") == "SCENE 1: THE THEATER"

    def test_removes_h1_prefix(self):
        assert parser.strip_markdown_formatting("# THE 413 Season 1") == "THE 413 Season 1"

    def test_plain_text_unchanged(self):
        assert parser.strip_markdown_formatting("COLD OPEN") == "COLD OPEN"

    def test_bold_brackets(self):
        """Bold-wrapped brackets should become bare brackets."""
        assert parser.strip_markdown_formatting("**[SFX: DOOR]**") == "[SFX: DOOR]"

    def test_trailing_double_space_stripped(self):
        assert parser.strip_markdown_formatting("**MAYA**  ") == "MAYA"

    def test_multiline(self):
        """Full text with multiple lines is normalized."""
        text = "## **COLD OPEN**\n**[BEAT]**\n**MAYA**  \nPlain text"
        result = parser.strip_markdown_formatting(text)
        lines = result.split("\n")
        assert lines[0] == "COLD OPEN"
        assert lines[1] == "[BEAT]"
        assert lines[2] == "MAYA"
        assert lines[3] == "Plain text"


# ─── Tests: is_divider with markdown ───

class TestIsDividerMarkdown:
    def test_triple_dash_is_divider(self):
        assert parser.is_divider("---") is True

    def test_triple_equals_still_works(self):
        assert parser.is_divider("===") is True

    def test_regular_text_not_divider(self):
        assert parser.is_divider("some text") is False


# ─── Tests: end-of-episode variants ───

class TestEndOfEpisodeVariants:
    def test_end_of_production_script_stops_parsing(self, tmp_path):
        script = (
            "THE 413 Season 1: Episode 1: Test\n\n===\n\nCOLD OPEN\n\n"
            "ADAM Hello there.\n\nEND OF PRODUCTION SCRIPT\n\n"
            "ADAM This should not be parsed.\n"
        )
        script_file = tmp_path / "test.md"
        script_file.write_text(script, encoding="utf-8")
        parsed = parser.parse_script(str(script_file))
        all_text = " ".join(e["text"] for e in parsed["entries"] if e.get("text"))
        assert "Hello there" in all_text
        assert "should not be parsed" not in all_text


# ─── Tests: compound speaker matching ───

class TestTryMatchSpeakerCompound:
    def test_film_audio_compound(self):
        result = parser.try_match_speaker("FILM AUDIO (MARGARET'S VOICE)")
        assert result is not None
        assert result[0] == "film_audio"

    def test_stranger_compound(self):
        result = parser.try_match_speaker("STRANGER (MALE VOICE, FLAT)")
        assert result is not None
        assert result[0] == "stranger"

    def test_stranger_simple(self):
        result = parser.try_match_speaker("STRANGER Hello.")
        assert result is not None
        assert result[0] == "stranger"
        assert result[2] == "Hello."

    def test_karen_simple(self):
        result = parser.try_match_speaker("KAREN (clipped) Is this the young man?")
        assert result is not None
        assert result[0] == "karen"
        assert result[1] == "clipped"

    def test_sarah_simple(self):
        result = parser.try_match_speaker("SARAH (quiet) I'm here.")
        assert result is not None
        assert result[0] == "sarah"


# ─── Tests: OPENING CREDITS section ───

class TestSectionMapOpeningCredits:
    def test_opening_credits_recognized(self):
        assert parser.is_section_header("OPENING CREDITS") is True

    def test_opening_credits_slug(self):
        assert parser.SECTION_MAP["OPENING CREDITS"] == "opening-credits"


# ─── Integration: markdown-format script parsing ───

MARKDOWN_SCRIPT = """\
# THE 413 Season 1: Episode 2: "Reel to Real"

## **Full Production Script with SFX, Music Cues & Production Notes**

---

## **COLD OPEN**

**[AMBIENCE: MOVIE THEATER]**

**MAYA**
 (narration, internal voice)
 Here's the thing about working at a movie theater.

But tonight, I actually watched the movie.

**[SFX: FILM AUDIO]**

**FILM AUDIO (MARGARET'S VOICE)**
 (distant, through speakers)
 The menu was always yours.

**[BEAT]**

---

## **OPENING CREDITS**

**[SFX: RADIO STATIC]**

---

## **ACT ONE**

### **SCENE 1: THE THEATER**

**ADAM**
 (narration)
 I got to the theater at 2:30.

**KAREN**
 (clipped)
 Is this the young man?

(pause)

My name is Karen Ellis.

**STRANGER (MALE VOICE, FLAT)**
 I'm looking for the people asking about Margaret Ellis.

**[BEAT]**

**STRANGER**
 (speaking carefully)
 Some stories aren't meant to be told.

(beat)

Karen Ellis has spent forty years protecting her family.

---

## **END OF EPISODE 2**

---

## **PRODUCTION NOTES**

Should not appear.
"""


class TestParseMarkdownScript:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "test_md.md"
        script_file.write_text(MARKDOWN_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_top_level_metadata(self, parsed):
        assert parsed["show"] == "THE 413"
        assert parsed["season"] == 1
        assert parsed["episode"] == 2

    def test_dialogue_count(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        # MAYA x1, FILM AUDIO x1, ADAM x1, KAREN x1, STRANGER x2 = 6
        assert len(dialogue) == 6

    def test_speakers_found(self, parsed):
        expected = {"maya", "film_audio", "adam", "karen", "stranger"}
        assert set(parsed["stats"]["speakers"]) == expected

    def test_maya_continuation_merged(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        maya_line = [d for d in dialogue if d["speaker"] == "maya"][0]
        assert "working at a movie theater" in maya_line["text"]
        assert "actually watched the movie" in maya_line["text"]

    def test_maya_direction(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        maya_line = [d for d in dialogue if d["speaker"] == "maya"][0]
        assert maya_line["direction"] == "narration, internal voice"

    def test_film_audio_direction(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        fa_line = [d for d in dialogue if d["speaker"] == "film_audio"][0]
        assert fa_line["direction"] == "distant, through speakers"

    def test_inline_direction_not_in_text(self, parsed):
        """Standalone (pause) and (beat) should not appear in dialogue text."""
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        karen_line = [d for d in dialogue if d["speaker"] == "karen"][0]
        assert "(pause)" not in karen_line["text"]
        assert "Karen Ellis" in karen_line["text"]

    def test_stranger_no_direction_line(self, parsed):
        """STRANGER (MALE VOICE, FLAT) with no separate direction line."""
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        stranger_lines = [d for d in dialogue if d["speaker"] == "stranger"]
        first = stranger_lines[0]
        assert "looking for" in first["text"]

    def test_stranger_with_direction(self, parsed):
        """STRANGER with a (direction) on separate line."""
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        stranger_lines = [d for d in dialogue if d["speaker"] == "stranger"]
        second = stranger_lines[1]
        assert second["direction"] == "speaking carefully"
        assert "stories aren't meant" in second["text"]

    def test_stranger_beat_continuation_filtered(self, parsed):
        """Standalone (beat) within STRANGER continuation not in text."""
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        stranger_lines = [d for d in dialogue if d["speaker"] == "stranger"]
        second = stranger_lines[1]
        assert "(beat)" not in second["text"]
        assert "Karen Ellis" in second["text"]

    def test_opening_credits_section(self, parsed):
        assert "opening-credits" in parsed["stats"]["sections"]

    def test_sections_tracked(self, parsed):
        sections = parsed["stats"]["sections"]
        assert "cold-open" in sections
        assert "opening-credits" in sections
        assert "act1" in sections

    def test_metadata_excluded(self, parsed):
        all_text = " ".join(e["text"] for e in parsed["entries"] if e.get("text"))
        assert "Should not appear" not in all_text

    def test_no_bold_markers_in_output(self, parsed):
        """No ** markers should leak into any entry text."""
        for entry in parsed["entries"]:
            if entry.get("text"):
                assert "**" not in entry["text"], f"Bold in seq {entry['seq']}: {entry['text'][:50]}"

    def test_sequence_numbers_ascending(self, parsed):
        seqs = [e["seq"] for e in parsed["entries"]]
        assert seqs == sorted(seqs)
        assert len(seqs) == len(set(seqs))


# ─── Integration: full production script E02 ───

FULL_SCRIPT_E02_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "scripts",
    "Full Production Script THE 413 Season 1 _ Episode 2_ _Reel to Real_ Arc_ _The Holiday Shift_ 1_11_26 CLAUDE.AI PROJECT THE 413.md"
)


@pytest.mark.skipif(not os.path.exists(FULL_SCRIPT_E02_PATH), reason="E02 production script not present")
class TestParseFullScriptE02:
    @pytest.fixture
    def parsed(self):
        return parser.parse_script(FULL_SCRIPT_E02_PATH)

    def test_speakers_include_new_characters(self, parsed):
        speakers = set(parsed["stats"]["speakers"])
        assert "karen" in speakers
        assert "stranger" in speakers
        assert "film_audio" in speakers

    def test_dialogue_line_count_in_range(self, parsed):
        assert 50 <= parsed["stats"]["dialogue_lines"] <= 120

    def test_six_sections(self, parsed):
        # COLD OPEN, OPENING CREDITS, ACT ONE, MID-EPISODE BREAK, ACT TWO, CLOSING
        assert len(parsed["stats"]["sections"]) == 6

    def test_season_and_episode(self, parsed):
        assert parsed["season"] == 1
        assert parsed["episode"] == 2

    def test_no_bold_markers_in_dialogue(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        for d in dialogue:
            assert "**" not in d["text"], f"Bold in seq {d['seq']}: {d['text'][:50]}"


# ─── Integration: full production script E03 ───

FULL_SCRIPT_E03_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "scripts",
    "Full Production Script THE 413 Season 1 _ Episode 3_ _The Long Way Home_ Arc_ _The Holiday Shift_ 1_11_26 CLAUDE.AI PROJECT THE 413.md"
)


@pytest.mark.skipif(not os.path.exists(FULL_SCRIPT_E03_PATH), reason="E03 production script not present")
class TestParseFullScriptE03:
    @pytest.fixture
    def parsed(self):
        return parser.parse_script(FULL_SCRIPT_E03_PATH)

    def test_speakers_include_sarah(self, parsed):
        assert "sarah" in set(parsed["stats"]["speakers"])

    def test_dialogue_line_count_in_range(self, parsed):
        assert 50 <= parsed["stats"]["dialogue_lines"] <= 200

    def test_season_and_episode(self, parsed):
        assert parsed["season"] == 1
        assert parsed["episode"] == 3

    def test_no_bold_markers_in_dialogue(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        for d in dialogue:
            assert "**" not in d["text"], f"Bold in seq {d['seq']}: {d['text'][:50]}"


# ─── Unit: S02 new speakers and sections ───

class TestS02Speakers:
    def test_tina_in_known_speakers(self):
        assert "TINA" in parser.KNOWN_SPEAKERS

    def test_martha_in_known_speakers(self):
        assert "MARTHA" in parser.KNOWN_SPEAKERS

    def test_gerald_in_known_speakers(self):
        assert "GERALD" in parser.KNOWN_SPEAKERS

    def test_elena_in_known_speakers(self):
        assert "ELENA" in parser.KNOWN_SPEAKERS

    def test_rian_s02_spelling_in_known_speakers(self):
        assert "RÍÁN" in parser.KNOWN_SPEAKERS

    def test_tina_speaker_key(self):
        assert parser.SPEAKER_KEYS["TINA"] == "tina"

    def test_martha_speaker_key(self):
        assert parser.SPEAKER_KEYS["MARTHA"] == "martha"

    def test_gerald_speaker_key(self):
        assert parser.SPEAKER_KEYS["GERALD"] == "gerald"

    def test_elena_speaker_key(self):
        assert parser.SPEAKER_KEYS["ELENA"] == "elena"

    def test_rian_s02_spelling_maps_to_rian(self):
        assert parser.SPEAKER_KEYS["RÍÁN"] == "rian"

    def test_post_interview_in_section_map(self):
        assert "POST-INTERVIEW" in parser.SECTION_MAP

    def test_post_interview_slug(self):
        assert parser.SECTION_MAP["POST-INTERVIEW"] == "post-interview"

    def test_closing_radio_station_in_section_map(self):
        assert "CLOSING — RADIO STATION" in parser.SECTION_MAP

    def test_closing_radio_station_slug(self):
        assert parser.SECTION_MAP["CLOSING — RADIO STATION"] == "closing"


POST_INTERVIEW_SCRIPT = """\
# THE 413 Season 2: Episode 1: "The Return"

===

## POST-INTERVIEW

**ADAM**
Hi. I am Adam Santos, and I am here with Tina Brissette.

Tina, this episode feels different from Season 1.

**TINA**
That's intentional. Season 2 is about return.

**ADAM**
Sarah came back with letters.

**TINA**
And that's a different kind of mystery.
"""


class TestPostInterviewParse:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "test_s02e01.md"
        script_file.write_text(POST_INTERVIEW_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_section_is_post_interview(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        for d in dialogue:
            assert d["section"] == "post-interview", (
                f"seq {d['seq']} has section={d['section']!r}, expected 'post-interview'"
            )

    def test_tina_lines_parse_as_tina(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        tina_lines = [d for d in dialogue if d["speaker"] == "tina"]
        assert len(tina_lines) == 2

    def test_adam_lines_parse_as_adam(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        adam_lines = [d for d in dialogue if d["speaker"] == "adam"]
        assert len(adam_lines) == 2

    def test_tina_text_not_in_adam_dialogue(self, parsed):
        """Tina's spoken lines must not be appended to Adam's text."""
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        adam_lines = [d for d in dialogue if d["speaker"] == "adam"]
        for d in adam_lines:
            assert "That's intentional" not in d["text"]
            assert "different kind of mystery" not in d["text"]

    def test_no_speaker_names_in_dialogue_text(self, parsed):
        dialogue = [e for e in parsed["entries"] if e["type"] == "dialogue"]
        for d in dialogue:
            assert "TINA" not in d["text"], f"Speaker name in text: {d['text'][:60]}"
            assert "ADAM" not in d["text"], f"Speaker name in text: {d['text'][:60]}"


# ─── Unit: S02E03 new speakers, sections, divider fix ───

class TestS02E03Fixtures:
    def test_margaret_vo_in_known_speakers(self):
        assert "MARGARET (V.O.)" in parser.KNOWN_SPEAKERS

    def test_margaret_vo_before_plain_margaret(self):
        # Compound name must appear before plain MARGARET for longest-first matching
        idx_vo = parser.KNOWN_SPEAKERS.index("MARGARET (V.O.)")
        idx_plain = parser.KNOWN_SPEAKERS.index("MARGARET")
        assert idx_vo < idx_plain

    def test_margaret_vo_speaker_key(self):
        assert parser.SPEAKER_KEYS["MARGARET (V.O.)"] == "margaret"

    def test_clerk_in_known_speakers(self):
        assert "CLERK" in parser.KNOWN_SPEAKERS

    def test_clerk_speaker_key(self):
        assert parser.SPEAKER_KEYS["CLERK"] == "clerk"

    def test_post_credits_scene_in_section_map(self):
        assert "POST-CREDITS SCENE" in parser.SECTION_MAP

    def test_post_credits_scene_slug(self):
        assert parser.SECTION_MAP["POST-CREDITS SCENE"] == "post-credits"

    def test_dez_closing_narration_in_section_map(self):
        assert "DEZ'S CLOSING NARRATION" in parser.SECTION_MAP

    def test_dez_closing_narration_slug(self):
        assert parser.SECTION_MAP["DEZ'S CLOSING NARRATION"] == "dez-closing"

    def test_production_notes_in_section_map(self):
        assert "PRODUCTION NOTES" in parser.SECTION_MAP

    def test_five_dash_divider_recognized(self):
        assert parser.is_divider("-----") is True

    def test_five_equals_divider_recognized(self):
        assert parser.is_divider("=====") is True

    def test_three_dash_divider_still_recognized(self):
        assert parser.is_divider("---") is True

    def test_three_equals_divider_still_recognized(self):
        assert parser.is_divider("===") is True

    def test_mixed_divider_not_recognized(self):
        assert parser.is_divider("-==-") is False


# ─── Tests: compute_speaker_stats ───

class TestComputeSpeakerStats:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_returns_list_of_dicts(self, parsed):
        stats = parser.compute_speaker_stats(parsed)
        assert isinstance(stats, list)
        assert all(isinstance(r, dict) for r in stats)

    def test_all_speakers_present(self, parsed):
        stats = parser.compute_speaker_stats(parsed)
        speakers = {r["speaker"] for r in stats}
        assert "adam" in speakers
        assert "dez" in speakers

    def test_has_required_keys(self, parsed):
        stats = parser.compute_speaker_stats(parsed)
        for r in stats:
            assert "speaker" in r
            assert "lines" in r
            assert "words" in r
            assert "chars" in r
            assert "pct_lines" in r
            assert "pct_words" in r
            assert "pct_chars" in r

    def test_percentages_sum_to_100(self, parsed):
        stats = parser.compute_speaker_stats(parsed)
        assert abs(sum(r["pct_lines"] for r in stats) - 100.0) < 0.5
        assert abs(sum(r["pct_words"] for r in stats) - 100.0) < 0.5
        assert abs(sum(r["pct_chars"] for r in stats) - 100.0) < 0.5

    def test_sorted_by_lines_descending(self, parsed):
        stats = parser.compute_speaker_stats(parsed)
        lines = [r["lines"] for r in stats]
        assert lines == sorted(lines, reverse=True)

    def test_word_count_is_positive(self, parsed):
        stats = parser.compute_speaker_stats(parsed)
        for r in stats:
            if r["lines"] > 0:
                assert r["words"] > 0


class TestPrintSpeakerStats:
    @pytest.fixture
    def parsed(self, tmp_path):
        script_file = tmp_path / "script.md"
        script_file.write_text(MINIMAL_SCRIPT, encoding="utf-8")
        return parser.parse_script(str(script_file))

    def test_prints_header_and_speakers(self, parsed, caplog):
        parser.print_speaker_stats(parsed)
        assert "Speaker" in caplog.text
        assert "Lines" in caplog.text
        assert "Words" in caplog.text
        assert "Chars" in caplog.text
        assert "adam" in caplog.text

    def test_prints_total_row(self, parsed, caplog):
        parser.print_speaker_stats(parsed)
        assert "TOTAL" in caplog.text

    def test_prints_percentages(self, parsed, caplog):
        parser.print_speaker_stats(parsed)
        assert "%" in caplog.text
