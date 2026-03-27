# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP005_daw_export.py — DAW layer export."""

import json
import os
import unittest.mock

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

# ─── Import XILP005 ───

from xil_pipeline import XILP005_daw_export as daw


# ─── Helpers ───

def _make_tone(duration_ms: int = 200) -> AudioSegment:
    return Sine(440).to_audio_segment(duration=duration_ms)


def _write_mp3(path: str, duration_ms: int = 200) -> None:
    _make_tone(duration_ms).export(path, format="mp3")


# ─── Fixtures ───

@pytest.fixture
def cast_data():
    return {
        "show": "THE 413",
        "season": 1,
        "episode": 1,
        "title": "Test Episode",
        "cast": {
            "adam": {"full_name": "Adam Santos", "voice_id": "abc123",
                     "pan": 0.0, "filter": False, "role": "Host"},
            "ava":  {"full_name": "Ava", "voice_id": "def456",
                     "pan": 0.3, "filter": True, "role": "Guest"},
        },
    }


@pytest.fixture
def cast_file(tmp_path, cast_data):
    p = tmp_path / "cast_the413_S01E01.json"
    p.write_text(json.dumps(cast_data), encoding="utf-8")
    return str(p)


@pytest.fixture
def parsed_data():
    return {
        "show": "THE 413", "season": 1, "episode": 1, "title": "Test",
        "source_file": "test.md",
        "entries": [
            {"seq": 1, "type": "section_header", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "COLD OPEN", "direction_type": None},
            {"seq": 2, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "AMBIENCE: DINER", "direction_type": "AMBIENCE"},
            {"seq": 3, "type": "dialogue", "section": "cold-open",
             "scene": None, "speaker": "adam", "direction": None,
             "text": "Hello.", "direction_type": None},
            {"seq": 4, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "BEAT", "direction_type": "BEAT"},
            {"seq": 5, "type": "dialogue", "section": "cold-open",
             "scene": None, "speaker": "ava", "direction": None,
             "text": "Hi.", "direction_type": None},
            {"seq": 6, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "MUSIC: STING", "direction_type": "MUSIC"},
        ],
        "stats": {"total_entries": 6, "dialogue_lines": 2, "direction_lines": 3,
                  "characters_for_tts": 9, "speakers": ["adam", "ava"],
                  "sections": ["cold-open"]},
    }


@pytest.fixture
def parsed_file(tmp_path, parsed_data):
    p = tmp_path / "parsed_the413_S01E01.json"
    p.write_text(json.dumps(parsed_data), encoding="utf-8")
    return str(p)


@pytest.fixture
def stems_dir(tmp_path):
    """Stems directory with dialogue, ambience, beat, and music stems."""
    d = tmp_path / "stems" / "S01E01"
    d.mkdir(parents=True)
    _write_mp3(str(d / "003_cold-open_adam.mp3"), duration_ms=300)   # dialogue
    _write_mp3(str(d / "005_cold-open_ava.mp3"),  duration_ms=250)   # dialogue
    _write_mp3(str(d / "002_cold-open_sfx.mp3"),  duration_ms=500)   # ambience
    _write_mp3(str(d / "004_cold-open_sfx.mp3"),  duration_ms=100)   # beat
    _write_mp3(str(d / "006_cold-open_sfx.mp3"),  duration_ms=200)   # music
    return str(d)


@pytest.fixture
def config(cast_data):
    return {
        "adam": {"id": "abc123", "pan": 0.0, "filter": False},
        "ava":  {"id": "def456", "pan": 0.3, "filter": True},
    }


# ─── Tests: module import ───

class TestModuleImport:
    def test_daw_export_importable(self):
        assert daw is not None

    def test_no_elevenlabs_import(self):
        import ast
        import inspect
        with open(inspect.getfile(daw), "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)
        assert not any("elevenlabs" in imp for imp in imports)

    def test_main_function_exists(self):
        assert callable(getattr(daw, "main", None))


# ─── Tests: _make_audacity_script ───

class TestMakeAudacityScript:
    def test_returns_string(self):
        result = daw._make_audacity_script("S01E01", [("Dialogue", "d.wav")])
        assert isinstance(result, str)

    def test_contains_tag(self):
        result = daw._make_audacity_script("S01E02", [("Dialogue", "d.wav")])
        assert "S01E02" in result

    def test_contains_layer_names(self):
        layers = [("Dialogue", "d.wav"), ("Ambience", "a.wav")]
        result = daw._make_audacity_script("S01E01", layers)
        assert "Dialogue" in result
        assert "Ambience" in result

    def test_contains_manual_instructions(self):
        result = daw._make_audacity_script("S01E01", [("Dialogue", "d.wav")])
        assert "Import" in result


# ─── Tests: dry_run_daw ───

class TestDryRunDaw:
    def test_prints_summary(self, stems_dir, parsed_file, config, capsys):
        from xil_pipeline.mix_common import load_entries_index, collect_stem_plans
        idx = load_entries_index(parsed_file)
        plans = collect_stem_plans(stems_dir, idx)
        daw.dry_run_daw("S01E01", plans, idx, "daw/S01E01")
        out = capsys.readouterr().out
        assert "S01E01" in out
        assert "dialogue" in out.lower()
        assert "ambience" in out.lower()


# ─── Tests: export_daw_layers ───

class TestExportDawLayers:
    def test_creates_four_wav_files(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01")
        for _, suffix, _ in daw.LAYERS:
            assert os.path.exists(os.path.join(output_dir, f"S01E01_{suffix}.wav"))

    def test_creates_audacity_script(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01")
        assert os.path.exists(os.path.join(output_dir, "S01E01_open_in_audacity.py"))

    def test_all_layers_same_duration(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01")
        durations = []
        for _, suffix, _ in daw.LAYERS:
            seg = AudioSegment.from_file(
                os.path.join(output_dir, f"S01E01_{suffix}.wav")
            )
            durations.append(len(seg))
        assert len(set(durations)) == 1, f"Layer durations differ: {durations}"

    def test_dialogue_layer_is_stereo(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01")
        seg = AudioSegment.from_file(
            os.path.join(output_dir, "S01E01_layer_dialogue.wav")
        )
        assert seg.channels == 2

    def test_no_stems_prints_warning(self, config, parsed_file, tmp_path, capsys):
        empty_stems = str(tmp_path / "empty_stems")
        os.makedirs(empty_stems)
        output_dir = str(tmp_path / "daw")
        daw.export_daw_layers(config, empty_stems, parsed_file, output_dir, "S01E01")
        assert "No stems found" in capsys.readouterr().out

    def test_dialogue_layer_not_all_silence(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01")
        seg = AudioSegment.from_file(
            os.path.join(output_dir, "S01E01_layer_dialogue.wav")
        )
        assert seg.dBFS > -80, "Dialogue layer should contain audio, not silence"


# ─── Tests: XILP005 main() ───

class TestDawExportMain:
    def test_main_dry_run(self, cast_file, parsed_file, stems_dir, tmp_path, capsys):
        cast_path = cast_file
        parsed_path = parsed_file
        (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch(
                "sys.argv",
                ["XILP005", "--episode", "S01E01",
                 "--parsed", parsed_path,
                 "--output-dir", str(tmp_path / "daw" / "S01E01"),
                 "--dry-run"],
            ):
                daw.main()
        finally:
            os.chdir(original_cwd)
        out = capsys.readouterr().out
        assert "Dry Run" in out or "dry" in out.lower()

    def test_main_exits_gracefully_no_parsed(self, cast_file, tmp_path, capsys):
        (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch(
                "sys.argv",
                ["XILP005", "--episode", "S01E01",
                 "--parsed", str(tmp_path / "nonexistent.json")],
            ):
                daw.main()
        finally:
            os.chdir(original_cwd)
        out = capsys.readouterr().out
        assert "not found" in out or "Run XILP001" in out


# ─── Tests: _make_audacity_script save_aup3 ───

class TestMakeAudacityScriptSaveAup3:
    def test_save_aup3_includes_save_command(self):
        result = daw._make_audacity_script("S01E01", [("Dialogue", "d.wav")], save_aup3=True)
        assert "SaveProject2" in result

    def test_save_aup3_false_excludes_save_command(self):
        result = daw._make_audacity_script("S01E01", [("Dialogue", "d.wav")], save_aup3=False)
        assert "SaveProject2" not in result

    def test_save_aup3_includes_aup3_path(self):
        result = daw._make_audacity_script("S01E01", [("Dialogue", "d.wav")], save_aup3=True)
        assert "S01E01.aup3" in result

    def test_pipe_send_reads_until_blank_line(self):
        result = daw._make_audacity_script("S01E01", [("Dialogue", "d.wav")], save_aup3=False)
        # send() must use a loop, not a single readline
        assert "while True" in result
        assert "readline()" in result

    def test_wsl_detection_present(self):
        result = daw._make_audacity_script("S01E01", [("Dialogue", "d.wav")])
        assert "WSL_DISTRO_NAME" in result
        assert "wslpath" in result
        assert "python.exe" in result


# ─── Tests: export_daw_layers save_aup3 ───

class TestExportDawLayersSaveAup3:
    def test_export_daw_layers_save_aup3_flag_forwarded(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01", save_aup3=True)
        script = open(os.path.join(output_dir, "S01E01_open_in_audacity.py")).read()
        assert "SaveProject2" in script

    def test_export_daw_layers_default_no_save(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01")
        script = open(os.path.join(output_dir, "S01E01_open_in_audacity.py")).read()
        assert "SaveProject2" not in script


# ─── Tests: generate_audacity_macro ───

class TestGenerateAudacityMacro:
    """Tests for generate_audacity_macro() — uses mocked helpers to avoid
    filesystem/WSL dependencies."""

    def _run(self, tmp_path, layer_files=None, tag="S01E01"):
        if layer_files is None:
            layer_files = [
                ("Dialogue", "S01E01_layer_dialogue.wav"),
                ("Labels (Dialogue)", "S01E01_labels_dialogue.txt"),
            ]
        output_dir = str(tmp_path / "daw" / tag)
        os.makedirs(output_dir)
        macros_dir = str(tmp_path / "Macros")
        os.makedirs(macros_dir)
        with (
            unittest.mock.patch.object(daw, "_find_audacity_macros_dir", return_value=macros_dir),
            unittest.mock.patch.object(daw, "_to_windows_path", side_effect=lambda p: p.replace("/", "\\")),
        ):
            macro_path = daw.generate_audacity_macro(output_dir, tag, layer_files, show="THE 413")
        return macro_path, macros_dir, output_dir

    def test_returns_macro_path(self, tmp_path):
        macro_path, macros_dir, _ = self._run(tmp_path)
        assert macro_path == os.path.join(macros_dir, "THE413_S01E01.txt")

    def test_creates_macro_file(self, tmp_path):
        macro_path, _, _ = self._run(tmp_path)
        assert os.path.exists(macro_path)

    def test_macro_contains_import2_only_for_wav(self, tmp_path):
        # layer_files has 1 WAV + 1 TXT — only the WAV should appear
        macro_path, _, _ = self._run(tmp_path)
        content = open(macro_path).read()
        assert content.count("Import2:") == 1

    def test_macro_contains_wav_filename(self, tmp_path):
        macro_path, _, _ = self._run(tmp_path)
        content = open(macro_path).read()
        assert "S01E01_layer_dialogue.wav" in content

    def test_macro_excludes_label_txt_files(self, tmp_path):
        macro_path, _, _ = self._run(tmp_path)
        content = open(macro_path).read()
        assert "S01E01_labels_dialogue.txt" not in content

    def test_returns_none_when_macros_dir_not_found(self, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        os.makedirs(output_dir)
        with unittest.mock.patch.object(daw, "_find_audacity_macros_dir", return_value=None):
            result = daw.generate_audacity_macro(output_dir, "S01E01", [])
        assert result is None

    def test_export_daw_layers_macro_flag_writes_macro(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        macros_dir = str(tmp_path / "Macros")
        os.makedirs(macros_dir)
        with (
            unittest.mock.patch.object(daw, "_find_audacity_macros_dir", return_value=macros_dir),
            unittest.mock.patch.object(daw, "_to_windows_path", side_effect=lambda p: p.replace("/", "\\")),
        ):
            daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01", macro=True, show="THE 413")
        macro_path = os.path.join(macros_dir, "THE413_S01E01.txt")
        assert os.path.exists(macro_path)
        content = open(macro_path).read()
        assert content.count("Import2:") == 4

    def test_export_daw_layers_no_macro_by_default(self, config, stems_dir, parsed_file, tmp_path):
        output_dir = str(tmp_path / "daw" / "S01E01")
        macros_dir = str(tmp_path / "Macros")
        os.makedirs(macros_dir)
        with unittest.mock.patch.object(daw, "_find_audacity_macros_dir", return_value=macros_dir) as mock_find:
            daw.export_daw_layers(config, stems_dir, parsed_file, output_dir, "S01E01")
        mock_find.assert_not_called()


# ─── Tests: Preamble in DAW export ───

class TestPreambleInDawExport:
    """Verify n002_preamble_tina.mp3 and n001_preamble_sfx.mp3 appear in the correct layers."""

    def _inject_preamble(self, parsed_file: str) -> None:
        """Prepend seq -2 and -1 preamble entries into the parsed JSON (as XILP002 would)."""
        import json as _json
        with open(parsed_file, "r", encoding="utf-8") as f:
            data = _json.load(f)
        preamble_entries = [
            {"seq": -2, "type": "dialogue", "section": "preamble", "scene": None,
             "speaker": "tina", "direction": None, "text": "Hello, listeners.",
             "direction_type": None},
            {"seq": -1, "type": "direction", "section": "preamble", "scene": None,
             "speaker": None, "direction": None, "text": "INTRO MUSIC",
             "direction_type": "MUSIC"},
        ]
        data["entries"] = preamble_entries + data["entries"]
        with open(parsed_file, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2)

    def test_preamble_voice_appears_in_dialogue_layer(
        self, config, stems_dir, parsed_file, tmp_path
    ):
        """n002_preamble_tina.mp3 is placed in the dialogue layer at t=0."""
        _write_mp3(os.path.join(stems_dir, "n002_preamble_tina.mp3"), duration_ms=400)
        self._inject_preamble(parsed_file)
        output_dir = str(tmp_path / "daw" / "S01E01")

        daw.export_daw_layers(
            config, stems_dir, parsed_file, output_dir, "S01E01",
        )

        labels_path = os.path.join(output_dir, "S01E01_labels_dialogue.txt")
        content = open(labels_path).read()
        assert "tina" in content
        # tina should be the first label (earliest start time)
        first_label = content.strip().splitlines()[0]
        assert "tina" in first_label

    def test_preamble_music_appears_in_music_layer(
        self, config, stems_dir, parsed_file, tmp_path
    ):
        """n001_preamble_sfx.mp3 is placed in the music layer with INTRO MUSIC label."""
        _write_mp3(os.path.join(stems_dir, "n002_preamble_tina.mp3"), duration_ms=300)
        _write_mp3(os.path.join(stems_dir, "n001_preamble_sfx.mp3"), duration_ms=500)
        self._inject_preamble(parsed_file)
        output_dir = str(tmp_path / "daw" / "S01E01")

        daw.export_daw_layers(
            config, stems_dir, parsed_file, output_dir, "S01E01",
        )

        labels_path = os.path.join(output_dir, "S01E01_labels_music.txt")
        content = open(labels_path).read()
        assert "INTRO MUSIC" in content

    def test_old_preamble_filenames_silently_ignored(
        self, config, stems_dir, parsed_file, tmp_path
    ):
        """Old-style preamble_tina.mp3 on disk without index entries is silently ignored."""
        _write_mp3(os.path.join(stems_dir, "preamble_tina.mp3"), duration_ms=400)
        output_dir = str(tmp_path / "daw" / "S01E01")

        # No preamble entries in parsed JSON → old filenames silently skipped
        daw.export_daw_layers(
            config, stems_dir, parsed_file, output_dir, "S01E01",
        )

        labels_path = os.path.join(output_dir, "S01E01_labels_dialogue.txt")
        content = open(labels_path).read()
        assert "tina" not in content
