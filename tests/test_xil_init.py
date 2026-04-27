# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for xil_init scaffold command."""

import json
import os

import pytest

from xil_pipeline.xil_init import SAMPLE_SPEAKERS, scaffold


@pytest.fixture
def workspace(tmp_path):
    """Scaffold a default workspace and return its path."""
    target = str(tmp_path / "test-show")
    scaffold(target, "Test Show")
    return target


def test_scaffold_creates_project_json(workspace):
    path = os.path.join(workspace, "project.json")
    assert os.path.exists(path)
    with open(path) as f:
        data = json.load(f)
    assert data["show"] == "Test Show"


def test_scaffold_creates_speakers_json(workspace):
    path = os.path.join(workspace, "configs", "testshow", "speakers.json")
    assert os.path.exists(path)
    with open(path) as f:
        data = json.load(f)
    assert data == SAMPLE_SPEAKERS
    # All entries have required keys
    for entry in data:
        assert "display" in entry
        assert "key" in entry
    # Default podcast type has host and caller
    keys = [e["key"] for e in data]
    assert "host" in keys
    assert "caller" in keys


def test_scaffold_creates_sample_script(workspace):
    path = os.path.join(workspace, "scripts", "sample_S01E01.md")
    assert os.path.exists(path)
    with open(path) as f:
        content = f.read()
    # Show name appears in header; no Season N: when not specified
    assert content.startswith("Test Show Episode 1:")
    assert "Season" not in content.splitlines()[0]
    # Contains expected structure
    assert "COLD OPEN" in content
    assert "ACT ONE" in content
    assert "END OF EPISODE" in content


def test_scaffold_creates_subdirectories(workspace):
    for subdir in ("scripts", "parsed", "stems", "SFX", "daw", "masters", "cues"):
        assert os.path.isdir(os.path.join(workspace, subdir))


def test_scaffold_custom_show_name(tmp_path):
    target = str(tmp_path / "custom")
    scaffold(target, "Night Owls")
    with open(os.path.join(target, "project.json")) as f:
        data = json.load(f)
    assert data["show"] == "Night Owls"
    with open(os.path.join(target, "scripts", "sample_S01E01.md")) as f:
        content = f.read()
    assert content.startswith("Night Owls Episode 1:")


def test_scaffold_with_season_and_title(tmp_path):
    """--season and --season-title are written to project.json and the sample script."""
    target = str(tmp_path / "arc-show")
    scaffold(target, "Night Owls", season=2, season_title="The Bridge")
    with open(os.path.join(target, "project.json")) as f:
        data = json.load(f)
    assert data["season"] == 2
    assert data["season_title"] == "The Bridge"
    with open(os.path.join(target, "scripts", "sample_S01E01.md")) as f:
        header = f.readline()
    assert "Season 2:" in header
    assert 'Arc: "The Bridge"' in header


def test_scaffold_skips_existing_files(workspace):
    """Re-running scaffold should not overwrite existing files."""
    # Modify project.json
    pj_path = os.path.join(workspace, "project.json")
    with open(pj_path, "w") as f:
        json.dump({"show": "Modified"}, f)

    # Re-scaffold
    scaffold(workspace, "Test Show")

    # Original modification should be preserved
    with open(pj_path) as f:
        data = json.load(f)
    assert data["show"] == "Modified"


def test_scaffold_into_current_dir(tmp_path, monkeypatch):
    """Scaffolding into '.' (default) works."""
    monkeypatch.chdir(tmp_path)
    scaffold(str(tmp_path), "Dot Show")
    assert os.path.exists(os.path.join(str(tmp_path), "project.json"))


def test_sample_script_parses_with_speakers(workspace, monkeypatch):
    """The sample script should parse cleanly through XILP001 with the sample speakers."""
    from xil_pipeline.XILP001_script_parser import load_speakers, parse_script

    # CWD must be workspace so resolve_season/resolve_season_title read workspace's project.json
    monkeypatch.chdir(workspace)

    speakers_path = os.path.join(workspace, "configs", "testshow", "speakers.json")
    script_path = os.path.join(workspace, "scripts", "sample_S01E01.md")

    # Load speakers from the scaffolded speakers.json
    known, keys = load_speakers(speakers_path)
    assert "HOST" in known
    assert "CO-HOST" in known
    assert "CALLER" in known

    # Parse the script
    parsed = parse_script(script_path, speakers_path=speakers_path)

    assert parsed["show"] == "Test Show"
    assert parsed["season"] is None   # no --season passed to scaffold
    assert parsed["episode"] == 1
    assert parsed["title"] == "Pilot"

    # Should have dialogue entries for at least two speakers
    speakers_found = set(
        e["speaker"] for e in parsed["entries"] if e["type"] == "dialogue"
    )
    assert "host" in speakers_found
    assert "caller" in speakers_found

    # Should have directions (SFX, AMBIENCE, BEAT, MUSIC)
    direction_types = set(
        e["direction_type"] for e in parsed["entries"]
        if e["type"] == "direction" and e["direction_type"]
    )
    assert "SFX" in direction_types
    assert "AMBIENCE" in direction_types
    assert "BEAT" in direction_types
    assert "MUSIC" in direction_types

    # Should have sections
    sections = parsed["stats"]["sections"]
    assert "cold-open" in sections
    assert "act1" in sections
