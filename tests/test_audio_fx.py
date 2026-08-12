# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for audio_fx.py — the ffmpeg-backed dialogue treatments."""

import hashlib
import unittest.mock

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

from xil_pipeline import audio_fx, mix_common

# Hashes recorded from the pre-registry implementation.  These lock the two
# legacy pydub treatments against accidental damage: episodes already produced
# with them must keep rendering identically.
GOLDEN_PHONE = "3c80fd1f05707336da859a6b26ae2efc63c08a0dc59538fdfa093794ca226849"
GOLDEN_VINTAGE = "00a2455c3b7867f722acc3aa12b8b8a64b4c8bc0a767bec0e5e85d3fd8b1bb49"

ffmpeg_required = pytest.mark.skipif(
    not audio_fx.ffmpeg_available(), reason="ffmpeg not available"
)


def _tone(freq: int = 440, duration_ms: int = 400, gain_db: float = 0) -> AudioSegment:
    seg = Sine(freq).to_audio_segment(duration=duration_ms)
    return seg + gain_db if gain_db else seg


@pytest.fixture(autouse=True)
def _clear_fx_cache():
    audio_fx.clear_cache()
    yield
    audio_fx.clear_cache()


# ─── Golden hashes: legacy treatments must not drift ───

class TestLegacyFiltersUnchanged:

    def test_phone_filter_hash(self):
        out = mix_common.apply_phone_filter(_tone(duration_ms=500))
        assert hashlib.sha256(out.raw_data).hexdigest() == GOLDEN_PHONE

    def test_vintage_filter_hash(self):
        out = mix_common.apply_vintage_filter(_tone(duration_ms=500))
        assert hashlib.sha256(out.raw_data).hexdigest() == GOLDEN_VINTAGE

    def test_legacy_names_never_invoke_ffmpeg(self, monkeypatch):
        """phone/vintage must stay pure pydub — no subprocess on that path."""
        def _boom(*args, **kwargs):
            pytest.fail("ffmpeg spawned for a legacy filter")

        monkeypatch.setattr(audio_fx, "run_ffmpeg_filter", _boom)
        tone = _tone()
        for value in (True, "phone", "vintage", "vintage,phone", "phone,vintage"):
            mix_common._apply_speaker_filters(tone, value)


# ─── ffmpeg bridge mechanics ───

@ffmpeg_required
class TestRunFfmpegFilter:

    def test_passthrough_preserves_format(self):
        tone = _tone()
        out = audio_fx.run_ffmpeg_filter(tone, "[0:a]volume=1.0[out]", label="probe")
        assert (out.frame_rate, out.channels, out.sample_width) == (
            tone.frame_rate, tone.channels, tone.sample_width)
        assert len(out) == len(tone)

    def test_aecho_output_is_trimmed_to_input_length(self):
        """aecho extends audio; the length contract must claw it back.

        The cue timeline is derived from unfiltered MP3 header durations, so a
        treatment that changed a stem's length would desync the rendered mix
        from the label track with no error.
        """
        tone = _tone(duration_ms=1000)
        untrimmed = audio_fx.run_ffmpeg_filter(
            tone, "[0:a]aecho=0.9:0.9:55:0.22[out]",
            preserve_length=False, label="probe")
        trimmed = audio_fx.run_ffmpeg_filter(
            tone, "[0:a]aecho=0.9:0.9:55:0.22[out]", label="probe")
        assert len(untrimmed) > 1000, "expected aecho to extend the segment"
        assert len(trimmed) == 1000

    def test_mono_input_stays_mono(self):
        out = audio_fx.apply_treatment(_tone(), "film")
        assert out.channels == 1

    def test_stereo_input_stays_stereo(self):
        out = audio_fx.apply_treatment(_tone().set_channels(2), "film")
        assert out.channels == 2

    def test_result_is_cached(self):
        tone = _tone()
        graph = "[0:a]volume=0.9[out]"
        real = audio_fx.subprocess.run
        with unittest.mock.patch.object(audio_fx.subprocess, "run", wraps=real) as m:
            first = audio_fx.run_ffmpeg_filter(tone, graph, label="probe")
            second = audio_fx.run_ffmpeg_filter(tone, graph, label="probe")
        assert m.call_count == 1
        assert first.raw_data == second.raw_data


class TestFailureDegradation:

    def test_missing_binary_returns_input_unchanged(self, monkeypatch, caplog):
        monkeypatch.setattr(audio_fx, "_ffmpeg_binary", lambda: "/nonexistent/ffmpeg")
        audio_fx._warned.clear()
        tone = _tone()
        out = audio_fx.apply_treatment(tone, "film")
        assert out.raw_data == tone.raw_data

    def test_strict_mode_raises(self, monkeypatch):
        monkeypatch.setattr(audio_fx, "_ffmpeg_binary", lambda: "/nonexistent/ffmpeg")
        monkeypatch.setenv(audio_fx.STRICT_ENV_VAR, "1")
        with pytest.raises(audio_fx.AudioFxError):
            audio_fx.apply_treatment(_tone(), "film")

    def test_unknown_treatment_returns_input(self):
        tone = _tone()
        assert audio_fx.apply_treatment(tone, "gramophone").raw_data == tone.raw_data

    def test_unsupported_sample_width_returns_input(self, monkeypatch):
        tone = _tone()
        monkeypatch.setattr(audio_fx, "_RAW_FORMATS", {})
        assert audio_fx.apply_treatment(tone, "film").raw_data == tone.raw_data


# ─── Spectral behaviour ───
#
# Asserted as relative levels rather than exact samples, so the tests survive an
# ffmpeg version bump that nudges filter coefficients.

@ffmpeg_required
class TestSpeakerphoneResponse:

    @staticmethod
    def _delta(freq: int) -> float:
        tone = _tone(freq, gain_db=-20)
        return audio_fx.apply_treatment(tone, "speakerphone").dBFS - tone.dBFS

    def test_passband_dominates_low_rejection(self):
        assert self._delta(1500) - self._delta(120) >= 20

    def test_passband_dominates_high_rejection(self):
        assert self._delta(1500) - self._delta(6000) >= 12

    def test_level_roughly_matched_to_input(self):
        """Output must not collapse; it sits just under unity on real speech."""
        tone = _tone(1000, duration_ms=800, gain_db=-18)
        out = audio_fx.apply_treatment(tone, "speakerphone")
        assert out.dBFS > tone.dBFS - 12


@ffmpeg_required
class TestFilmResponse:

    @staticmethod
    def _delta(freq: int) -> float:
        tone = _tone(freq, gain_db=-20)
        return audio_fx.apply_treatment(tone, "film").dBFS - tone.dBFS

    def test_low_end_is_not_stripped(self):
        """Unlike phone/vintage, film keeps body at 200 Hz."""
        assert self._delta(200) > -3

    def test_top_end_rolls_off(self):
        assert self._delta(200) - self._delta(12000) >= 8

    def test_grain_is_audible_on_silence(self):
        silent = AudioSegment.silent(duration=400, frame_rate=44100)
        assert audio_fx.apply_treatment(silent, "film").dBFS > -75

    def test_film_and_vintage_differ(self):
        tone = _tone()
        assert (audio_fx.apply_treatment(tone, "film").raw_data
                != mix_common.apply_vintage_filter(tone).raw_data)


# ─── Registry dispatch ───

class TestFilterRegistry:

    def test_registry_covers_all_four_names(self):
        assert set(mix_common.FILTER_REGISTRY) == {
            "phone", "vintage", "film", "speakerphone"}

    def test_registry_targets_resolve(self):
        for func_name in mix_common.FILTER_REGISTRY.values():
            assert callable(getattr(mix_common, func_name))

    def test_film_name_dispatches_to_film_filter(self):
        with unittest.mock.patch.object(
                mix_common, "apply_film_filter",
                wraps=mix_common.apply_film_filter) as m:
            mix_common._apply_speaker_filters(_tone(), "film")
        m.assert_called_once()

    def test_speakerphone_name_dispatches(self):
        with unittest.mock.patch.object(
                mix_common, "apply_speakerphone_filter",
                wraps=mix_common.apply_speakerphone_filter) as m:
            mix_common._apply_speaker_filters(_tone(), "speakerphone")
        m.assert_called_once()

    def test_chained_names_apply_in_order(self):
        calls = []
        with unittest.mock.patch.object(
                mix_common, "apply_phone_filter",
                side_effect=lambda s: (calls.append("phone"), s)[1]), \
             unittest.mock.patch.object(
                mix_common, "apply_film_filter",
                side_effect=lambda s: (calls.append("film"), s)[1]):
            mix_common._apply_speaker_filters(_tone(), "phone,film")
        assert calls == ["phone", "film"]

    def test_unknown_name_warns_once_and_passes_through(self, caplog):
        mix_common._warned_unknown_filters.discard("telephone")
        tone = _tone()
        with caplog.at_level("WARNING"):
            first = mix_common._apply_speaker_filters(tone, "telephone")
            mix_common._apply_speaker_filters(tone, "telephone")
        assert first.raw_data == tone.raw_data
        assert sum("telephone" in r.message for r in caplog.records) == 1
