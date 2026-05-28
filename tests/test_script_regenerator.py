# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP009_script_regenerator.py."""

import pytest

from xil_pipeline.XILP009_script_regenerator import (
    regenerate_script,
    section_display_name,
    speaker_display_name,
)

# --- Helper fixtures ---

@pytest.fixture
def minimal_parsed():
    """Minimal parsed JSON with one section and two dialogue entries."""
    return {
        "show": "THE 413",
        "season": 2,
        "episode": 3,
        "title": "The Bridge",
        "season_title": "The Letters",
        "entries": [
            {
                "seq": 1,
                "type": "section_header",
                "section": "cold-open",
                "scene": None,
                "speaker": None,
                "direction": None,
                "text": "COLD OPEN",
                "direction_type": None,
            },
            {
                "seq": 2,
                "type": "direction",
                "section": "cold-open",
                "scene": None,
                "speaker": None,
                "direction": None,
                "text": "AMBIENCE: RADIO BOOTH",
                "direction_type": "AMBIENCE",
            },
            {
                "seq": 3,
                "type": "dialogue",
                "section": "cold-open",
                "scene": None,
                "speaker": "adam",
                "direction": "narration",
                "text": "It was a dark and stormy night.",
                "direction_type": None,
            },
            {
                "seq": 4,
                "type": "direction",
                "section": "cold-open",
                "scene": None,
                "speaker": None,
                "direction": None,
                "text": "BEAT",
                "direction_type": "BEAT",
            },
            {
                "seq": 5,
                "type": "dialogue",
                "section": "cold-open",
                "scene": None,
                "speaker": "maya",
                "direction": None,
                "text": "I can't believe this.",
                "direction_type": None,
            },
        ],
        "stats": {"dialogue_lines": 2, "total_entries": 5},
    }


# --- Unit tests ---


class TestSectionDisplayName:
    def test_known_slug(self):
        assert section_display_name("cold-open") == "COLD OPEN"

    def test_act_slug_prefers_word_form(self):
        # "ACT ONE" is longer than "ACT 1", so the reverse mapping prefers it
        assert section_display_name("act1") == "ACT ONE"

    def test_unknown_slug_falls_back(self):
        result = section_display_name("bonus-content")
        assert result == "BONUS CONTENT"


class TestSpeakerDisplayName:
    def test_known_speaker(self):
        assert speaker_display_name("adam") == "ADAM"

    def test_rian_uses_fada(self):
        # Should use the canonical form with fada
        display = speaker_display_name("rian")
        assert "R" in display
        assert display in ("RÍAN", "RÍÁN")

    def test_unknown_speaker(self):
        assert speaker_display_name("zack") == "ZACK"


class TestRegenerateScript:
    def test_header_line(self, minimal_parsed):
        result = regenerate_script(minimal_parsed)
        # No markup — plain text header, no leading #
        assert 'THE 413 Season 2: Episode 3: "The Bridge" Arc: "The Letters"' in result
        assert '# THE 413' not in result

    def test_section_header(self, minimal_parsed):
        result = regenerate_script(minimal_parsed)
        # Section header is plain text, not a markdown heading
        assert "COLD OPEN" in result
        assert "## COLD OPEN" not in result

    def test_divider_before_header(self, minimal_parsed):
        result = regenerate_script(minimal_parsed)
        lines = result.split("\n")
        # Find COLD OPEN, then verify === immediately precedes it
        header_idx = next(i for i, l in enumerate(lines) if l.strip() == "COLD OPEN")
        preceding_non_blank = [l for l in lines[:header_idx] if l.strip()]
        assert preceding_non_blank[-1] == "==="

    def test_direction_in_brackets(self, minimal_parsed):
        result = regenerate_script(minimal_parsed)
        assert "[AMBIENCE: RADIO BOOTH]" in result
        assert "[BEAT]" in result

    def test_dialogue_with_direction(self, minimal_parsed):
        result = regenerate_script(minimal_parsed)
        assert "ADAM (narration)" in result
        assert "It was a dark and stormy night." in result

    def test_dialogue_without_direction(self, minimal_parsed):
        result = regenerate_script(minimal_parsed)
        lines = result.split("\n")
        # Find MAYA line — should NOT have parenthetical
        maya_idx = next(i for i, l in enumerate(lines) if l.strip() == "MAYA")
        assert lines[maya_idx + 1] == "I can't believe this."

    def test_end_marker(self, minimal_parsed):
        result = regenerate_script(minimal_parsed)
        assert "END OF EPISODE" in result

    def test_preamble_excluded(self, minimal_parsed):
        """Preamble entries (seq < 0) should not appear in output."""
        minimal_parsed["entries"].insert(0, {
            "seq": -2,
            "type": "dialogue",
            "section": "preamble",
            "scene": None,
            "speaker": "tina",
            "direction": None,
            "text": "This is the Berkshire Talking Chronicle.",
            "direction_type": None,
        })
        result = regenerate_script(minimal_parsed)
        assert "Berkshire Talking Chronicle" not in result

    def test_postamble_included(self, minimal_parsed):
        """Postamble is a first-class script section — must appear in output."""
        minimal_parsed["entries"].append({
            "seq": 100,
            "type": "section_header",
            "section": "postamble",
            "scene": None,
            "speaker": None,
            "direction": None,
            "text": "POSTAMBLE",
            "direction_type": None,
        })
        minimal_parsed["entries"].append({
            "seq": 101,
            "type": "dialogue",
            "section": "postamble",
            "scene": None,
            "speaker": "tina",
            "direction": None,
            "text": "Thank you for listening.",
            "direction_type": None,
        })
        result = regenerate_script(minimal_parsed)
        assert "Thank you for listening." in result
        assert "POSTAMBLE" in result

    def test_scene_header(self):
        """Scene headers are plain text within a section — no ## prefix, no === divider."""
        parsed = {
            "show": "THE 413",
            "season": 1,
            "episode": 1,
            "title": "Test",
            "entries": [
                {
                    "seq": 1,
                    "type": "section_header",
                    "section": "act1",
                    "scene": None,
                    "speaker": None,
                    "direction": None,
                    "text": "ACT ONE",
                    "direction_type": None,
                },
                {
                    "seq": 2,
                    "type": "scene_header",
                    "section": "act1",
                    "scene": "scene-1",
                    "speaker": None,
                    "direction": None,
                    "text": "SCENE 1: MORRISON'S DINER",
                    "direction_type": None,
                },
                {
                    "seq": 3,
                    "type": "dialogue",
                    "section": "act1",
                    "scene": "scene-1",
                    "speaker": "adam",
                    "direction": None,
                    "text": "Hello.",
                    "direction_type": None,
                },
            ],
            "stats": {"dialogue_lines": 1, "total_entries": 3},
        }
        result = regenerate_script(parsed)
        # Section header: plain text, no ##
        assert "ACT ONE" in result
        assert "## ACT ONE" not in result
        # Scene header: plain text, no ##
        assert "SCENE 1: MORRISON'S DINER" in result
        assert "## SCENE 1: MORRISON'S DINER" not in result
        # === appears only once (before ACT ONE), not before the scene header
        assert result.count("===") == 1

    def test_cast_block(self):
        """CAST block is emitted when cast config has a characters dict."""
        parsed = {
            "show": "THE 413",
            "season": 1,
            "episode": 1,
            "title": "Test",
            "entries": [],
            "stats": {"dialogue_lines": 0, "total_entries": 0},
        }
        cast = {
            "cast": {
                "adam": {"full_name": "Adam", "role": "Host, late-night radio DJ"},
                "dez": {"full_name": "Dez", "role": "Adam's sister"},
            }
        }
        result = regenerate_script(parsed, cast=cast)
        assert "CAST:" in result
        assert "* Adam — Host, late-night radio DJ" in result
        assert "* Dez — Adam's sister" in result

    def test_no_cast_block_without_cast(self):
        """CAST block is omitted when no cast config is provided."""
        parsed = {
            "show": "THE 413",
            "season": 1,
            "episode": 1,
            "title": "Test",
            "entries": [],
            "stats": {"dialogue_lines": 0, "total_entries": 0},
        }
        result = regenerate_script(parsed)
        assert "CAST:" not in result

    def test_no_season(self):
        """Script without season should omit 'Season N:' from header."""
        parsed = {
            "show": "THE 413",
            "season": None,
            "episode": 5,
            "title": "Standalone",
            "entries": [],
            "stats": {"dialogue_lines": 0, "total_entries": 0},
        }
        result = regenerate_script(parsed)
        assert "Season" not in result
        assert "Episode 5:" in result
