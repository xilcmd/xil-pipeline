# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP012_publish.py — Social Media Post Draft Generator."""

import json
import os
import unittest.mock

from xil_pipeline import XILP012_publish as mod

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_PARSED = {
    "show": "THE 413",
    "season": 4,
    "episode": 1,
    "title": "The Summer Before",
    "season_title": "The Search",
    "source_file": "scripts/sample.md",
    "entries": [
        {"seq": 1, "type": "section_header", "section": "cold-open", "scene": None, "speaker": None, "text": "COLD OPEN", "direction_type": None},
        {"seq": 2, "type": "scene_header", "section": "cold-open", "scene": "scene-1", "speaker": None, "text": "SCENE 1: WRRS RADIO BOOTH — 2:00 AM", "direction_type": None},
        {"seq": 3, "type": "direction", "section": "cold-open", "scene": "scene-1", "speaker": None, "text": "AMBIENCE: BOOTH HUM", "direction_type": "AMBIENCE"},
        {"seq": 4, "type": "dialogue", "section": "cold-open", "scene": "scene-1", "speaker": "adam", "direction": "warm", "text": "It's 2:00 AM in the Berkshires.", "direction_type": None},
        {"seq": 5, "type": "dialogue", "section": "cold-open", "scene": "scene-1", "speaker": "adam", "direction": None, "text": "The Echo arc gave us a lot.", "direction_type": None},
        {"seq": 6, "type": "dialogue", "section": "cold-open", "scene": "scene-1", "speaker": "maya", "direction": None, "text": "Tonight is about that summer.", "direction_type": None},
        {"seq": 7, "type": "dialogue", "section": "cold-open", "scene": "scene-1", "speaker": "adam", "direction": None, "text": "A fourth dialogue line — should not appear in excerpt.", "direction_type": None},
        {"seq": 8, "type": "section_header", "section": "act1", "scene": None, "speaker": None, "text": "ACT ONE", "direction_type": None},
        {"seq": 9, "type": "dialogue", "section": "act1", "scene": "scene-2", "speaker": "maya", "direction": None, "text": "Act one dialogue.", "direction_type": None},
    ],
    "stats": {
        "total_entries": 9,
        "dialogue_lines": 5,
        "direction_lines": 1,
        "characters_for_tts": 100,
        "speakers": ["adam", "maya"],
        "sections": ["cold-open", "act1"],
    },
}

_CAST_CFG = {
    "show": "THE 413",
    "season": 4,
    "episode": 1,
    "title": "The Summer Before",
    "cast": {
        "adam": {"full_name": "Adam Santos", "voice_id": "abc", "role": "Host/Narrator"},
        "maya": {"full_name": "Maya Chen", "voice_id": "def", "role": "Staff Reporter"},
        "tina": {"full_name": "Tina Brissette", "voice_id": "ghi", "role": "Announcer"},
    },
}


# ---------------------------------------------------------------------------
# extract_episode_summary
# ---------------------------------------------------------------------------


def test_extract_cold_open_lines():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    # Only first 3 dialogue lines from cold-open section
    assert len(summary["cold_open_lines"]) == 3
    assert summary["cold_open_lines"][0]["speaker"] == "adam"
    assert summary["cold_open_lines"][0]["text"] == "It's 2:00 AM in the Berkshires."


def test_extract_cold_open_scene():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    assert summary["cold_open_scene"] == "SCENE 1: WRRS RADIO BOOTH — 2:00 AM"


def test_extract_cast_filters_to_stats_speakers():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    keys = [c["key"] for c in summary["cast"]]
    # tina is not in stats.speakers — should be excluded
    assert "tina" not in keys
    assert "adam" in keys
    assert "maya" in keys


def test_extract_cast_full_names():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    by_key = {c["key"]: c for c in summary["cast"]}
    assert by_key["adam"]["full_name"] == "Adam Santos"
    assert by_key["adam"]["role"] == "Host/Narrator"


def test_extract_section_arc():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    assert "Cold Open" in summary["section_arc"]
    assert "Act One" in summary["section_arc"]


def test_extract_basic_fields():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    assert summary["show"] == "THE 413"
    assert summary["tag"] == "S04E01"
    assert summary["title"] == "The Summer Before"
    assert summary["season_title"] == "The Search"


def test_extract_no_cast_cfg():
    summary = mod.extract_episode_summary(_PARSED, None)
    assert summary["cast"] == []


def test_extract_runtime_no_master():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG, master_path=None)
    assert summary["runtime_minutes"] is None


def test_extract_runtime_missing_file(tmp_path):
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG, master_path=str(tmp_path / "missing.mp3"))
    assert summary["runtime_minutes"] is None


# ---------------------------------------------------------------------------
# build_user_message
# ---------------------------------------------------------------------------


def test_build_user_message_contains_key_fields():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    msg = mod.build_user_message(summary, "facebook", spotlight_index=0)
    assert "THE 413" in msg
    assert "S04E01" in msg
    assert "The Summer Before" in msg
    assert "Adam Santos" in msg
    assert "Maya Chen" in msg
    assert "## Hype Post" in msg
    assert "## Quote Post" in msg
    assert "## Spotlight Post" in msg


def test_build_user_message_spotlight_cycles():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    # Episode 1 → index 0 → first cast member (adam)
    msg0 = mod.build_user_message(summary, "facebook", spotlight_index=0)
    assert "Adam Santos" in msg0
    # Episode 2 → index 1 → second cast member (maya)
    msg1 = mod.build_user_message(summary, "facebook", spotlight_index=1)
    assert "Maya Chen" in msg1


def test_build_user_message_cold_open_excerpt():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    msg = mod.build_user_message(summary, "facebook", spotlight_index=0)
    assert "It's 2:00 AM in the Berkshires." in msg
    # 4th dialogue line must NOT appear
    assert "A fourth dialogue line" not in msg


def test_build_user_message_instagram_platform():
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    msg = mod.build_user_message(summary, "instagram", spotlight_index=0)
    assert "instagram" in msg.lower()


# ---------------------------------------------------------------------------
# write_posts_file
# ---------------------------------------------------------------------------


def test_write_posts_file_creates_file(tmp_path):
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    output_path = str(tmp_path / "posts" / "the413" / "S04E01_posts.md")
    mod.write_posts_file(output_path, "## Hype Post\nContent here.\n", summary, "facebook")
    assert os.path.exists(output_path)
    text = open(output_path).read()
    assert "THE 413" in text
    assert "S04E01" in text
    assert "## Hype Post" in text


def test_write_posts_file_adds_newline(tmp_path):
    summary = mod.extract_episode_summary(_PARSED, _CAST_CFG)
    output_path = str(tmp_path / "posts" / "the413" / "S04E01_posts.md")
    mod.write_posts_file(output_path, "Content without trailing newline", summary, "facebook")
    text = open(output_path).read()
    assert text.endswith("\n")


# ---------------------------------------------------------------------------
# publish_episode — mock API call
# ---------------------------------------------------------------------------


def test_publish_episode_dry_run_no_api_call(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Write parsed JSON
    parsed_dir = tmp_path / "parsed" / "the413"
    parsed_dir.mkdir(parents=True)
    (parsed_dir / "parsed_S04E01.json").write_text(json.dumps(_PARSED))
    # Write cast config
    cast_dir = tmp_path / "configs" / "the413"
    cast_dir.mkdir(parents=True)
    (cast_dir / "cast_S04E01.json").write_text(json.dumps(_CAST_CFG))

    with unittest.mock.patch("xil_pipeline.XILP012_publish.call_claude_api") as mock_api:
        result = mod.publish_episode("the413", "S04E01", dry_run=True)

    assert result is True
    mock_api.assert_not_called()
    # No posts file written in dry-run
    assert not (tmp_path / "posts" / "the413" / "S04E01_posts.md").exists()


def test_publish_episode_calls_api_and_writes_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed_dir = tmp_path / "parsed" / "the413"
    parsed_dir.mkdir(parents=True)
    (parsed_dir / "parsed_S04E01.json").write_text(json.dumps(_PARSED))
    cast_dir = tmp_path / "configs" / "the413"
    cast_dir.mkdir(parents=True)
    (cast_dir / "cast_S04E01.json").write_text(json.dumps(_CAST_CFG))

    fake_response = "## Hype Post\nNew episode!\n\n## Quote Post\nQuote.\n\n## Spotlight Post\nSpot.\n"
    with unittest.mock.patch("xil_pipeline.XILP012_publish.call_claude_api", return_value=fake_response) as mock_api:
        result = mod.publish_episode("the413", "S04E01", dry_run=False)

    assert result is True
    mock_api.assert_called_once()
    posts_path = tmp_path / "posts" / "the413" / "S04E01_posts.md"
    assert posts_path.exists()
    text = posts_path.read_text()
    assert "## Hype Post" in text


def test_publish_episode_missing_parsed_returns_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = mod.publish_episode("the413", "S04E01", dry_run=False)
    assert result is False


def test_publish_episode_api_error_returns_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed_dir = tmp_path / "parsed" / "the413"
    parsed_dir.mkdir(parents=True)
    (parsed_dir / "parsed_S04E01.json").write_text(json.dumps(_PARSED))
    cast_dir = tmp_path / "configs" / "the413"
    cast_dir.mkdir(parents=True)
    (cast_dir / "cast_S04E01.json").write_text(json.dumps(_CAST_CFG))

    with unittest.mock.patch("xil_pipeline.XILP012_publish.call_claude_api", side_effect=Exception("API error")):
        result = mod.publish_episode("the413", "S04E01", dry_run=False)

    assert result is False


# ---------------------------------------------------------------------------
# _find_all_parsed
# ---------------------------------------------------------------------------


def test_find_all_parsed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    parsed_dir = tmp_path / "parsed" / "the413"
    parsed_dir.mkdir(parents=True)
    (parsed_dir / "parsed_S01E01.json").write_text("{}")
    (parsed_dir / "parsed_S02E01.json").write_text("{}")
    # orig_parsed should be excluded
    (parsed_dir / "orig_parsed_S02E01.json").write_text("{}")

    paths = mod._find_all_parsed("the413")
    basenames = [os.path.basename(p) for p in paths]
    assert "parsed_S01E01.json" in basenames
    assert "parsed_S02E01.json" in basenames
    assert "orig_parsed_S02E01.json" not in basenames


# ---------------------------------------------------------------------------
# Section slug conversion
# ---------------------------------------------------------------------------


def test_section_slug_to_label_known():
    assert mod._section_slug_to_label("cold-open") == "Cold Open"
    assert mod._section_slug_to_label("act1") == "Act One"
    assert mod._section_slug_to_label("mid-break") == "Mid-Episode Break"


def test_section_slug_to_label_unknown():
    label = mod._section_slug_to_label("custom-section")
    assert label == "Custom Section"
