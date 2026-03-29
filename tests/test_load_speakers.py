# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for speaker loading and externalization."""

import json

from xil_pipeline.XILP001_script_parser import (
    _BUILTIN_KNOWN_SPEAKERS,
    _BUILTIN_SPEAKER_KEYS,
    load_speakers,
    parse_script_header,
    try_match_speaker,
)


class TestLoadSpeakers:
    """Test load_speakers() resolution and parsing."""

    def test_fallback_to_builtins(self, tmp_path, monkeypatch):
        """When no speakers.json exists, returns built-in defaults."""
        monkeypatch.chdir(tmp_path)  # No speakers.json here
        known, keys = load_speakers()
        assert known == _BUILTIN_KNOWN_SPEAKERS
        assert keys == _BUILTIN_SPEAKER_KEYS

    def test_load_from_explicit_path(self, tmp_path):
        """Loading from an explicit path works."""
        speakers = [
            {"display": "ALICE", "key": "alice"},
            {"display": "BOB", "key": "bob"},
        ]
        path = str(tmp_path / "speakers.json")
        with open(path, "w") as f:
            json.dump(speakers, f)

        known, keys = load_speakers(path)
        assert "ALICE" in known
        assert "BOB" in known
        assert keys["ALICE"] == "alice"
        assert keys["BOB"] == "bob"

    def test_load_from_cwd(self, tmp_path, monkeypatch):
        """Auto-detects speakers.json in CWD."""
        speakers = [{"display": "NARRATOR", "key": "narrator"}]
        with open(tmp_path / "speakers.json", "w") as f:
            json.dump(speakers, f)

        monkeypatch.chdir(tmp_path)
        known, keys = load_speakers()
        assert known == ["NARRATOR"]
        assert keys == {"NARRATOR": "narrator"}

    def test_explicit_path_overrides_cwd(self, tmp_path, monkeypatch):
        """Explicit path takes precedence over CWD speakers.json."""
        # CWD has one set of speakers
        with open(tmp_path / "speakers.json", "w") as f:
            json.dump([{"display": "CWD_SPEAKER", "key": "cwd"}], f)

        # Explicit path has another
        explicit = str(tmp_path / "custom_speakers.json")
        with open(explicit, "w") as f:
            json.dump([{"display": "EXPLICIT_SPEAKER", "key": "explicit"}], f)

        monkeypatch.chdir(tmp_path)
        known, keys = load_speakers(explicit)
        assert "EXPLICIT_SPEAKER" in known
        assert "CWD_SPEAKER" not in known

    def test_longest_first_sorting(self, tmp_path):
        """Speakers are sorted longest-first for compound name matching."""
        speakers = [
            {"display": "AL", "key": "al"},
            {"display": "AL CAPONE", "key": "al_capone"},
            {"display": "AL CAPONE (DISGUISED)", "key": "al_capone"},
        ]
        path = str(tmp_path / "speakers.json")
        with open(path, "w") as f:
            json.dump(speakers, f)

        known, keys = load_speakers(path)
        assert known[0] == "AL CAPONE (DISGUISED)"
        assert known[1] == "AL CAPONE"
        assert known[2] == "AL"


class TestTryMatchSpeakerWithCustomSpeakers:
    """Test try_match_speaker with custom speaker lists."""

    def test_match_custom_speakers(self):
        known = ["HOST", "CALLER"]
        keys = {"HOST": "host", "CALLER": "caller"}
        result = try_match_speaker("HOST Hello everyone!", known, keys)
        assert result is not None
        assert result[0] == "host"
        assert result[2] == "Hello everyone!"

    def test_no_match_unknown_speaker(self):
        known = ["HOST"]
        keys = {"HOST": "host"}
        result = try_match_speaker("UNKNOWN Hello!", known, keys)
        assert result is None

    def test_match_with_direction(self):
        known = ["HOST"]
        keys = {"HOST": "host"}
        result = try_match_speaker("HOST (whispering) Be quiet.", known, keys)
        assert result is not None
        assert result[0] == "host"
        assert result[1] == "whispering"
        assert result[2] == "Be quiet."

    def test_defaults_to_module_globals(self):
        """When no speakers passed, uses module-level KNOWN_SPEAKERS."""
        result = try_match_speaker("ADAM Hello there.")
        assert result is not None
        assert result[0] == "adam"


class TestParseScriptHeader:
    """Test generic header detection."""

    def test_standard_header(self):
        result = parse_script_header("THE 413 Season 2: Episode 3: \"The Bridge\"")
        assert result is not None
        show, season, episode, title = result
        assert show == "THE 413"
        assert season == 2
        assert episode == 3
        assert title == "The Bridge"

    def test_generic_show_header(self):
        result = parse_script_header("Night Owls Season 1: Episode 1: \"Pilot\"")
        assert result is not None
        show, season, episode, title = result
        assert show == "Night Owls"
        assert season == 1
        assert episode == 1
        assert title == "Pilot"

    def test_no_episode_returns_none(self):
        """Lines without 'Episode N' are not headers."""
        result = parse_script_header("COLD OPEN")
        assert result is None

    def test_no_season(self):
        result = parse_script_header("My Show Episode 5: \"Title\"")
        assert result is not None
        show, season, episode, title = result
        assert show == "My Show"
        assert season is None
        assert episode == 5

    def test_empty_line(self):
        result = parse_script_header("")
        assert result is None
