# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP000_script_scanner.py — pre-flight script analysis tool."""

import json
import unittest.mock

import pytest

from xil_pipeline import XILP000_script_scanner as scanner

# ─── Fixtures ───

SIMPLE_SCRIPT = """\
# THE 413 Season 2: Episode 1: "The Return"

===

## COLD OPEN

**ADAM**
Hello, world.

**MAYA**
Hello back.

===

## ACT ONE

**ADAM**
Let's go.
"""

UNKNOWN_SPEAKER_SCRIPT = """\
# THE 413 Season 2: Episode 1: "The Return"

===

## COLD OPEN

**ADAM**
Hello.

**BOBSWORTH**
I am unknown.
"""

UNKNOWN_SECTION_SCRIPT = """\
# THE 413 Season 2: Episode 1: "The Return"

===

## ACT SEVEN

**ADAM**
In an unrecognized section.
"""

DIRECTIONS_SCRIPT = """\
# THE 413 Season 2: Episode 1: "The Return"

===

## COLD OPEN

[BEAT]

[SFX: PHONE RINGING]

[MUSIC: FADES IN]

**ADAM**
Spoken line.
"""

MULTI_OCCURRENCE_SCRIPT = """\
# THE 413 Season 2: Episode 1: "The Return"

===

## COLD OPEN

**ADAM**
First line.

**ADAM**
Second line.

**ADAM**
Third line.
"""


def _scan(script_text, tmp_path):
    """Helper: write script to tmp file and return scan result."""
    p = tmp_path / "test_script.md"
    p.write_text(script_text, encoding="utf-8")
    lines = scanner.load_and_normalize(str(p))
    return scanner.scan_script(lines)


# ─── Tests: is_all_caps_candidate ───

class TestIsAllCapsCandidate:
    def test_bare_speaker_name(self):
        assert scanner.is_all_caps_candidate("ADAM") is True

    def test_divider_not_candidate(self):
        assert scanner.is_all_caps_candidate("===") is False
        assert scanner.is_all_caps_candidate("---") is False

    def test_stage_direction_not_candidate(self):
        assert scanner.is_all_caps_candidate("[BEAT]") is False
        assert scanner.is_all_caps_candidate("[SFX: PHONE]") is False

    def test_scene_header_not_candidate(self):
        assert scanner.is_all_caps_candidate("SCENE 1: THE DINER") is False

    def test_empty_string_not_candidate(self):
        assert scanner.is_all_caps_candidate("") is False

    def test_single_char_not_candidate(self):
        assert scanner.is_all_caps_candidate("A") is False

    def test_lowercase_not_candidate(self):
        assert scanner.is_all_caps_candidate("Hello World") is False

    def test_mixed_case_not_candidate(self):
        assert scanner.is_all_caps_candidate("Adam Santos") is False

    def test_compound_name_is_candidate(self):
        assert scanner.is_all_caps_candidate("FILM AUDIO (MARGARET'S VOICE)") is True

    def test_section_header_is_candidate(self):
        assert scanner.is_all_caps_candidate("POST-INTERVIEW") is True
        assert scanner.is_all_caps_candidate("COLD OPEN") is True


# ─── Tests: load_and_normalize ───

class TestLoadAndNormalize:
    def test_strips_bold_markers(self, tmp_path):
        p = tmp_path / "s.md"
        p.write_text("**ADAM**\nHello.", encoding="utf-8")
        lines = scanner.load_and_normalize(str(p))
        assert "ADAM" in lines
        assert "**ADAM**" not in lines

    def test_strips_heading_markers(self, tmp_path):
        p = tmp_path / "s.md"
        p.write_text("## COLD OPEN\n", encoding="utf-8")
        lines = scanner.load_and_normalize(str(p))
        assert "COLD OPEN" in lines

    def test_empty_lines_preserved(self, tmp_path):
        p = tmp_path / "s.md"
        p.write_text("A\n\nB\n", encoding="utf-8")
        lines = scanner.load_and_normalize(str(p))
        assert "" in lines


# ─── Tests: scan_script — speakers ───

class TestScanScriptSpeakers:
    def test_recognizes_known_speaker_plain(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        assert "adam" in result["speakers"]

    def test_recognizes_known_speaker_markdown_bold(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        assert "maya" in result["speakers"]

    def test_flags_unknown_speaker(self, tmp_path):
        result = _scan(UNKNOWN_SPEAKER_SCRIPT, tmp_path)
        unrecognized_texts = [u["text"] for u in result["unrecognized"]]
        assert "BOBSWORTH" in unrecognized_texts

    def test_known_speaker_not_in_unrecognized(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        unrecognized_texts = [u["text"] for u in result["unrecognized"]]
        assert "ADAM" not in unrecognized_texts
        assert "MAYA" not in unrecognized_texts

    def test_scan_counts_speaker_occurrences(self, tmp_path):
        result = _scan(MULTI_OCCURRENCE_SCRIPT, tmp_path)
        assert result["speakers"]["adam"]["count"] == 3

    def test_speaker_entry_has_line_numbers(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        assert len(result["speakers"]["adam"]["lines"]) >= 1


# ─── Tests: scan_script — sections ───

class TestScanScriptSections:
    def test_recognizes_known_section(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        section_texts = [s["text"] for s in result["sections"]]
        assert "COLD OPEN" in section_texts

    def test_flags_unknown_section(self, tmp_path):
        result = _scan(UNKNOWN_SECTION_SCRIPT, tmp_path)
        unrecognized_texts = [u["text"] for u in result["unrecognized"]]
        assert "ACT SEVEN" in unrecognized_texts

    def test_known_section_not_in_unrecognized(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        unrecognized_texts = [u["text"] for u in result["unrecognized"]]
        assert "COLD OPEN" not in unrecognized_texts

    def test_section_entry_has_slug(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        cold_open = next(s for s in result["sections"] if s["text"] == "COLD OPEN")
        assert cold_open["slug"] == "cold-open"

    def test_multiple_sections_captured(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        section_texts = [s["text"] for s in result["sections"]]
        assert "COLD OPEN" in section_texts
        assert "ACT ONE" in section_texts


# ─── Tests: scan_script — directions skipped ───

class TestScanScriptDirections:
    def test_stage_directions_not_candidates(self, tmp_path):
        result = _scan(DIRECTIONS_SCRIPT, tmp_path)
        unrecognized_texts = [u["text"] for u in result["unrecognized"]]
        assert "BEAT" not in unrecognized_texts
        assert "[BEAT]" not in unrecognized_texts
        assert "[SFX: PHONE RINGING]" not in unrecognized_texts


# ─── Tests: scan_paralinguistic_tags ───

class TestScanParalinguisticTags:
    def test_exact_turbo_tags_not_flagged(self):
        lines = ["[laugh] that's funny", "[clear throat] as I was saying", "[sarcastic] lovely"]
        assert scanner.scan_paralinguistic_tags(lines) == []

    def test_case_insensitive_exact_match_not_flagged(self):
        assert scanner.scan_paralinguistic_tags(["[LAUGH] ha", "[Angry] no"]) == []

    def test_plural_form_flagged_with_suggestion(self):
        found = scanner.scan_paralinguistic_tags(["he [laughs] loudly"])
        assert len(found) == 1
        assert found[0]["text"] == "laughs"
        assert found[0]["suggestion"] == "laugh"
        assert found[0]["lines"] == [1]

    def test_clear_throat_variants_flagged(self):
        for variant in ("clears throat", "throat clearing", "clearing throat"):
            found = scanner.scan_paralinguistic_tags([f"[{variant}] ahem"])
            assert [f["suggestion"] for f in found] == ["clear throat"], variant

    def test_elevenlabs_only_tags_not_flagged(self):
        # These are legitimate under --backend elevenlabs and are silently
        # stripped by Turbo; they must not produce near-miss noise.
        lines = ["I'm so [exhausted]", "wait [pause] for it", "[curious] hmm", "[french accent] bonjour"]
        assert scanner.scan_paralinguistic_tags(lines) == []

    def test_stage_directions_ignored(self):
        lines = ["[SFX: PHONE RINGING]", "[BEAT]", "[LONG BEAT]", "[AMBIENCE: DINER]",
                 "[VINTAGE FILTER ENGAGES]", "[END OF EPISODE]"]
        assert scanner.scan_paralinguistic_tags(lines) == []

    def test_tag_at_start_of_dialogue_line_is_scanned(self):
        # is_stage_direction() would treat this whole line as a direction, so
        # the scan must filter per token or it misses the most common placement.
        found = scanner.scan_paralinguistic_tags(["[laughs] Oh, that's rich."])
        assert [f["suggestion"] for f in found] == ["laugh"]

    def test_repeat_occurrences_collapse_to_one_entry(self):
        found = scanner.scan_paralinguistic_tags(["he [laughs]", "filler", "she [laughs] too"])
        assert len(found) == 1
        assert found[0]["lines"] == [1, 3]

    def test_report_flags_near_miss_but_stays_non_fatal(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        result["paralinguistic_near_misses"] = [
            {"text": "laughs", "suggestion": "laugh", "lines": [12]}
        ]
        report = scanner.format_report(result, {})
        assert "laughs" in report and "laugh]" in report
        # Near-misses are advisory — they must not flip the verdict.
        assert "safe to run XILP001" in report


# ─── Tests: format_report ───

class TestFormatReport:
    def test_report_contains_section_names(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        report = scanner.format_report(result, {"show": "THE 413", "season": 2, "episode": 1, "title": "The Return"})
        assert "COLD OPEN" in report
        assert "ACT ONE" in report

    def test_report_contains_speaker_keys(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        report = scanner.format_report(result, {})
        assert "adam" in report.lower() or "ADAM" in report

    def test_report_flags_unknown_speaker(self, tmp_path):
        result = _scan(UNKNOWN_SPEAKER_SCRIPT, tmp_path)
        report = scanner.format_report(result, {})
        assert "BOBSWORTH" in report

    def test_report_clean_script_shows_ok(self, tmp_path):
        result = _scan(SIMPLE_SCRIPT, tmp_path)
        report = scanner.format_report(result, {})
        assert "safe to run xilp001" in report.lower() or "0 unrecognized" in report

    def test_report_dirty_script_shows_warning(self, tmp_path):
        result = _scan(UNKNOWN_SPEAKER_SCRIPT, tmp_path)
        report = scanner.format_report(result, {})
        assert "unrecognized" in report.lower()


# ─── Tests: CLI main ───

class TestMainCLI:
    def test_main_prints_report(self, tmp_path, caplog):
        p = tmp_path / "s.md"
        p.write_text(SIMPLE_SCRIPT, encoding="utf-8")
        with unittest.mock.patch("sys.argv", ["XILP000", str(p)]):
            scanner.main()
        assert "COLD OPEN" in caplog.text
        assert "adam" in caplog.text.lower() or "ADAM" in caplog.text

    def test_main_json_flag_returns_valid_json(self, tmp_path, capsys):
        p = tmp_path / "s.md"
        p.write_text(SIMPLE_SCRIPT, encoding="utf-8")
        with unittest.mock.patch("sys.argv", ["XILP000", str(p), "--json"]):
            scanner.main()
        # JSON is printed to stdout via print() (machine-readable output)
        out = capsys.readouterr().out
        json_start = out.find("{")
        json_end = out.rfind("}") + 1
        data = json.loads(out[json_start:json_end])
        assert "speakers" in data
        assert "sections" in data
        assert "unrecognized" in data

    def test_main_exits_nonzero_on_unknown(self, tmp_path):
        p = tmp_path / "s.md"
        p.write_text(UNKNOWN_SPEAKER_SCRIPT, encoding="utf-8")
        with unittest.mock.patch("sys.argv", ["XILP000", str(p)]):
            with pytest.raises(SystemExit) as exc:
                scanner.main()
        assert exc.value.code != 0


# ─── Tests: span pairing (VINTAGE FILTER / FILM AUDIO) ───

class TestSpanPairing:
    def test_film_audio_paired_is_clean(self):
        lines = ["[FILM AUDIO ENGAGES]", "some dialogue", "[FILM AUDIO DISENGAGES]"]
        assert scanner.scan_film_audio_pairing(lines) == []

    def test_film_audio_colon_form_is_paired(self):
        lines = ["[FILM AUDIO: ENGAGES]", "[FILM AUDIO: DISENGAGES]"]
        assert scanner.scan_film_audio_pairing(lines) == []

    def test_film_audio_unclosed_engages_is_reported(self):
        found = scanner.scan_film_audio_pairing(["[FILM AUDIO ENGAGES]", "text"])
        assert [f["type"] for f in found] == ["ENGAGES"]

    def test_film_audio_orphan_disengages_is_reported(self):
        found = scanner.scan_film_audio_pairing(["[FILM AUDIO DISENGAGES]"])
        assert [f["type"] for f in found] == ["DISENGAGES"]

    def test_vintage_filter_colon_form_still_ignored(self):
        """Deliberate: widening this would newly fail scripts that pass today."""
        lines = ["[VINTAGE FILTER: ENGAGES]"]
        assert scanner.scan_vintage_filter_pairing(lines) == []

    def test_vintage_filter_colonless_unclosed_is_reported(self):
        found = scanner.scan_vintage_filter_pairing(["[VINTAGE FILTER ENGAGES]"])
        assert [f["type"] for f in found] == ["ENGAGES"]

    def test_film_audio_markers_do_not_match_vintage_scan(self):
        lines = ["[FILM AUDIO ENGAGES]"]
        assert scanner.scan_vintage_filter_pairing(lines) == []
