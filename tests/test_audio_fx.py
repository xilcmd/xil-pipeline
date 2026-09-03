# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for audio_fx.py — the ffmpeg-backed dialogue treatments."""

import hashlib
import subprocess
import unittest.mock

import pytest
from pydub import AudioSegment
from pydub.generators import Sine, WhiteNoise

from xil_pipeline import audio_fx, mix_common

# Hash recorded from the pre-registry implementation.  This locks the one
# remaining pure-pydub treatment against accidental damage: episodes already
# produced with it must keep rendering identically.
#
# `phone` used to be pinned here too.  It is an ffmpeg treatment now, and
# ffmpeg output is not byte-identical across the CI matrix, so it is covered by
# TestPhoneResponse the same way film and speakerphone always have been.
GOLDEN_VINTAGE = "00a2455c3b7867f722acc3aa12b8b8a64b4c8bc0a767bec0e5e85d3fd8b1bb49"

ffmpeg_required = pytest.mark.skipif(
    not audio_fx.ffmpeg_available(), reason="ffmpeg not available"
)

# libgsm ships with Debian's ffmpeg but not with the GitHub macOS or Windows
# runner builds, where `phone` correctly degrades to filter-only.  Assertions
# about codec character have to skip there rather than fail.
gsm_required = pytest.mark.skipif(
    not audio_fx.encoder_available("libgsm"), reason="ffmpeg build has no libgsm"
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

    def test_vintage_filter_hash(self):
        out = mix_common.apply_vintage_filter(_tone(duration_ms=500))
        assert hashlib.sha256(out.raw_data).hexdigest() == GOLDEN_VINTAGE

    def test_vintage_never_invokes_ffmpeg(self, monkeypatch):
        """vintage must stay pure pydub — no subprocess on that path."""
        def _boom(*args, **kwargs):
            pytest.fail("ffmpeg spawned for the vintage filter")

        monkeypatch.setattr(audio_fx, "run_ffmpeg_filter", _boom)
        mix_common._apply_speaker_filters(_tone(), "vintage")


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
class TestPhoneResponse:
    """The band-limit that the pure-pydub implementation never actually had.

    The old chain left 80 Hz only ~11 dB down and 8 kHz only ~9 dB down, which
    reads as a slightly muffled voice.  These thresholds are set well inside the
    measured margins (~37 dB and ~47 dB) so an ffmpeg version bump does not trip
    them, while still failing loudly if the treatment ever degrades to a tilt.
    """

    @staticmethod
    def _delta(freq: int) -> float:
        tone = _tone(freq, gain_db=-20)
        return audio_fx.apply_treatment(tone, "phone").dBFS - tone.dBFS

    def test_low_end_is_rejected(self):
        assert self._delta(1000) - self._delta(80) >= 25

    def test_top_end_is_rejected(self):
        """Holds on filter-only builds too, where this measures ~20 dB.

        With libgsm the codec's 8 kHz rate brickwalls at 4 kHz and this jumps
        to ~47 dB — see test_codec_adds_a_hard_ceiling.  The threshold here is
        the floor the treatment must clear everywhere; the old pydub chain
        managed 9 dB.
        """
        assert self._delta(1000) - self._delta(6000) >= 15

    @gsm_required
    def test_codec_adds_a_hard_ceiling(self):
        """The 8 kHz codec rate is what turns a roll-off into a brickwall."""
        assert self._delta(1000) - self._delta(6000) >= 35

    def test_passband_is_reasonably_flat(self):
        span = [self._delta(f) for f in (500, 1000, 1700, 3000)]
        assert max(span) - min(span) <= 8

    def test_broadband_level_stays_near_unity(self):
        """No runaway gain and no collapse on material that spans the band.

        A sine is the wrong probe here: it sits entirely inside the passband, so
        it picks up the presence lift and comes out ~7 dB hot.  Broadband noise
        loses its out-of-band energy the way speech does.  The output trim is
        calibrated on real dialogue stems, where it lands ~1.4 dB under dry;
        this only pins that the treatment has not drifted into a blanket boost
        like the legacy flat +5 dB.
        """
        noise = WhiteNoise().to_audio_segment(duration=800) - 18
        out = audio_fx.apply_treatment(noise, "phone")
        assert abs(out.dBFS - noise.dBFS) <= 4

    def test_phone_and_speakerphone_differ(self):
        tone = _tone(1000, duration_ms=400, gain_db=-18)
        assert (audio_fx.apply_treatment(tone, "phone").raw_data
                != audio_fx.apply_treatment(tone, "speakerphone").raw_data)


@ffmpeg_required
class TestCodecRoundTrip:
    """The GSM stage is what makes it a mobile call rather than an EQ curve."""

    def _no_encoder(self, monkeypatch):
        """Make only the encode invocation fail, leaving the filter pass alone."""
        real = subprocess.run

        def fake(cmd, *args, **kwargs):
            if "-c:a" in cmd:
                return subprocess.CompletedProcess(cmd, 1, b"", b"Unknown encoder")
            return real(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake)

    @gsm_required
    def test_codec_changes_the_result(self):
        tone = _tone(1000, duration_ms=400, gain_db=-18)
        graph = audio_fx.TREATMENTS["phone"].graph
        plain = audio_fx.run_ffmpeg_filter(tone, graph, label="phone")
        coded = audio_fx.run_ffmpeg_filter(
            tone, graph, label="phone",
            codec="libgsm", container="gsm", codec_rate=8000,
        )
        assert plain.raw_data != coded.raw_data

    @gsm_required
    def test_codec_is_part_of_the_cache_key(self):
        """Two runs differing only in codec must not collide in the cache."""
        tone = _tone(1000, duration_ms=400, gain_db=-18)
        graph = audio_fx.TREATMENTS["phone"].graph
        first = audio_fx.run_ffmpeg_filter(tone, graph, label="phone")
        second = audio_fx.run_ffmpeg_filter(
            tone, graph, label="phone",
            codec="libgsm", container="gsm", codec_rate=8000,
        )
        assert first.raw_data != second.raw_data

    def test_missing_codec_keeps_the_filtered_audio(self, monkeypatch):
        """Losing the encoder must cost the grit, not the whole treatment."""
        tone = _tone(1000, duration_ms=400, gain_db=-18)
        self._no_encoder(monkeypatch)
        out = audio_fx.apply_treatment(tone, "phone")
        assert out.raw_data != tone.raw_data          # filters still applied
        assert len(out) == len(tone)

    def test_missing_codec_raises_in_strict_mode(self, monkeypatch):
        monkeypatch.setenv(audio_fx.STRICT_ENV_VAR, "1")
        self._no_encoder(monkeypatch)
        with pytest.raises(audio_fx.AudioFxError):
            audio_fx.apply_treatment(_tone(1000, gain_db=-18), "phone")

    def test_length_is_preserved_across_the_round_trip(self):
        """GSM codes in 20 ms frames, so odd lengths come back padded."""
        tone = _tone(1000, duration_ms=333, gain_db=-18)
        assert len(audio_fx.apply_treatment(tone, "phone")) == len(tone)


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


class TestMissingCodecs:
    """Which treatments this build cannot render at full character."""

    def test_reports_nothing_when_encoder_is_present(self, monkeypatch):
        monkeypatch.setattr(audio_fx, "encoder_available", lambda name: True)
        assert audio_fx.missing_codecs(["phone"]) == {}

    def test_reports_the_encoder_a_treatment_needs(self, monkeypatch):
        monkeypatch.setattr(audio_fx, "encoder_available", lambda name: False)
        assert audio_fx.missing_codecs(["phone"]) == {"phone": "libgsm"}

    def test_filter_only_treatments_are_never_reported(self, monkeypatch):
        """film and speakerphone declare no codec, so nothing can be missing."""
        monkeypatch.setattr(audio_fx, "encoder_available", lambda name: False)
        assert audio_fx.missing_codecs(["film", "speakerphone", "vintage"]) == {}

    def test_unknown_names_are_ignored(self, monkeypatch):
        monkeypatch.setattr(audio_fx, "encoder_available", lambda name: False)
        assert audio_fx.missing_codecs(["nonsense"]) == {}

    def test_encoder_is_probed_once_per_process(self, monkeypatch):
        """Each probe spawns ffmpeg; a render asks about the same codec often."""
        calls = []

        def counted(name):
            calls.append(name)
            return False

        monkeypatch.setattr(audio_fx, "encoder_available", counted)
        audio_fx.missing_codecs(["phone"])
        audio_fx.missing_codecs(["phone"])
        audio_fx.missing_codecs(["phone", "film"])
        assert calls == ["libgsm"]
