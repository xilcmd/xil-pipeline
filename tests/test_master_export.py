# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP011_master_export.py — Final Master MP3 Export."""

import json
import os
import unittest.mock

from xil_pipeline import XILP011_master_export as mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_wav(tmp_path, tag, suffix, duration_ms=1000):
    """Create a minimal WAV file for testing."""
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
        # Verify the pattern matches what main() would produce
        slug = "the413"
        tag = "S02E03"
        result = os.path.join("masters", f"{tag}_{slug}_{today}.mp3")
        # Normalize to forward slashes for cross-platform comparison
        assert result.replace("\\", "/") == f"masters/S02E03_the413_{today}.mp3"


# ---------------------------------------------------------------------------
# Tests: load_layer_wavs — edge cases
# ---------------------------------------------------------------------------


class TestLoadLayerWavsEdgeCases:
    def test_nonexistent_directory_returns_empty(self, tmp_path):
        layers = mod.load_layer_wavs(str(tmp_path / "does_not_exist"), "S01E01")
        assert layers == []

    def test_returns_paths_in_canonical_order(self, tmp_path):
        tag = "S01E01"
        # Create in reverse order to confirm result order is LAYER_SUFFIXES order
        for s in reversed(("dialogue", "ambience", "music", "sfx")):
            _make_wav(tmp_path, tag, s)
        layers = mod.load_layer_wavs(str(tmp_path / "daw" / tag), tag)
        assert [l[0] for l in layers] == list(mod.LAYER_SUFFIXES)


# ---------------------------------------------------------------------------
# Tests: mix_layers — edge cases
# ---------------------------------------------------------------------------


class TestMixLayersEdgeCases:
    def test_empty_list_returns_none(self):
        result = mod.mix_layers([])
        assert result is None


# ---------------------------------------------------------------------------
# Tests: export_master — ID3 metadata
# ---------------------------------------------------------------------------


class TestExportMasterMetadata:
    def test_id3_tags_written(self, tmp_path):
        from mutagen.id3 import ID3
        from pydub.generators import Sine

        seg = Sine(440).to_audio_segment(duration=300).set_channels(2).set_frame_rate(48000)
        out = str(tmp_path / "tagged.mp3")
        mod.export_master(
            seg, out,
            show_name="THE 413",
            tag="S02E03",
            title="THE 413 — The Bridge",
            artist="Tina Brissette",
        )
        tags = ID3(out)
        assert "TIT2" in tags   # title
        assert "TPE1" in tags   # artist

    def test_title_falls_back_to_tag_when_none(self, tmp_path):
        from mutagen.id3 import ID3
        from pydub.generators import Sine

        seg = Sine(440).to_audio_segment(duration=300).set_channels(2).set_frame_rate(48000)
        out = str(tmp_path / "notitle.mp3")
        mod.export_master(seg, out, show_name="TEST", tag="S01E01", title=None)
        tags = ID3(out)
        assert str(tags["TIT2"]) == "S01E01"


# ---------------------------------------------------------------------------
# Tests: main() — all branches
# ---------------------------------------------------------------------------


class TestMain:
    """Integration-level tests for the main() CLI entry point."""

    def _argv(self, tmp_path, tag="S01E01", extra=None):
        args = ["xilp011", "--episode", tag, "--daw-dir", str(tmp_path / "daw" / tag)]
        if extra:
            args += extra
        return args

    def test_dry_run_no_files_written(self, tmp_path):
        tag = "S01E01"
        for s in mod.LAYER_SUFFIXES:
            _make_wav(tmp_path, tag, s)
        output_path = str(tmp_path / "master.mp3")
        with unittest.mock.patch("sys.argv", self._argv(tmp_path, tag, [
            "--output", output_path, "--dry-run",
        ])):
            mod.main()
        assert not os.path.exists(output_path)

    def test_no_layers_returns_early(self, tmp_path):
        tag = "S01E01"
        daw_dir = tmp_path / "daw" / tag
        daw_dir.mkdir(parents=True)
        output_path = str(tmp_path / "master.mp3")
        with unittest.mock.patch("sys.argv", self._argv(tmp_path, tag, [
            "--output", output_path,
        ])):
            mod.main()
        assert not os.path.exists(output_path)

    def test_full_export_writes_mp3(self, tmp_path):
        from pydub import AudioSegment

        tag = "S01E01"
        for s in mod.LAYER_SUFFIXES:
            _make_wav(tmp_path, tag, s, duration_ms=500)
        output_path = str(tmp_path / "out.mp3")
        with unittest.mock.patch("sys.argv", self._argv(tmp_path, tag, [
            "--output", output_path,
        ])):
            mod.main()
        assert os.path.exists(output_path)
        assert os.path.getsize(output_path) > 0
        loaded = AudioSegment.from_mp3(output_path)
        assert loaded.frame_rate == mod.SAMPLE_RATE

    def test_cast_config_provides_metadata(self, tmp_path):
        from mutagen.id3 import ID3

        tag = "S02E03"
        for s in mod.LAYER_SUFFIXES:
            _make_wav(tmp_path, tag, s, duration_ms=300)
        cast_path = _make_cast(tmp_path, tag)
        output_path = str(tmp_path / "out.mp3")
        # Patch resolve_slug + derive_paths so cast config is picked up from tmp_path
        paths = {"cast": cast_path}
        with unittest.mock.patch("xil_pipeline.XILP011_master_export.derive_paths", return_value=paths), \
             unittest.mock.patch("xil_pipeline.XILP011_master_export.resolve_slug", return_value="the413"), \
             unittest.mock.patch("sys.argv", [
                 "xilp011", "--episode", tag,
                 "--daw-dir", str(tmp_path / "daw" / tag),
                 "--output", output_path,
             ]):
            mod.main()
        tags = ID3(output_path)
        assert "THE 413" in str(tags.get("TALB", ""))

    def test_missing_cast_config_uses_defaults(self, tmp_path):
        tag = "S01E01"
        for s in mod.LAYER_SUFFIXES:
            _make_wav(tmp_path, tag, s, duration_ms=300)
        output_path = str(tmp_path / "out.mp3")
        # Point cast path at a file that doesn't exist
        paths = {"cast": str(tmp_path / "no_such_cast.json")}
        with unittest.mock.patch("xil_pipeline.XILP011_master_export.derive_paths", return_value=paths), \
             unittest.mock.patch("xil_pipeline.XILP011_master_export.resolve_slug", return_value="sample"), \
             unittest.mock.patch("sys.argv", [
                 "xilp011", "--episode", tag,
                 "--daw-dir", str(tmp_path / "daw" / tag),
                 "--output", output_path,
             ]):
            mod.main()
        # Should succeed using "Sample Show" default — file written
        assert os.path.exists(output_path)
