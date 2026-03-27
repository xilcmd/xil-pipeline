"""Tests for XILU002_generate_SFX.py — standalone SFX stem generation utility."""

import json
import os
import unittest.mock

import pytest

# Patch out ElevenLabs client before loading module (no API key needed for these tests)
with unittest.mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key"}):
    with unittest.mock.patch("elevenlabs.client.ElevenLabs"):
        from xil_pipeline import XILU002_generate_SFX as generate_sfx


# ─── Fixtures ───

@pytest.fixture
def sample_cast(tmp_path):
    cast = {
        "show": "TEST SHOW", "season": 1, "episode": 1,
        "title": "Test", "cast": {},
    }
    cast_file = tmp_path / "cast_the413_S01E01.json"
    cast_file.write_text(json.dumps(cast), encoding="utf-8")
    (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))
    return str(cast_file)


@pytest.fixture
def sample_sfx(tmp_path):
    sfx = {
        "show": "TEST SHOW", "season": 1, "episode": 1,
        "defaults": {"prompt_influence": 0.3},
        "effects": {
            "AMBIENCE: RADIO STATION": {
                "prompt": "Late night radio station ambience",
                "duration_seconds": 30.0,
                "loop": True,
            },
            "SFX: PHONE BUZZING": {
                "prompt": "Phone vibrating buzz",
                "duration_seconds": 2.0,
                "prompt_influence": 0.5,
            },
            "BEAT": {
                "type": "silence",
                "duration_seconds": 1.0,
            },
            "MUSIC: SHOW THEME": {
                "prompt": "Eerie indie folk theme",
                "duration_seconds": 15.0,
            },
        },
    }
    sfx_file = tmp_path / "sfx_the413_S01E01.json"
    sfx_file.write_text(json.dumps(sfx), encoding="utf-8")
    return str(sfx_file)


@pytest.fixture
def sample_script(tmp_path):
    script = {
        "show": "TEST SHOW", "episode": 1, "title": "Test",
        "entries": [
            {"seq": 1, "type": "section_header", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "COLD OPEN", "direction_type": None},
            {"seq": 2, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "AMBIENCE: RADIO STATION", "direction_type": "AMBIENCE"},
            {"seq": 3, "type": "dialogue", "section": "cold-open",
             "scene": None, "speaker": "adam", "direction": None,
             "text": "Hello.", "direction_type": None},
            {"seq": 4, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "BEAT", "direction_type": "BEAT"},
            {"seq": 5, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "SFX: PHONE BUZZING", "direction_type": "SFX"},
            {"seq": 6, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "MUSIC: SHOW THEME", "direction_type": "MUSIC"},
        ],
        "stats": {},
    }
    script_file = tmp_path / "parsed" / "parsed_the413_S01E01.json"
    script_file.parent.mkdir()
    script_file.write_text(json.dumps(script), encoding="utf-8")
    return str(script_file)


# ─── Tests: module import ───

class TestModuleImport:
    def test_importable(self):
        assert generate_sfx is not None

    def test_has_main(self):
        assert hasattr(generate_sfx, "main")

    def test_delegates_to_sfx_common(self):
        """XILU002 should import shared functions from sfx_common."""
        from xil_pipeline import sfx_common
        assert generate_sfx.load_sfx_entries is sfx_common.load_sfx_entries
        assert generate_sfx.generate_sfx is sfx_common.generate_sfx
        assert generate_sfx.dry_run_sfx is sfx_common.dry_run_sfx


# ─── Tests: load_sfx_plan ───

class TestLoadSfxPlan:
    def test_returns_entries_and_stems_dir(self, sample_script, sample_sfx, sample_cast):
        entries, stems_dir = generate_sfx.load_sfx_plan(
            sample_script, sample_sfx, sample_cast,
        )
        assert isinstance(entries, list)
        assert len(entries) == 4  # AMBIENCE + BEAT + SFX + MUSIC
        assert "S01E01" in stems_dir

    def test_max_duration_filters_long_effects(self, sample_script, sample_sfx, sample_cast):
        entries, _ = generate_sfx.load_sfx_plan(
            sample_script, sample_sfx, sample_cast, max_duration=5.0,
        )
        texts = [e["text"] for e in entries]
        assert "SFX: PHONE BUZZING" in texts
        assert "BEAT" in texts
        assert "AMBIENCE: RADIO STATION" not in texts
        assert "MUSIC: SHOW THEME" not in texts

    def test_max_duration_none_includes_all(self, sample_script, sample_sfx, sample_cast):
        entries, _ = generate_sfx.load_sfx_plan(
            sample_script, sample_sfx, sample_cast, max_duration=None,
        )
        assert len(entries) == 4

    def test_stems_dir_uses_cast_tag(self, sample_script, sample_sfx, sample_cast):
        _, stems_dir = generate_sfx.load_sfx_plan(
            sample_script, sample_sfx, sample_cast,
        )
        assert stems_dir.endswith(os.path.join("stems", "S01E01"))


# ─── Tests: main() CLI wiring ───

class TestMainCli:
    def test_dry_run_flag(self, sample_script, sample_sfx, sample_cast, tmp_path, capsys):
        stems_base = str(tmp_path / "stems")
        original = generate_sfx.STEMS_DIR
        generate_sfx.STEMS_DIR = stems_base
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILU002", "--episode", "S01E01",
                "--script", sample_script, "--dry-run",
            ]):
                generate_sfx.main()
        finally:
            generate_sfx.STEMS_DIR = original
            os.chdir(original_cwd)
        out = capsys.readouterr().out
        assert "DRY RUN" in out

    def test_max_duration_flag(self, sample_script, sample_sfx, sample_cast, tmp_path, capsys):
        stems_base = str(tmp_path / "stems")
        original = generate_sfx.STEMS_DIR
        generate_sfx.STEMS_DIR = stems_base
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILU002", "--episode", "S01E01",
                "--script", sample_script, "--dry-run", "--max-duration", "5.0",
            ]):
                generate_sfx.main()
        finally:
            generate_sfx.STEMS_DIR = original
            os.chdir(original_cwd)
        out = capsys.readouterr().out
        assert "AMBIENCE: RADIO STATION" not in out
        assert "MUSIC: SHOW THEME" not in out
        assert "SFX: PHONE BUZZING" in out or "BEAT" in out

    def test_generate_creates_shared_and_episode_stems(
        self, sample_script, sample_sfx, sample_cast, tmp_path,
    ):
        stems_base = str(tmp_path / "stems")
        original = generate_sfx.STEMS_DIR
        generate_sfx.STEMS_DIR = stems_base
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILU002", "--episode", "S01E01",
                "--script", sample_script, "--max-duration", "1.0",
            ]):
                generate_sfx.main()
        finally:
            generate_sfx.STEMS_DIR = original
            os.chdir(original_cwd)
        # BEAT (1.0s) is the only effect ≤ 1.0s — silence, no API
        assert (tmp_path / "stems" / "S01E01" / "004_cold-open_sfx.mp3").exists()
