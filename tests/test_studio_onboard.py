# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP004_studio_onboard.py — ElevenLabs Studio project onboarding."""

import json
import os
import unittest.mock

import pytest

# Patch out ElevenLabs client before loading module (no API key needed for these tests)
with unittest.mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key"}):
    with unittest.mock.patch("elevenlabs.client.ElevenLabs"):
        from xil_pipeline import XILP004_studio_onboard as onboard


# ─── Fixtures ───

SAMPLE_CAST = {
    "show": "THE 413",
    "season": 1,
    "episode": 2,
    "title": "Reel to Real",
    "cast": {
        "adam": {
            "full_name": "Adam Santos",
            "voice_id": "voice_adam",
            "pan": 0.0,
            "filter": False,
            "role": "Host/Narrator",
        },
        "maya": {
            "full_name": "Maya Chen",
            "voice_id": "voice_maya",
            "pan": 0.20,
            "filter": False,
            "role": "Supporting",
        },
        "dez": {
            "full_name": "Dez Williams",
            "voice_id": "voice_dez",
            "pan": -0.15,
            "filter": False,
            "role": "Supporting",
        },
    },
}

SAMPLE_PARSED = {
    "show": "THE 413",
    "season": 1,
    "episode": 2,
    "title": "Reel to Real",
    "source_file": "test.md",
    "entries": [
        {"seq": 1, "type": "section_header", "section": "cold-open", "scene": None,
         "speaker": None, "direction": None, "text": "COLD OPEN", "direction_type": None},
        {"seq": 2, "type": "direction", "section": "cold-open", "scene": None,
         "speaker": None, "direction": None, "text": "AMBIENCE: RADIO STATION",
         "direction_type": "AMBIENCE"},
        {"seq": 3, "type": "dialogue", "section": "cold-open", "scene": None,
         "speaker": "adam", "direction": "on-air voice",
         "text": "It's 2:47 AM on a Wednesday.", "direction_type": None},
        {"seq": 4, "type": "direction", "section": "cold-open", "scene": None,
         "speaker": None, "direction": None, "text": "BEAT", "direction_type": "BEAT"},
        {"seq": 5, "type": "dialogue", "section": "cold-open", "scene": None,
         "speaker": "adam", "direction": "continuing",
         "text": "If you're listening right now, you're awake.", "direction_type": None},
        {"seq": 6, "type": "section_header", "section": "act1", "scene": None,
         "speaker": None, "direction": None, "text": "ACT ONE", "direction_type": None},
        {"seq": 7, "type": "scene_header", "section": "act1", "scene": "scene-1",
         "speaker": None, "direction": None, "text": "SCENE 1: THE DINER",
         "direction_type": None},
        {"seq": 8, "type": "dialogue", "section": "act1", "scene": "scene-1",
         "speaker": "maya", "direction": "excited",
         "text": "I found something incredible.", "direction_type": None},
        {"seq": 9, "type": "dialogue", "section": "act1", "scene": "scene-1",
         "speaker": "dez", "direction": None,
         "text": "What is it?", "direction_type": None},
    ],
    "stats": {
        "total_entries": 9, "dialogue_lines": 4,
        "direction_lines": 2, "characters_for_tts": 100,
        "speakers": ["adam", "dez", "maya"],
        "sections": ["act1", "cold-open"],
    },
}


# ─── Tests: build_content_json ───

class TestBuildContentJson:
    def test_returns_list_of_chapters(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        assert isinstance(chapters, list)
        assert len(chapters) > 0

    def test_section_creates_chapter(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        names = [ch["name"] for ch in chapters]
        assert "COLD OPEN" in names
        assert "ACT ONE" in names

    def test_multiple_sections_multiple_chapters(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        assert len(chapters) == 2

    def test_dialogue_becomes_tts_node(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        # First chapter (COLD OPEN) should have dialogue blocks
        cold_open = chapters[0]
        p_blocks = [b for b in cold_open["blocks"] if b["sub_type"] == "p"]
        assert len(p_blocks) >= 1
        node = p_blocks[0]["nodes"][0]
        assert node["type"] == "tts_node"
        assert node["text"] == "It's 2:47 AM on a Wednesday."

    def test_voice_id_from_cast_config(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        cold_open = chapters[0]
        p_blocks = [b for b in cold_open["blocks"] if b["sub_type"] == "p"]
        node = p_blocks[0]["nodes"][0]
        assert node["voice_id"] == "voice_adam"

    def test_scene_header_becomes_h2(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        act_one = [ch for ch in chapters if ch["name"] == "ACT ONE"][0]
        h2_blocks = [b for b in act_one["blocks"] if b["sub_type"] == "h2"]
        assert len(h2_blocks) == 1
        assert h2_blocks[0]["nodes"][0]["text"] == "SCENE 1: THE DINER"

    def test_directions_skipped(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        all_texts = []
        for ch in chapters:
            for block in ch["blocks"]:
                for node in block["nodes"]:
                    all_texts.append(node["text"])
        assert "AMBIENCE: RADIO STATION" not in all_texts
        assert "BEAT" not in all_texts

    def test_continuation_text_intact(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        cold_open = chapters[0]
        p_blocks = [b for b in cold_open["blocks"] if b["sub_type"] == "p"]
        texts = [b["nodes"][0]["text"] for b in p_blocks]
        assert "If you're listening right now, you're awake." in texts

    def test_no_speaker_names_in_text(self):
        """Speaker names should never appear in tts_node text."""
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        for ch in chapters:
            for block in ch["blocks"]:
                for node in block["nodes"]:
                    assert not node["text"].startswith("ADAM ")
                    assert not node["text"].startswith("MAYA ")
                    assert not node["text"].startswith("DEZ ")

    def test_maya_voice_in_act_one(self):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        act_one = [ch for ch in chapters if ch["name"] == "ACT ONE"][0]
        p_blocks = [b for b in act_one["blocks"] if b["sub_type"] == "p"]
        maya_blocks = [b for b in p_blocks if b["nodes"][0]["voice_id"] == "voice_maya"]
        assert len(maya_blocks) == 1
        assert maya_blocks[0]["nodes"][0]["text"] == "I found something incredible."


# ─── Tests: load_episode ───

class TestLoadEpisode:
    def test_loads_parsed_and_cast(self, tmp_path):
        (tmp_path / "parsed").mkdir(exist_ok=True)
        parsed_path = tmp_path / "parsed" / "parsed_the413_S01E02.json"
        cast_path = tmp_path / "cast_the413_S01E02.json"
        parsed_path.write_text(json.dumps(SAMPLE_PARSED), encoding="utf-8")
        cast_path.write_text(json.dumps(SAMPLE_CAST), encoding="utf-8")
        (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            parsed, cast = onboard.load_episode("S01E02")
        finally:
            os.chdir(original_cwd)
        assert parsed["episode"] == 2
        assert "adam" in cast["cast"]

    def test_aborts_on_tbd_voice_id(self, tmp_path):
        cast_with_tbd = dict(SAMPLE_CAST)
        cast_with_tbd["cast"] = dict(SAMPLE_CAST["cast"])
        cast_with_tbd["cast"]["adam"] = dict(SAMPLE_CAST["cast"]["adam"])
        cast_with_tbd["cast"]["adam"]["voice_id"] = "TBD"
        (tmp_path / "parsed").mkdir(exist_ok=True)
        parsed_path = tmp_path / "parsed" / "parsed_the413_S01E02.json"
        cast_path = tmp_path / "cast_the413_S01E02.json"
        parsed_path.write_text(json.dumps(SAMPLE_PARSED), encoding="utf-8")
        cast_path.write_text(json.dumps(cast_with_tbd), encoding="utf-8")
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with pytest.raises(SystemExit):
                onboard.load_episode("S01E02")
        finally:
            os.chdir(original_cwd)

    def test_missing_parsed_file_raises(self, tmp_path):
        cast_path = tmp_path / "cast_the413_S01E02.json"
        cast_path.write_text(json.dumps(SAMPLE_CAST), encoding="utf-8")
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with pytest.raises(SystemExit):
                onboard.load_episode("S01E02")
        finally:
            os.chdir(original_cwd)


# ─── Tests: dry_run ───

class TestDryRun:
    def test_prints_chapter_summary(self, caplog):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        onboard.dry_run(chapters, SAMPLE_CAST)
        assert "COLD OPEN" in caplog.text
        assert "ACT ONE" in caplog.text

    def test_prints_voice_assignments(self, caplog):
        chapters = onboard.build_content_json(SAMPLE_PARSED, SAMPLE_CAST)
        onboard.dry_run(chapters, SAMPLE_CAST)
        assert "adam" in caplog.text.lower() or "Adam" in caplog.text


# ─── Tests: CLI main ───

class TestMainCLI:
    def test_dry_run_mode(self, tmp_path):
        (tmp_path / "parsed").mkdir(exist_ok=True)
        parsed_path = tmp_path / "parsed" / "parsed_the413_S01E02.json"
        cast_path = tmp_path / "cast_the413_S01E02.json"
        parsed_path.write_text(json.dumps(SAMPLE_PARSED), encoding="utf-8")
        cast_path.write_text(json.dumps(SAMPLE_CAST), encoding="utf-8")
        (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILP004", "--episode", "S01E02", "--dry-run",
            ]):
                onboard.main()
        finally:
            os.chdir(original_cwd)

    def test_create_calls_api(self, tmp_path):
        """In non-dry-run mode, the API should be called."""
        (tmp_path / "parsed").mkdir(exist_ok=True)
        parsed_path = tmp_path / "parsed" / "parsed_the413_S01E02.json"
        cast_path = tmp_path / "cast_the413_S01E02.json"
        parsed_path.write_text(json.dumps(SAMPLE_PARSED), encoding="utf-8")
        cast_path.write_text(json.dumps(SAMPLE_CAST), encoding="utf-8")
        (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            mock_response = unittest.mock.MagicMock()
            mock_response.project.project_id = "proj_test_123"
            with unittest.mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key"}):
                with unittest.mock.patch("sys.argv", [
                    "XILP004", "--episode", "S01E02",
                ]):
                    with unittest.mock.patch.object(
                        onboard, "create_project", return_value=mock_response
                    ) as mock_create:
                        with unittest.mock.patch.object(onboard, "check_elevenlabs_quota", return_value=50000):
                            onboard.main()
                            mock_create.assert_called_once()
        finally:
            os.chdir(original_cwd)
