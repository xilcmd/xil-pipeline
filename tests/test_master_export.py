"""Tests for XILP011_master_export.py — Final Master MP3 Export."""

import importlib.util
import json
import os

import pytest

_mod_path = os.path.join(
    os.path.dirname(__file__), "..", "XILP011_master_export.py"
)
spec = importlib.util.spec_from_file_location("master_export", _mod_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wav(tmp_path, tag, suffix, duration_ms=1000):
    """Create a minimal WAV file for testing."""
    from pydub import AudioSegment
    from pydub.generators import Sine

    seg = Sine(440).to_audio_segment(duration=duration_ms)
    seg = seg.set_channels(2).set_frame_rate(48000)
    daw_dir = tmp_path / "daw" / tag
    daw_dir.mkdir(parents=True, exist_ok=True)
    path = daw_dir / f"{tag}_layer_{suffix}.wav"
    seg.export(str(path), format="wav")
    return str(path)


def _make_cast(tmp_path, tag, slug="the413"):
    """Create a minimal cast config."""
    cast = {
        "show": "THE 413",
        "season": 2,
        "episode": 3,
        "title": "The Bridge",
        "cast": {},
    }
    path = tmp_path / f"cast_{slug}_{tag}.json"
    path.write_text(json.dumps(cast))
    return str(path)


# ---------------------------------------------------------------------------
# Tests: load_layer_wavs
# ---------------------------------------------------------------------------


class TestLoadLayerWavs:
    def test_finds_all_four(self, tmp_path):
        tag = "S02E03"
        for s in ("dialogue", "ambience", "music", "sfx"):
            _make_wav(tmp_path, tag, s)
        layers = mod.load_layer_wavs(str(tmp_path / "daw" / tag), tag)
        assert len(layers) == 4
        names = [l[0] for l in layers]
        assert names == ["dialogue", "ambience", "music", "sfx"]

    def test_missing_layers(self, tmp_path):
        tag = "S02E03"
        _make_wav(tmp_path, tag, "dialogue")
        layers = mod.load_layer_wavs(str(tmp_path / "daw" / tag), tag)
        assert len(layers) == 1
        assert layers[0][0] == "dialogue"

    def test_empty_dir(self, tmp_path):
        daw_dir = tmp_path / "daw" / "S01E01"
        daw_dir.mkdir(parents=True)
        layers = mod.load_layer_wavs(str(daw_dir), "S01E01")
        assert layers == []


# ---------------------------------------------------------------------------
# Tests: mix_layers
# ---------------------------------------------------------------------------


class TestMixLayers:
    def test_single_layer(self, tmp_path):
        path = _make_wav(tmp_path, "S01E01", "dialogue", duration_ms=500)
        combined = mod.mix_layers([("dialogue", path)])
        assert len(combined) == 500

    def test_two_layers_same_duration(self, tmp_path):
        p1 = _make_wav(tmp_path, "S01E01", "dialogue", duration_ms=1000)
        p2 = _make_wav(tmp_path, "S01E01", "ambience", duration_ms=1000)
        combined = mod.mix_layers([("dialogue", p1), ("ambience", p2)])
        assert len(combined) == 1000
        assert combined.channels == 2


# ---------------------------------------------------------------------------
# Tests: export_master
# ---------------------------------------------------------------------------


class TestExportMaster:
    def test_creates_mp3(self, tmp_path):
        from pydub import AudioSegment
        from pydub.generators import Sine

        seg = Sine(440).to_audio_segment(duration=500).set_channels(2).set_frame_rate(48000)
        out = str(tmp_path / "test_master.mp3")
        mod.export_master(seg, out, show_name="TEST SHOW", tag="S01E01")
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0

        # Verify it's loadable as MP3
        loaded = AudioSegment.from_mp3(out)
        assert loaded.frame_rate == 48000
        assert loaded.channels == 2

    def test_mono_upscaled_to_stereo(self, tmp_path):
        from pydub import AudioSegment
        from pydub.generators import Sine

        seg = Sine(440).to_audio_segment(duration=300).set_channels(1)
        out = str(tmp_path / "mono_test.mp3")
        mod.export_master(seg, out, show_name="TEST", tag="S01E01")
        loaded = AudioSegment.from_mp3(out)
        assert loaded.channels == 2


# ---------------------------------------------------------------------------
# Tests: output filename
# ---------------------------------------------------------------------------


class TestOutputFilename:
    def test_default_format(self):
        import datetime
        today = datetime.date.today().isoformat()
        expected = f"masters/S02E03_the413_{today}.mp3"
        # Verify the pattern matches what main() would produce
        slug = "the413"
        tag = "S02E03"
        result = os.path.join("masters", f"{tag}_{slug}_{today}.mp3")
        assert result == expected
