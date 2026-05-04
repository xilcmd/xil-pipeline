# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for mix_common.py — stem plans, clip effects, and layer builders."""

import math

import pytest
from pydub import AudioSegment
from pydub.generators import Sine

# ─── Import mix_common ───
from xil_pipeline import mix_common, models

collect_stem_plans = mix_common.collect_stem_plans
extract_seq = mix_common.extract_seq
_apply_clip_effects = mix_common._apply_clip_effects
_resolve_audio_params = mix_common._resolve_audio_params
_vf_engaged_seqs = mix_common._vf_engaged_seqs
_volume_pct_to_db = mix_common._volume_pct_to_db
build_ambience_layer = mix_common.build_ambience_layer
build_music_layer = mix_common.build_music_layer
build_sfx_layer = mix_common.build_sfx_layer
compute_dialogue_labels = mix_common.compute_dialogue_labels
StemPlan = mix_common.StemPlan

SfxConfiguration = models.SfxConfiguration


# ─── Helpers ───

def _write_mp3(path: str, duration_ms: int = 100) -> None:
    Sine(440).to_audio_segment(duration=duration_ms).export(path, format="mp3")


# ─── Tests ───

class TestCollectStemPlans:
    def test_sfx_stem_matching_dialogue_entry_is_skipped(self, tmp_path, caplog):
        """A `_sfx.mp3` stem whose seq maps to a dialogue entry must be skipped."""
        stem = tmp_path / "005_act-one_sfx.mp3"
        _write_mp3(str(stem))
        index = {5: {"seq": 5, "type": "dialogue", "direction_type": None, "text": "Hello"}}

        plans = collect_stem_plans(str(tmp_path), index)

        assert plans == []
        assert "WARNING" in caplog.text
        assert "005_act-one_sfx.mp3" in caplog.text
        assert "dialogue" in caplog.text

    def test_dialogue_stem_matching_direction_entry_is_skipped(self, tmp_path, caplog):
        """A speaker-named stem whose seq maps to a direction entry must be skipped."""
        stem = tmp_path / "005_act-one_adam.mp3"
        _write_mp3(str(stem))
        index = {5: {"seq": 5, "type": "direction", "direction_type": "SFX", "text": "SFX: BANG"}}

        plans = collect_stem_plans(str(tmp_path), index)

        assert plans == []
        assert "WARNING" in caplog.text
        assert "005_act-one_adam.mp3" in caplog.text
        assert "direction" in caplog.text

    def test_valid_dialogue_stem_is_kept(self, tmp_path):
        """A speaker-named stem whose seq maps to a dialogue entry must be kept."""
        stem = tmp_path / "005_act-one_adam.mp3"
        _write_mp3(str(stem))
        index = {5: {"seq": 5, "type": "dialogue", "direction_type": None, "text": "Hello"}}

        plans = collect_stem_plans(str(tmp_path), index)

        assert len(plans) == 1
        assert plans[0].seq == 5
        assert plans[0].entry_type == "dialogue"

    def test_valid_sfx_stem_is_kept(self, tmp_path):
        """A `_sfx.mp3` stem whose seq maps to a direction entry must be kept."""
        stem = tmp_path / "005_act-one_sfx.mp3"
        _write_mp3(str(stem))
        index = {5: {"seq": 5, "type": "direction", "direction_type": "SFX", "text": "SFX: BANG"}}

        plans = collect_stem_plans(str(tmp_path), index)

        assert len(plans) == 1
        assert plans[0].seq == 5
        assert plans[0].direction_type == "SFX"

    def test_unknown_seq_stem_is_skipped(self, tmp_path, caplog):
        """A stem whose seq is not in the index is stale and must be skipped with a warning."""
        stem = tmp_path / "099_act-two_sfx.mp3"
        _write_mp3(str(stem))
        index = {}  # seq 99 not present

        plans = collect_stem_plans(str(tmp_path), index)

        assert len(plans) == 0
        assert "not in parsed JSON" in caplog.text

    def test_old_preamble_filenames_are_silently_skipped(self, tmp_path, caplog):
        """Legacy preamble_tina.mp3 / preamble_music.mp3 have no numeric prefix — silently skipped."""
        _write_mp3(str(tmp_path / "preamble_tina.mp3"))
        _write_mp3(str(tmp_path / "preamble_music.mp3"))
        index = {}

        plans = collect_stem_plans(str(tmp_path), index)

        assert plans == []
        assert "WARNING" not in caplog.text

    def test_n002_preamble_tina_is_included_as_dialogue(self, tmp_path):
        """n002_preamble_tina.mp3 → seq -2 dialogue plan; foreground_override=False."""
        _write_mp3(str(tmp_path / "n002_preamble_tina.mp3"))
        index = {-2: {"seq": -2, "type": "dialogue", "direction_type": None, "text": "Hello."}}

        plans = collect_stem_plans(str(tmp_path), index)

        assert len(plans) == 1
        assert plans[0].seq == -2
        assert plans[0].entry_type == "dialogue"
        assert plans[0].foreground_override is False
        assert plans[0].is_background is False

    def test_n001_preamble_sfx_is_foreground_music(self, tmp_path):
        """n001_preamble_sfx.mp3 → seq -1 MUSIC plan; foreground_override=True."""
        _write_mp3(str(tmp_path / "n001_preamble_sfx.mp3"))
        index = {-1: {"seq": -1, "type": "direction", "direction_type": "MUSIC", "text": "INTRO MUSIC"}}

        plans = collect_stem_plans(str(tmp_path), index)

        assert len(plans) == 1
        assert plans[0].seq == -1
        assert plans[0].direction_type == "MUSIC"
        assert plans[0].foreground_override is True
        assert plans[0].is_background is False

    def test_negative_seqs_sort_before_positive(self, tmp_path):
        """Preamble seqs -2 and -1 sort before any parsed entry seq >= 1."""
        _write_mp3(str(tmp_path / "n002_preamble_tina.mp3"))
        _write_mp3(str(tmp_path / "n001_preamble_sfx.mp3"))
        _write_mp3(str(tmp_path / "001_cold-open_adam.mp3"))
        index = {
            -2: {"seq": -2, "type": "dialogue", "direction_type": None, "text": "Hello."},
            -1: {"seq": -1, "type": "direction", "direction_type": "MUSIC", "text": "INTRO MUSIC"},
            1: {"seq": 1, "type": "dialogue", "direction_type": None, "text": "Hi"},
        }

        plans = collect_stem_plans(str(tmp_path), index)
        plans_sorted = sorted(plans, key=lambda p: p.seq)

        assert plans_sorted[0].seq == -2
        assert plans_sorted[1].seq == -1
        assert plans_sorted[2].seq == 1



class TestExtractSeq:
    def test_positive_seq(self):
        assert extract_seq('stems/S01E01/003_cold-open_adam.mp3') == 3

    def test_positive_seq_high(self):
        assert extract_seq('stems/S01E01/099_act-two_sfx.mp3') == 99

    def test_negative_seq_n002(self):
        assert extract_seq('stems/S02E03/n002_preamble_tina.mp3') == -2

    def test_negative_seq_n001(self):
        assert extract_seq('stems/S02E03/n001_preamble_sfx.mp3') == -1

    def test_negative_seq_n010(self):
        assert extract_seq('stems/S02E03/n010_something_sfx.mp3') == -10

    def test_invalid_prefix_raises_value_error(self):
        with pytest.raises((ValueError, IndexError)):
            extract_seq('stems/S01E01/preamble_tina.mp3')



# ─── _volume_pct_to_db and _apply_clip_effects ───


class TestVolumePctToDb:
    def test_100_percent_is_zero_db(self):
        assert abs(_volume_pct_to_db(100.0)) < 1e-9

    def test_50_percent_is_minus_6db(self):
        # 20 * log10(0.5) ≈ -6.02
        assert abs(_volume_pct_to_db(50.0) - 20 * math.log10(0.5)) < 1e-9

    def test_200_percent_is_plus_6db(self):
        assert abs(_volume_pct_to_db(200.0) - 20 * math.log10(2.0)) < 1e-9

    def test_80_percent(self):
        expected = 20 * math.log10(0.80)
        assert abs(_volume_pct_to_db(80.0) - expected) < 1e-9

    def test_20_percent(self):
        expected = 20 * math.log10(0.20)
        assert abs(_volume_pct_to_db(20.0) - expected) < 1e-9

    def test_zero_returns_negative_inf(self):
        result = _volume_pct_to_db(0.0)
        assert result == -math.inf


def _make_sine(duration_ms: int = 500) -> AudioSegment:
    return Sine(440).to_audio_segment(duration=duration_ms)


class TestApplyClipEffects:
    def test_no_op_none_volume_zero_ramps_zero_level(self):
        clip = _make_sine(500)
        result = _apply_clip_effects(clip, None, 0, 0, 0)
        assert len(result) == len(clip)
        # dBFS should be unchanged
        assert abs(result.dBFS - clip.dBFS) < 0.5

    def test_volume_100_percent_unchanged(self):
        clip = _make_sine(500)
        result = _apply_clip_effects(clip, 100.0, 0, 0, 0)
        assert abs(result.dBFS - clip.dBFS) < 0.5

    def test_volume_50_percent_reduces_level(self):
        clip = _make_sine(500)
        result = _apply_clip_effects(clip, 50.0, 0, 0, 0)
        # ≈ -6 dB
        assert result.dBFS < clip.dBFS - 5

    def test_level_db_offset_applied(self):
        clip = _make_sine(500)
        result = _apply_clip_effects(clip, None, 0, 0, -6.0)
        assert result.dBFS < clip.dBFS - 5

    def test_ramp_in_does_not_change_duration(self):
        clip = _make_sine(500)
        result = _apply_clip_effects(clip, None, 100, 0, 0)
        assert len(result) == len(clip)

    def test_ramp_out_does_not_change_duration(self):
        clip = _make_sine(500)
        result = _apply_clip_effects(clip, None, 0, 100, 0)
        assert len(result) == len(clip)

    def test_combined_volume_and_ramps(self):
        clip = _make_sine(500)
        result = _apply_clip_effects(clip, 80.0, 50, 50, -2.0)
        assert len(result) == len(clip)
        assert result.dBFS < clip.dBFS  # overall quieter


# ─── collect_stem_plans with sfx_config ───


def _make_sfx_config(**defaults_overrides):
    """Return a minimal SfxConfiguration with given defaults."""
    defaults = {
        "music_volume_percentage": 80,
        "music_ramp_in_seconds": 1.0,
        "music_ramp_out_seconds": 1.0,
        "ambience_volume_percentage": 20,
        "ambience_ramp_in_seconds": 1.0,
        "ambience_ramp_out_seconds": 1.0,
    }
    defaults.update(defaults_overrides)
    return SfxConfiguration(show="THE 413", episode=1, defaults=defaults, effects={})


class TestCollectStemPlansWithSfxConfig:
    def test_music_plan_gets_category_defaults(self, tmp_path):
        stem = tmp_path / "010_act-one_sfx.mp3"
        _write_mp3(str(stem))
        index = {10: {"seq": 10, "type": "direction", "direction_type": "MUSIC", "text": "MUSIC: THEME"}}
        sfx_config = _make_sfx_config()

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert len(plans) == 1
        assert plans[0].volume_percentage == 80
        assert plans[0].ramp_in_seconds == 1.0
        assert plans[0].ramp_out_seconds == 1.0

    def test_ambience_plan_gets_category_defaults(self, tmp_path):
        stem = tmp_path / "020_act-one_sfx.mp3"
        _write_mp3(str(stem))
        index = {20: {"seq": 20, "type": "direction", "direction_type": "AMBIENCE", "text": "AMBIENCE: DINER"}}
        sfx_config = _make_sfx_config()

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert len(plans) == 1
        assert plans[0].volume_percentage == 20
        assert plans[0].ramp_in_seconds == 1.0
        assert plans[0].ramp_out_seconds == 1.0

    def test_dialogue_plan_gets_none_values(self, tmp_path):
        stem = tmp_path / "005_cold-open_adam.mp3"
        _write_mp3(str(stem))
        index = {5: {"seq": 5, "type": "dialogue", "direction_type": None, "text": "Hello"}}
        sfx_config = _make_sfx_config()

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert len(plans) == 1
        assert plans[0].volume_percentage is None
        assert plans[0].ramp_in_seconds is None
        assert plans[0].ramp_out_seconds is None

    def test_sfx_plan_gets_none_values(self, tmp_path):
        stem = tmp_path / "007_cold-open_sfx.mp3"
        _write_mp3(str(stem))
        index = {7: {"seq": 7, "type": "direction", "direction_type": "SFX", "text": "SFX: BANG"}}
        sfx_config = _make_sfx_config()

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert len(plans) == 1
        assert plans[0].volume_percentage is None

    def test_no_sfx_config_gives_none_values(self, tmp_path):
        stem = tmp_path / "010_act-one_sfx.mp3"
        _write_mp3(str(stem))
        index = {10: {"seq": 10, "type": "direction", "direction_type": "MUSIC", "text": "MUSIC: THEME"}}

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=None)

        assert plans[0].volume_percentage is None
        assert plans[0].ramp_in_seconds is None
        assert plans[0].ramp_out_seconds is None

    def test_per_effect_override_takes_priority(self, tmp_path):
        stem = tmp_path / "010_act-one_sfx.mp3"
        _write_mp3(str(stem))
        index = {10: {"seq": 10, "type": "direction", "direction_type": "MUSIC", "text": "MUSIC: THEME"}}
        sfx_config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={
                "music_volume_percentage": 80,
                "music_ramp_in_seconds": 1.0,
                "music_ramp_out_seconds": 1.0,
            },
            effects={
                "MUSIC: THEME": {
                    "prompt": "Dramatic orchestral theme",
                    "duration_seconds": 10.0,
                    "volume_percentage": 50.0,
                    "ramp_in_seconds": 2.0,
                    "ramp_out_seconds": 0.0,
                },
            },
        )

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert plans[0].volume_percentage == 50.0
        assert plans[0].ramp_in_seconds == 2.0
        assert plans[0].ramp_out_seconds == 0.0


# ─── build_ambience_layer and build_music_layer with volume/ramp ───


def _make_plan(seq, filepath, direction_type, entry_type="direction",
               text=None, volume_percentage=None, ramp_in_seconds=None,
               ramp_out_seconds=None):
    plan = StemPlan(
        seq=seq, filepath=filepath,
        direction_type=direction_type, entry_type=entry_type, text=text,
    )
    plan.volume_percentage = volume_percentage
    plan.ramp_in_seconds = ramp_in_seconds
    plan.ramp_out_seconds = ramp_out_seconds
    return plan


class TestBuildAmbienceLayerWithVolumeRamp:
    def test_volume_percentage_reduces_ambience_level(self, tmp_path):
        mp3_path = str(tmp_path / "amb.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "AMBIENCE", volume_percentage=20.0)
        timeline = {1: 0}
        total_ms = 400

        layer, labels = build_ambience_layer([plan], timeline, total_ms, level_db=0)

        assert len(layer) == total_ms
        assert len(labels) == 1
        # With 20% volume the layer should be significantly quieter than the raw clip
        raw_clip = AudioSegment.from_file(mp3_path)
        assert layer.dBFS < raw_clip.dBFS - 5

    def test_ramp_fields_do_not_error(self, tmp_path):
        mp3_path = str(tmp_path / "amb.mp3")
        _write_mp3(mp3_path, duration_ms=500)
        plan = _make_plan(1, mp3_path, "AMBIENCE",
                          ramp_in_seconds=0.05, ramp_out_seconds=0.05)
        timeline = {1: 0}
        total_ms = 600

        layer, labels = build_ambience_layer([plan], timeline, total_ms, level_db=0)

        assert len(layer) == total_ms

    def test_no_volume_ramp_uses_level_db_only(self, tmp_path):
        mp3_path = str(tmp_path / "amb.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "AMBIENCE")
        timeline = {1: 0}
        total_ms = 400

        layer, _ = build_ambience_layer([plan], timeline, total_ms, level_db=-10.0)
        assert len(layer) == total_ms


class TestBuildMusicLayerWithVolumeRamp:
    def test_volume_percentage_reduces_music_level(self, tmp_path):
        mp3_path = str(tmp_path / "mus.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "MUSIC", volume_percentage=20.0)
        timeline = {1: 0}
        total_ms = 400

        layer, labels = build_music_layer([plan], timeline, total_ms, level_db=0)

        assert len(layer) == total_ms
        assert len(labels) == 1
        raw_clip = AudioSegment.from_file(mp3_path)
        assert layer.dBFS < raw_clip.dBFS - 5

    def test_ramp_fields_do_not_error(self, tmp_path):
        mp3_path = str(tmp_path / "mus.mp3")
        _write_mp3(mp3_path, duration_ms=500)
        plan = _make_plan(1, mp3_path, "MUSIC",
                          ramp_in_seconds=0.05, ramp_out_seconds=0.05)
        timeline = {1: 0}
        total_ms = 600

        layer, labels = build_music_layer([plan], timeline, total_ms, level_db=0)

        assert len(layer) == total_ms

    def test_no_volume_ramp_uses_level_db_only(self, tmp_path):
        mp3_path = str(tmp_path / "mus.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "MUSIC")
        timeline = {1: 0}
        total_ms = 400

        layer, _ = build_music_layer([plan], timeline, total_ms, level_db=-6.0)
        assert len(layer) == total_ms

    def test_play_duration_truncates_clip(self, tmp_path):
        """play_duration=50 should halve the active clip duration in the label."""
        mp3_path = str(tmp_path / "mus.mp3")
        _write_mp3(mp3_path, duration_ms=400)
        plan = _make_plan(1, mp3_path, "MUSIC")
        plan.play_duration = 50.0
        timeline = {1: 0}
        total_ms = 600

        layer, labels = build_music_layer([plan], timeline, total_ms, level_db=0)

        assert len(labels) == 1
        start_s, end_s, *_ = labels[0]
        # With 50% play_duration the label end should be ~200ms after start
        assert (end_s - start_s) < 0.3  # < 300ms (original 400ms, half = 200ms)

    def test_play_duration_none_plays_full_clip(self, tmp_path):
        """play_duration=None should play the full clip."""
        mp3_path = str(tmp_path / "mus.mp3")
        _write_mp3(mp3_path, duration_ms=400)
        plan = _make_plan(1, mp3_path, "MUSIC")
        plan.play_duration = None
        timeline = {1: 0}
        total_ms = 600

        layer, labels = build_music_layer([plan], timeline, total_ms, level_db=0)

        start_s, end_s, *_ = labels[0]
        assert (end_s - start_s) > 0.35  # > 350ms (full ~400ms clip)

    def test_labels_include_ramp_data(self, tmp_path):
        """Music labels should be 6-tuples carrying ramp and play_duration data."""
        mp3_path = str(tmp_path / "mus.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "MUSIC",
                          ramp_in_seconds=0.5, ramp_out_seconds=1.0)
        plan.play_duration = 75.0
        timeline = {1: 0}

        _, labels = build_music_layer([plan], timeline, 400, level_db=0)

        assert len(labels[0]) == 9
        assert labels[0][3] == 0.5   # ramp_in_seconds
        assert labels[0][4] == 1.0   # ramp_out_seconds
        assert labels[0][5] == 75.0  # play_duration
        assert labels[0][7] is None  # volume_pct (not set)
        assert labels[0][8] == 1     # seq


class TestAmbienceLabelRampData:
    def test_ambience_labels_include_ramp_data(self, tmp_path):
        """Ambience labels should be 9-tuples carrying ramp, volume, and seq data."""
        mp3_path = str(tmp_path / "amb.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "AMBIENCE",
                          ramp_in_seconds=0.5, ramp_out_seconds=1.5)
        timeline = {1: 0}

        _, labels = build_ambience_layer([plan], timeline, 400, level_db=0)

        assert len(labels[0]) == 9
        assert labels[0][3] == 0.5   # ramp_in_seconds
        assert labels[0][4] == 1.5   # ramp_out_seconds
        assert labels[0][7] is None  # volume_pct (not set)
        assert labels[0][8] == 1     # seq

    def test_ambience_labels_none_ramp_when_not_set(self, tmp_path):
        """Ambience labels carry None for ramp when plan has no ramp values."""
        mp3_path = str(tmp_path / "amb.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "AMBIENCE")
        timeline = {1: 0}

        _, labels = build_ambience_layer([plan], timeline, 400, level_db=0)

        assert labels[0][3] is None
        assert labels[0][4] is None


# ─── Tests: compute_dialogue_labels snippet ───

class TestComputeDialogueLabelsSnippet:
    def test_snippet_contains_first_five_words(self, tmp_path):
        mp3_path = str(tmp_path / "001_cold-open_adam.mp3")
        _write_mp3(mp3_path, duration_ms=500)
        plan = _make_plan(1, mp3_path, None, entry_type="dialogue",
                          text="It's 7:14 AM on a Saturday in February.")
        timeline = {1: 0}

        labels = compute_dialogue_labels([plan], timeline)

        assert len(labels) == 1
        tup = labels[0]
        assert len(tup) == 9
        assert tup[6] == "It's 7:14 AM on a"
        assert tup[8] == 1  # seq

    def test_snippet_is_none_when_no_text(self, tmp_path):
        mp3_path = str(tmp_path / "001_cold-open_adam.mp3")
        _write_mp3(mp3_path, duration_ms=500)
        plan = _make_plan(1, mp3_path, None, entry_type="dialogue", text=None)
        timeline = {1: 0}

        labels = compute_dialogue_labels([plan], timeline)

        assert labels[0][6] is None

    def test_snippet_short_text_not_truncated(self, tmp_path):
        mp3_path = str(tmp_path / "001_cold-open_adam.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, None, entry_type="dialogue",
                          text="Hello world")
        timeline = {1: 0}

        labels = compute_dialogue_labels([plan], timeline)

        assert labels[0][6] == "Hello world"

    def test_non_dialogue_entries_excluded(self, tmp_path):
        mp3_path = str(tmp_path / "001_cold-open_sfx.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "SFX", entry_type="direction",
                          text="SOUND: phone buzzes")
        timeline = {1: 0}

        labels = compute_dialogue_labels([plan], timeline)

        assert labels == []

    def test_ramp_positions_are_none(self, tmp_path):
        mp3_path = str(tmp_path / "001_cold-open_adam.mp3")
        _write_mp3(mp3_path, duration_ms=300)
        plan = _make_plan(1, mp3_path, None, entry_type="dialogue",
                          text="Some dialogue text here now.")
        timeline = {1: 0}

        labels = compute_dialogue_labels([plan], timeline)

        tup = labels[0]
        assert tup[3] is None  # ramp_in
        assert tup[4] is None  # ramp_out
        assert tup[5] is None  # play_duration


# ─── Tests: SFX volume_percentage support ───


class TestSfxVolumePercentage:
    def test_sfx_plan_gets_category_default(self, tmp_path):
        """SFX stems should pick up sfx_volume_percentage from defaults."""
        stem = tmp_path / "007_cold-open_sfx.mp3"
        _write_mp3(str(stem))
        index = {7: {"seq": 7, "type": "direction", "direction_type": "SFX", "text": "SFX: BANG"}}
        sfx_config = _make_sfx_config(sfx_volume_percentage=60)

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert len(plans) == 1
        assert plans[0].volume_percentage == 60

    def test_sfx_per_effect_override(self, tmp_path):
        """Per-effect volume_percentage should override the category default."""
        stem = tmp_path / "007_cold-open_sfx.mp3"
        _write_mp3(str(stem))
        index = {7: {"seq": 7, "type": "direction", "direction_type": "SFX", "text": "SFX: BANG"}}
        sfx_config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"sfx_volume_percentage": 60},
            effects={
                "SFX: BANG": {
                    "prompt": "Loud bang",
                    "duration_seconds": 1.0,
                    "volume_percentage": 30.0,
                },
            },
        )

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert plans[0].volume_percentage == 30.0

    def test_sfx_no_default_no_effect_gives_none(self, tmp_path):
        """SFX with no defaults and no per-effect config should get None."""
        stem = tmp_path / "007_cold-open_sfx.mp3"
        _write_mp3(str(stem))
        index = {7: {"seq": 7, "type": "direction", "direction_type": "SFX", "text": "SFX: BANG"}}
        sfx_config = _make_sfx_config()  # no sfx_volume_percentage

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert plans[0].volume_percentage is None

    def test_beat_plan_gets_sfx_category_default(self, tmp_path):
        """BEAT stems should also pick up sfx_volume_percentage."""
        stem = tmp_path / "008_cold-open_sfx.mp3"
        _write_mp3(str(stem))
        index = {8: {"seq": 8, "type": "direction", "direction_type": "BEAT", "text": "BEAT"}}
        sfx_config = _make_sfx_config(sfx_volume_percentage=50)

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert plans[0].volume_percentage == 50

    def test_build_sfx_layer_applies_volume(self, tmp_path):
        """build_sfx_layer should apply volume_percentage to the overlaid segment."""
        mp3_path = str(tmp_path / "sfx.mp3")
        _write_mp3(mp3_path, duration_ms=200)
        plan = _make_plan(1, mp3_path, "SFX", volume_percentage=20.0)
        timeline = {1: 0}
        total_ms = 400

        layer, labels = build_sfx_layer([plan], timeline, total_ms)

        assert len(layer) == total_ms
        assert len(labels) == 1
        # With 20% volume the output should be quieter than the raw clip
        raw_clip = AudioSegment.from_file(mp3_path)
        # Sample the layer at the position where the SFX was placed
        sfx_region = layer[:200]
        assert sfx_region.dBFS < raw_clip.dBFS - 5

    def test_emdash_key_matches_hyphen_text(self, tmp_path):
        """SFX config with em-dash key should match parsed text with hyphen."""
        stem = tmp_path / "007_cold-open_sfx.mp3"
        _write_mp3(str(stem))
        # Parsed text uses plain hyphen
        index = {7: {"seq": 7, "type": "direction", "direction_type": "SFX",
                      "text": "SFX: PHONE BUZZING - DIFFERENT TONE"}}
        # Config key uses em-dash
        sfx_config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={},
            effects={
                "SFX: PHONE BUZZING \u2014 DIFFERENT TONE": {
                    "prompt": "Phone buzzing",
                    "duration_seconds": 5.0,
                    "volume_percentage": 50.0,
                },
            },
        )

        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)

        assert plans[0].volume_percentage == 50.0


# ─── _resolve_audio_params: global ramp fallback ───


class TestResolveAudioParamsGlobalFallback:
    """_resolve_audio_params() falls back to global ramp keys when category-specific keys absent."""

    def _music_plan(self, text="MUSIC: THEME"):
        return StemPlan(seq=1, filepath="", direction_type="MUSIC",
                        entry_type="direction", text=text)

    def test_music_uses_global_ramp_when_no_music_prefix_key(self):
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"ramp_in_seconds": 1.5, "ramp_out_seconds": 2.0},
            effects={},
        )
        plan = self._music_plan()
        _, ri, ro, _ = _resolve_audio_params(plan, config)
        assert ri == 1.5
        assert ro == 2.0

    def test_music_category_prefix_wins_over_global(self):
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={
                "music_ramp_in_seconds": 0.5,
                "music_ramp_out_seconds": 0.5,
                "ramp_in_seconds": 9.0,
                "ramp_out_seconds": 9.0,
            },
            effects={},
        )
        plan = self._music_plan()
        _, ri, ro, _ = _resolve_audio_params(plan, config)
        assert ri == 0.5
        assert ro == 0.5

    def test_per_effect_wins_over_global_ramp(self):
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"ramp_in_seconds": 9.0, "ramp_out_seconds": 9.0},
            effects={
                "MUSIC: THEME": {
                    "prompt": "theme",
                    "duration_seconds": 10.0,
                    "ramp_in_seconds": 0.25,
                    "ramp_out_seconds": 0.25,
                },
            },
        )
        plan = self._music_plan()
        _, ri, ro, _ = _resolve_audio_params(plan, config)
        assert ri == 0.25
        assert ro == 0.25

    def test_ambience_still_uses_category_key(self):
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={
                "ambience_ramp_in_seconds": 3.0,
                "ambience_ramp_out_seconds": 3.0,
                "ramp_in_seconds": 9.0,
                "ramp_out_seconds": 9.0,
            },
            effects={},
        )
        plan = StemPlan(seq=2, filepath="", direction_type="AMBIENCE",
                        entry_type="direction", text="AMBIENCE: RAIN")
        _, ri, ro, _ = _resolve_audio_params(plan, config)
        assert ri == 3.0
        assert ro == 3.0

    def test_sfx_global_ramp_resolves_but_has_no_audio_effect(self):
        """SFX picks up global ramp value (build_sfx_layer ignores it, so harmless)."""
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"ramp_in_seconds": 1.0, "ramp_out_seconds": 1.0},
            effects={},
        )
        plan = StemPlan(seq=3, filepath="", direction_type="SFX",
                        entry_type="direction", text="SFX: BANG")
        _, ri, ro, _ = _resolve_audio_params(plan, config)
        assert ri == 1.0
        assert ro == 1.0

    def test_music_no_ramp_anywhere_gives_none(self):
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"music_volume_percentage": 80},
            effects={},
        )
        plan = self._music_plan()
        _, ri, ro, _ = _resolve_audio_params(plan, config)
        assert ri is None
        assert ro is None

    def test_sfx_uses_global_volume_when_no_sfx_prefix_key(self):
        """SFX plan gets global volume_percentage when sfx_volume_percentage is absent."""
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"volume_percentage": 20},
            effects={},
        )
        plan = StemPlan(seq=3, filepath="", direction_type="SFX",
                        entry_type="direction", text="SFX: BANG")
        vol, _, _, _ = _resolve_audio_params(plan, config)
        assert vol == 20

    def test_music_uses_global_volume_when_no_music_prefix_key(self):
        """MUSIC plan gets global volume_percentage when music_volume_percentage is absent."""
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"volume_percentage": 20},
            effects={},
        )
        plan = StemPlan(seq=1, filepath="", direction_type="MUSIC",
                        entry_type="direction", text="MUSIC: THEME")
        vol, _, _, _ = _resolve_audio_params(plan, config)
        assert vol == 20

    def test_category_volume_wins_over_global(self):
        """sfx_volume_percentage takes priority over global volume_percentage."""
        config = SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"sfx_volume_percentage": 50, "volume_percentage": 20},
            effects={},
        )
        plan = StemPlan(seq=3, filepath="", direction_type="SFX",
                        entry_type="direction", text="SFX: BANG")
        vol, _, _, _ = _resolve_audio_params(plan, config)
        assert vol == 50


# ── collect_stem_plans: duration_seconds → play_duration for source-based clips ──

class TestSourceDurationSecondsToPlayDuration:
    """duration_seconds on a source= entry is converted to play_duration percentage."""

    def _make_sfx_config_with_source(self, key, source_path, duration_seconds,
                                      play_duration=None):
        effects = {
            key: {
                "source": source_path,
                "duration_seconds": duration_seconds,
                **({"play_duration": play_duration} if play_duration is not None else {}),
            }
        }
        return SfxConfiguration(show="TEST", episode=1, defaults={}, effects=effects)

    def test_duration_seconds_converted_to_play_duration(self, tmp_path):
        """A 30s source clip with duration_seconds=15 → play_duration ~50%."""
        stem = tmp_path / "010_act-one_sfx.mp3"
        _write_mp3(str(stem), duration_ms=30_000)
        source = tmp_path / "source.mp3"
        _write_mp3(str(source), duration_ms=30_000)
        index = {10: {"seq": 10, "type": "direction", "direction_type": "MUSIC",
                      "text": "MUSIC: THEME", "section": "act-one"}}
        sfx_config = self._make_sfx_config_with_source(
            "MUSIC: THEME", str(source), duration_seconds=15.0
        )
        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)
        assert len(plans) == 1
        pd = plans[0].play_duration
        assert pd is not None
        # 15s / 30s * 100 = 50%
        assert abs(pd - 50.0) < 1.0

    def test_explicit_play_duration_wins_over_duration_seconds(self, tmp_path):
        """Explicit play_duration in config is not overridden by duration_seconds."""
        stem = tmp_path / "010_act-one_sfx.mp3"
        _write_mp3(str(stem), duration_ms=30_000)
        source = tmp_path / "source.mp3"
        _write_mp3(str(source), duration_ms=30_000)
        index = {10: {"seq": 10, "type": "direction", "direction_type": "MUSIC",
                      "text": "MUSIC: THEME", "section": "act-one"}}
        sfx_config = self._make_sfx_config_with_source(
            "MUSIC: THEME", str(source), duration_seconds=15.0, play_duration=75.0
        )
        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)
        assert len(plans) == 1
        # play_duration from config (75) takes priority; duration_seconds is ignored
        assert abs(plans[0].play_duration - 75.0) < 0.1

    def test_duration_seconds_capped_at_100_percent(self, tmp_path):
        """duration_seconds longer than clip → play_duration capped at 100%."""
        stem = tmp_path / "010_act-one_sfx.mp3"
        _write_mp3(str(stem), duration_ms=5_000)
        source = tmp_path / "source.mp3"
        _write_mp3(str(source), duration_ms=5_000)
        index = {10: {"seq": 10, "type": "direction", "direction_type": "MUSIC",
                      "text": "MUSIC: THEME", "section": "act-one"}}
        sfx_config = self._make_sfx_config_with_source(
            "MUSIC: THEME", str(source), duration_seconds=99.0
        )
        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)
        assert len(plans) == 1
        assert plans[0].play_duration == 100.0

    def test_non_source_entry_unaffected(self, tmp_path):
        """API-generated (no source) entries are not affected."""
        stem = tmp_path / "010_act-one_sfx.mp3"
        _write_mp3(str(stem))
        index = {10: {"seq": 10, "type": "direction", "direction_type": "MUSIC",
                      "text": "MUSIC: THEME", "section": "act-one"}}
        effects = {"MUSIC: THEME": {"prompt": "theme music", "duration_seconds": 15.0}}
        sfx_config = SfxConfiguration(show="TEST", episode=1, defaults={}, effects=effects)
        plans = collect_stem_plans(str(tmp_path), index, sfx_config=sfx_config)
        assert len(plans) == 1
        assert plans[0].play_duration is None


# ─── Tests: _vf_engaged_seqs ───

class TestVfEngagedSeqs:
    def _make_plan(self, seq, entry_type, direction_type=None, text=None):
        return StemPlan(seq=seq, filepath="", entry_type=entry_type,
                        direction_type=direction_type, text=text)

    def test_dialogue_between_engage_disengage_is_included(self):
        plans = [
            self._make_plan(10, "direction", "VINTAGE FILTER", "VINTAGE FILTER: ENGAGES"),
            self._make_plan(11, "dialogue"),
            self._make_plan(12, "dialogue"),
            self._make_plan(13, "direction", "VINTAGE FILTER", "VINTAGE FILTER: DISENGAGES"),
            self._make_plan(14, "dialogue"),
        ]
        engaged = _vf_engaged_seqs(plans)
        assert 11 in engaged
        assert 12 in engaged
        assert 14 not in engaged  # after DISENGAGES

    def test_no_markers_returns_empty(self):
        plans = [
            self._make_plan(1, "dialogue"),
            self._make_plan(2, "dialogue"),
        ]
        assert _vf_engaged_seqs(plans) == frozenset()

    def test_engage_without_disengage_runs_to_end(self):
        plans = [
            self._make_plan(5, "dialogue"),
            self._make_plan(6, "direction", "VINTAGE FILTER", "VINTAGE FILTER: ENGAGES"),
            self._make_plan(7, "dialogue"),
            self._make_plan(8, "dialogue"),
        ]
        engaged = _vf_engaged_seqs(plans)
        assert 5 not in engaged  # before ENGAGES
        assert 7 in engaged
        assert 8 in engaged

    def test_multiple_spans(self):
        plans = [
            self._make_plan(1, "direction", "VINTAGE FILTER", "VINTAGE FILTER: ENGAGES"),
            self._make_plan(2, "dialogue"),
            self._make_plan(3, "direction", "VINTAGE FILTER", "VINTAGE FILTER: DISENGAGES"),
            self._make_plan(4, "dialogue"),
            self._make_plan(5, "direction", "VINTAGE FILTER", "VINTAGE FILTER: ENGAGES"),
            self._make_plan(6, "dialogue"),
            self._make_plan(7, "direction", "VINTAGE FILTER", "VINTAGE FILTER: DISENGAGES"),
            self._make_plan(8, "dialogue"),
        ]
        engaged = _vf_engaged_seqs(plans)
        assert 2 in engaged
        assert 4 not in engaged
        assert 6 in engaged
        assert 8 not in engaged

    def test_non_dialogue_entries_ignored(self):
        plans = [
            self._make_plan(1, "direction", "VINTAGE FILTER", "VINTAGE FILTER: ENGAGES"),
            self._make_plan(2, "direction", "SFX"),
            self._make_plan(3, "section_header"),
        ]
        engaged = _vf_engaged_seqs(plans)
        assert len(engaged) == 0  # no dialogue entries in span
