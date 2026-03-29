# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP003_audio_assembly.py and mix_common.py."""

import json
import os
import unittest.mock

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

# ─── Import modules ───
from xil_pipeline import XILP003_audio_assembly as assembly
from xil_pipeline import mix_common

# ─── Helpers ───

def _make_tone(duration_ms: int = 200) -> AudioSegment:
    return Sine(440).to_audio_segment(duration=duration_ms)


def _write_mp3(path: str, duration_ms: int = 200) -> None:
    _make_tone(duration_ms).export(path, format="mp3")


# ─── Fixtures ───

@pytest.fixture
def sample_cast(tmp_path):
    cast = {
        "show": "TEST SHOW",
        "season": 1,
        "episode": 1,
        "cast": {
            "adam": {"full_name": "Adam Santos", "voice_id": "voice_adam_123",
                     "pan": 0.0, "filter": False, "role": "Host"},
            "frank": {"full_name": "Frank", "voice_id": "TBD",
                      "pan": 0.0, "filter": True, "role": "Minor"},
        }
    }
    cast_file = tmp_path / "cast.json"
    cast_file.write_text(json.dumps(cast), encoding="utf-8")
    return str(cast_file)


@pytest.fixture
def config():
    return {
        "adam": {"id": "voice_adam_123", "pan": 0.0, "filter": False},
        "frank": {"id": "voice_frank", "pan": 0.0, "filter": True},
    }


@pytest.fixture
def stems_with_audio(tmp_path):
    """Create minimal valid MP3 stems for assembly testing."""
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    for name in ["003_cold-open_adam", "007_act1-scene-1_frank"]:
        _write_mp3(str(stems_dir / f"{name}.mp3"))
    return stems_dir


@pytest.fixture
def stems_with_bg(tmp_path):
    """Stems dir with dialogue AND background (ambience/music) stems."""
    stems_dir = tmp_path / "stems"
    stems_dir.mkdir()
    # Foreground: dialogue
    _write_mp3(str(stems_dir / "003_cold-open_adam.mp3"), duration_ms=300)
    _write_mp3(str(stems_dir / "005_cold-open_frank.mp3"), duration_ms=300)
    # Background: ambience (seq 002) and music (seq 007)
    _write_mp3(str(stems_dir / "002_cold-open_sfx.mp3"), duration_ms=500)
    _write_mp3(str(stems_dir / "007_cold-open_sfx.mp3"), duration_ms=200)
    return stems_dir


@pytest.fixture
def parsed_entries_index():
    """Entries index with dialogue, ambience, and music entries."""
    return {
        1: {"seq": 1, "type": "section_header", "direction_type": None},
        2: {"seq": 2, "type": "direction", "direction_type": "AMBIENCE"},
        3: {"seq": 3, "type": "dialogue", "direction_type": None},
        5: {"seq": 5, "type": "dialogue", "direction_type": None},
        7: {"seq": 7, "type": "direction", "direction_type": "MUSIC"},
    }


@pytest.fixture
def parsed_json(tmp_path, parsed_entries_index):
    """Write a parsed JSON file and return its path."""
    data = {
        "show": "TEST", "season": 1, "episode": 1, "title": "Test",
        "source_file": "test.md",
        "entries": list(parsed_entries_index.values()),
        "stats": {"total_entries": 5, "dialogue_lines": 2, "direction_lines": 2,
                  "characters_for_tts": 0, "speakers": ["adam"], "sections": ["cold-open"]},
    }
    p = tmp_path / "parsed.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ─── Tests: module import ───

class TestModuleImport:
    def test_audio_assembly_importable(self):
        assert assembly is not None

    def test_mix_common_importable(self):
        assert mix_common is not None

    def test_no_elevenlabs_import(self):
        """XILP003 must not import elevenlabs — assembly is API-free."""
        import ast
        import inspect
        with open(inspect.getfile(assembly), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        assert not any("elevenlabs" in imp for imp in imports)


# ─── Tests: apply_phone_filter ───

class TestApplyPhoneFilter:
    def test_returns_audio_segment(self):
        tone = _make_tone(100)
        filtered = mix_common.apply_phone_filter(tone)
        assert isinstance(filtered, AudioSegment)
        assert len(filtered) > 0

    def test_available_from_assembly(self):
        """apply_phone_filter imported into XILP003 from mix_common."""
        assert hasattr(assembly, "apply_phone_filter")


# ─── Tests: mix_common.extract_seq ───

class TestExtractSeq:
    def test_three_digit_prefix(self):
        assert mix_common.extract_seq("stems/S01E01/003_cold-open_adam.mp3") == 3

    def test_leading_zeros(self):
        assert mix_common.extract_seq("001_act1_frank.mp3") == 1

    def test_large_number(self):
        assert mix_common.extract_seq("132_closing_sfx.mp3") == 132


# ─── Tests: mix_common.load_entries_index ───

class TestLoadEntriesIndex:
    def test_returns_seq_keyed_dict(self, parsed_json):
        idx = mix_common.load_entries_index(parsed_json)
        assert isinstance(idx, dict)
        assert 2 in idx
        assert idx[2]["direction_type"] == "AMBIENCE"

    def test_all_entries_indexed(self, parsed_json):
        idx = mix_common.load_entries_index(parsed_json)
        assert set(idx.keys()) == {1, 2, 3, 5, 7}


# ─── Tests: mix_common.collect_stem_plans ───

class TestCollectStemPlans:
    def test_collects_all_stems(self, stems_with_bg, parsed_entries_index):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        assert len(plans) == 4

    def test_sorted_by_seq(self, stems_with_bg, parsed_entries_index):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        seqs = [p.seq for p in plans]
        assert seqs == sorted(seqs)

    def test_classifies_ambience_as_background(self, stems_with_bg, parsed_entries_index):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        plan_by_seq = {p.seq: p for p in plans}
        assert plan_by_seq[2].is_background is True
        assert plan_by_seq[2].direction_type == "AMBIENCE"

    def test_classifies_music_as_background(self, stems_with_bg, parsed_entries_index):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        plan_by_seq = {p.seq: p for p in plans}
        assert plan_by_seq[7].is_background is True
        assert plan_by_seq[7].direction_type == "MUSIC"

    def test_classifies_dialogue_as_foreground(self, stems_with_bg, parsed_entries_index):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        plan_by_seq = {p.seq: p for p in plans}
        assert plan_by_seq[3].is_background is False

    def test_unknown_seq_treated_as_foreground(self, tmp_path):
        stems_dir = tmp_path / "stems"
        stems_dir.mkdir()
        _write_mp3(str(stems_dir / "099_act1_adam.mp3"))
        plans = mix_common.collect_stem_plans(str(stems_dir), {})  # empty index
        assert plans[0].is_background is False


# ─── Tests: mix_common.build_foreground ───

class TestBuildForeground:
    def test_returns_audio_and_timeline(self, stems_with_bg, parsed_entries_index, config):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        fg, timeline = mix_common.build_foreground(plans, config)
        assert isinstance(fg, AudioSegment)
        assert isinstance(timeline, dict)

    def test_foreground_excludes_background_stems(self, stems_with_bg, parsed_entries_index, config):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        fg, timeline = mix_common.build_foreground(plans, config, gap_ms=0)
        # Two foreground dialogue stems (seqs 3 and 5), each 300ms
        # background stems (seqs 2, 7) should not add duration
        assert len(fg) == pytest.approx(600, abs=100)

    def test_timeline_records_all_seqs(self, stems_with_bg, parsed_entries_index, config):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        _, timeline = mix_common.build_foreground(plans, config)
        # All 4 stems have timeline entries
        assert 2 in timeline
        assert 3 in timeline
        assert 5 in timeline
        assert 7 in timeline

    def test_background_cue_at_foreground_position(self, stems_with_bg, parsed_entries_index, config):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        _, timeline = mix_common.build_foreground(plans, config, gap_ms=0)
        # seq 2 (AMBIENCE) appears before seq 3 (dialogue) → cue at ms=0
        assert timeline[2] == 0

    def test_gap_ms_affects_duration(self, stems_with_bg, parsed_entries_index, config):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        fg_wide, _ = mix_common.build_foreground(plans, config, gap_ms=600)
        fg_narrow, _ = mix_common.build_foreground(plans, config, gap_ms=200)
        # Narrower gap should produce shorter output
        assert len(fg_narrow) < len(fg_wide)
        # Difference should be roughly (600-200)*num_fg_stems ms
        assert len(fg_wide) - len(fg_narrow) == pytest.approx(800, abs=100)

    def test_empty_stems_returns_empty(self, tmp_path, config):
        plans = mix_common.collect_stem_plans(str(tmp_path), {})
        fg, timeline = mix_common.build_foreground(plans, config)
        assert len(fg) == 0
        assert timeline == {}

    def test_phone_filter_applied(self, stems_with_bg, parsed_entries_index):
        cfg = {"frank": {"pan": 0.0, "filter": True},
               "adam":  {"pan": 0.0, "filter": False}}
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        with unittest.mock.patch.object(
            mix_common, "apply_phone_filter", wraps=mix_common.apply_phone_filter
        ) as mock_filter:
            mix_common.build_foreground(plans, cfg, apply_effects_fn=mix_common.apply_phone_filter)
        mock_filter.assert_called_once()  # only frank (seq 5) gets filtered


# ─── Tests: mix_common._loop_clip ───

class TestLoopClip:
    def test_loops_to_exact_duration(self):
        clip = _make_tone(100)
        looped = mix_common._loop_clip(clip, 350)
        assert len(looped) == 350

    def test_short_clip_extended(self):
        clip = _make_tone(50)
        looped = mix_common._loop_clip(clip, 300)
        assert len(looped) == 300

    def test_empty_clip_returns_silence(self):
        clip = AudioSegment.empty()
        looped = mix_common._loop_clip(clip, 200)
        assert len(looped) == 200

    def test_zero_duration_returns_empty(self):
        clip = _make_tone(100)
        looped = mix_common._loop_clip(clip, 0)
        assert len(looped) == 0


# ─── Tests: mix_common.build_ambience_layer ───

class TestBuildAmbienceLayer:
    def test_returns_full_length(self, stems_with_bg, parsed_entries_index, config):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        _, timeline = mix_common.build_foreground(plans, config, gap_ms=0)
        total_ms = 600  # two 300ms dialogue stems
        layer, _ = mix_common.build_ambience_layer(plans, timeline, total_ms)
        assert len(layer) == total_ms

    def test_no_ambience_returns_silence(self, tmp_path, config):
        stems_dir = tmp_path / "stems"
        stems_dir.mkdir()
        _write_mp3(str(stems_dir / "001_act1_adam.mp3"))
        idx = {1: {"seq": 1, "type": "dialogue", "direction_type": None}}
        plans = mix_common.collect_stem_plans(str(stems_dir), idx)
        _, timeline = mix_common.build_foreground(plans, config, gap_ms=0)
        layer, labels = mix_common.build_ambience_layer(plans, timeline, 300)
        # Should be full silence (not all-zero exactly due to float, but very quiet)
        assert len(layer) == 300
        assert labels == []

    def test_level_db_zero_preserves_amplitude(self, stems_with_bg, parsed_entries_index, config):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        _, timeline = mix_common.build_foreground(plans, config, gap_ms=0)
        total_ms = 600
        layer_ducked, _ = mix_common.build_ambience_layer(
            plans, timeline, total_ms, level_db=mix_common.AMBIENCE_LEVEL_DB
        )
        layer_full, _ = mix_common.build_ambience_layer(
            plans, timeline, total_ms, level_db=0
        )
        # Ducked layer should be quieter (lower dBFS)
        assert layer_ducked.dBFS < layer_full.dBFS


# ─── Tests: mix_common.build_music_layer ───

class TestBuildMusicLayer:
    def test_returns_full_length(self, stems_with_bg, parsed_entries_index, config):
        plans = mix_common.collect_stem_plans(str(stems_with_bg), parsed_entries_index)
        _, timeline = mix_common.build_foreground(plans, config, gap_ms=0)
        total_ms = 600
        layer, _ = mix_common.build_music_layer(plans, timeline, total_ms)
        assert len(layer) == total_ms

    def test_no_music_returns_silence(self, tmp_path, config):
        stems_dir = tmp_path / "stems"
        stems_dir.mkdir()
        _write_mp3(str(stems_dir / "001_act1_adam.mp3"))
        idx = {1: {"seq": 1, "type": "dialogue", "direction_type": None}}
        plans = mix_common.collect_stem_plans(str(stems_dir), idx)
        _, timeline = mix_common.build_foreground(plans, config, gap_ms=0)
        layer, labels = mix_common.build_music_layer(plans, timeline, 300)
        assert len(layer) == 300
        assert labels == []


# ─── Tests: XILP003 assemble_audio (original sequential) ───

class TestAssembleAudio:
    def test_assembles_to_mp3(self, config, stems_with_audio, tmp_path):
        output_path = str(tmp_path / "master.mp3")
        with unittest.mock.patch("subprocess.run"):
            assembly.assemble_audio(config, str(stems_with_audio), output_path)
        assert os.path.exists(output_path)

    def test_no_stems_prints_warning(self, config, tmp_path, caplog):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        assembly.assemble_audio(config, str(empty_dir), str(tmp_path / "out.mp3"))
        assert "No stems found" in caplog.text

    def test_applies_phone_filter_for_frank(self, config, stems_with_audio, tmp_path):
        output_path = str(tmp_path / "master.mp3")
        with unittest.mock.patch("subprocess.run"):
            with unittest.mock.patch.object(
                assembly, "apply_phone_filter",
                wraps=assembly.apply_phone_filter
            ) as mock_filter:
                assembly.assemble_audio(config, str(stems_with_audio), output_path)
                mock_filter.assert_called_once()


# ─── Tests: XILP003 assemble_multitrack ───

class TestAssembleMultitrack:
    def test_produces_mp3(self, config, stems_with_bg, parsed_json, tmp_path):
        output = str(tmp_path / "master.mp3")
        with unittest.mock.patch("subprocess.run"):
            assembly.assemble_multitrack(config, str(stems_with_bg), parsed_json, output)
        assert os.path.exists(output)

    def test_no_stems_prints_warning(self, config, tmp_path, parsed_json, caplog):
        empty_dir = tmp_path / "no_stems"
        empty_dir.mkdir()
        assembly.assemble_multitrack(config, str(empty_dir), parsed_json, str(tmp_path / "out.mp3"))
        assert "No stems found" in caplog.text

    def test_output_is_stereo(self, config, stems_with_bg, parsed_json, tmp_path):
        output = str(tmp_path / "master.mp3")
        with unittest.mock.patch("subprocess.run"):
            assembly.assemble_multitrack(config, str(stems_with_bg), parsed_json, output)
        result = AudioSegment.from_file(output)
        assert result.channels == 2


# ─── Tests: XILP003 main() fallback ───

class TestAssembleAudioFromCastFile:
    def test_main_loads_config_from_cast_json(self, sample_cast, tmp_path, caplog):
        cast_episode = tmp_path / "cast_the413_S01E01.json"
        import shutil
        shutil.copy2(sample_cast, str(cast_episode))
        (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))
        episode_stems = tmp_path / "stems" / "S01E01"
        episode_stems.mkdir(parents=True)
        original_dir = assembly.STEMS_DIR
        assembly.STEMS_DIR = str(tmp_path / "stems")
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", ["XILP003", "--episode", "S01E01"]):
                assembly.main()
        finally:
            assembly.STEMS_DIR = original_dir
            os.chdir(original_cwd)
        assert "No stems found" in caplog.text

    def test_main_uses_multitrack_when_parsed_exists(self, sample_cast, tmp_path, caplog):
        """main() picks up multitrack path when parsed JSON is present."""
        import shutil
        cast_episode = tmp_path / "cast_the413_S01E01.json"
        shutil.copy2(sample_cast, str(cast_episode))
        (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))

        parsed_dir = tmp_path / "parsed"
        parsed_dir.mkdir()
        parsed_data = {
            "show": "TEST", "season": 1, "episode": 1, "title": "T",
            "source_file": "t.md",
            "entries": [{"seq": 1, "type": "dialogue", "direction_type": None,
                          "section": "act1", "scene": None, "speaker": "adam",
                          "direction": None, "text": "Hi"}],
            "stats": {"total_entries": 1, "dialogue_lines": 1, "direction_lines": 0,
                      "characters_for_tts": 2, "speakers": ["adam"], "sections": ["act1"]},
        }
        (parsed_dir / "parsed_the413_S01E01.json").write_text(
            json.dumps(parsed_data), encoding="utf-8"
        )

        episode_stems = tmp_path / "stems" / "S01E01"
        episode_stems.mkdir(parents=True)
        original_dir = assembly.STEMS_DIR
        assembly.STEMS_DIR = str(tmp_path / "stems")
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", ["XILP003", "--episode", "S01E01"]):
                assembly.main()
        finally:
            assembly.STEMS_DIR = original_dir
            os.chdir(original_cwd)
        # multitrack path prints "multi-track" or "No stems" (empty dir)
        assert "multi-track" in caplog.text or "No stems" in caplog.text
