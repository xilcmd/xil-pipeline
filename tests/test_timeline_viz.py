# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for timeline_viz.py — multitrack timeline visualization."""

import os

import pytest

# ─── Import timeline_viz ───

from xil_pipeline import timeline_viz

build_timeline_data = timeline_viz.build_timeline_data
render_terminal_timeline = timeline_viz.render_terminal_timeline
render_html_timeline = timeline_viz.render_html_timeline
LayerSpan = timeline_viz.LayerSpan
TimelineData = timeline_viz.TimelineData


# ─── Fixtures ───

@pytest.fixture
def sample_data():
    """A small but realistic TimelineData for testing."""
    return build_timeline_data(
        tag="S02E03",
        total_s=220.0,
        dlg_labels=[
            (0.0, 15.0, "tina"),
            (120.0, 125.0, "adam"),
            (130.0, 138.0, "maya"),
        ],
        amb_labels=[
            (120.0, 180.0, "AMBIENCE: RADIO STATIC"),
            (180.0, 220.0, "AMBIENCE: DINER BACKGROUND"),
        ],
        mus_labels=[
            (15.0, 120.0, "INTRO MUSIC"),
            (140.0, 148.0, "MUSIC: THEME STING"),
        ],
        sfx_labels=[
            (125.0, 126.0, "BEAT"),
            (128.0, 129.0, "BEAT"),
            (150.0, 155.0, "SFX: COFFEE POUR"),
        ],
    )


# ─── Tests: build_timeline_data ───

class TestBuildTimelineData:
    def test_returns_timeline_data(self, sample_data):
        assert isinstance(sample_data, TimelineData)
        assert sample_data.tag == "S02E03"
        assert sample_data.total_duration_s == 220.0

    def test_all_four_layers_present(self, sample_data):
        assert set(sample_data.layers.keys()) == {"dialogue", "ambience", "music", "sfx"}

    def test_dialogue_spans(self, sample_data):
        dlg = sample_data.layers["dialogue"]
        assert len(dlg) == 3
        assert dlg[0].label == "tina"
        assert dlg[0].start_s == 0.0
        assert dlg[0].end_s == 15.0

    def test_ambience_spans(self, sample_data):
        amb = sample_data.layers["ambience"]
        assert len(amb) == 2
        assert amb[0].label == "AMBIENCE: RADIO STATIC"

    def test_music_spans(self, sample_data):
        mus = sample_data.layers["music"]
        assert len(mus) == 2

    def test_sfx_spans(self, sample_data):
        sfx = sample_data.layers["sfx"]
        assert len(sfx) == 3

    def test_empty_labels(self):
        td = build_timeline_data("TEST", 60.0, [], [], [], [])
        for key in ("dialogue", "ambience", "music", "sfx"):
            assert td.layers[key] == []

    def test_span_attributes(self, sample_data):
        span = sample_data.layers["music"][0]
        assert isinstance(span, LayerSpan)
        assert span.start_s == 15.0
        assert span.end_s == 120.0
        assert span.label == "INTRO MUSIC"


# ─── Tests: render_terminal_timeline ───

class TestRenderTerminalTimeline:
    def test_contains_tag_and_duration(self, sample_data):
        out = render_terminal_timeline(sample_data, width=120)
        assert "S02E03" in out
        assert "3:40" in out

    def test_contains_layer_names(self, sample_data):
        out = render_terminal_timeline(sample_data, width=120)
        assert "DIALOGUE" in out
        assert "AMBIENCE" in out
        assert "MUSIC" in out
        assert "SFX" in out

    def test_contains_speaker_labels(self, sample_data):
        out = render_terminal_timeline(sample_data, width=120)
        assert "tina" in out
        assert "adam" in out

    def test_ruler_has_time_marks(self, sample_data):
        out = render_terminal_timeline(sample_data, width=120)
        assert "0:00" in out
        # 220s episode uses 60s intervals
        assert "1:00" in out

    def test_respects_width(self, sample_data):
        out = render_terminal_timeline(sample_data, width=80)
        for line in out.split("\n"):
            assert len(line) <= 80 + 5  # small tolerance for Unicode

    def test_zero_duration(self):
        td = TimelineData(tag="EMPTY", total_duration_s=0.0, layers={})
        out = render_terminal_timeline(td, width=80)
        assert "EMPTY" in out
        assert "0:00" in out

    def test_empty_layers_no_crash(self):
        td = build_timeline_data("TEST", 60.0, [], [], [], [])
        out = render_terminal_timeline(td, width=100)
        assert "TEST" in out
        # Should still have ruler
        assert "0:00" in out

    def test_uses_fill_characters(self, sample_data):
        out = render_terminal_timeline(sample_data, width=120)
        assert "█" in out or "▓" in out


# ─── Tests: render_html_timeline ───

class TestRenderHtmlTimeline:
    def test_writes_file(self, sample_data, tmp_path):
        out_path = str(tmp_path / "timeline.html")
        result = render_html_timeline(sample_data, out_path)
        assert result == out_path
        assert os.path.exists(out_path)

    def test_valid_html_structure(self, sample_data, tmp_path):
        out_path = str(tmp_path / "timeline.html")
        render_html_timeline(sample_data, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "<!DOCTYPE html>" in content
        assert "</html>" in content
        assert "<script>" in content
        assert "</script>" in content

    def test_contains_layer_json(self, sample_data, tmp_path):
        out_path = str(tmp_path / "timeline.html")
        render_html_timeline(sample_data, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert '"dialogue"' in content
        assert '"ambience"' in content
        assert '"music"' in content
        assert '"sfx"' in content

    def test_contains_tag(self, sample_data, tmp_path):
        out_path = str(tmp_path / "timeline.html")
        render_html_timeline(sample_data, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "S02E03" in content

    def test_contains_all_layer_names(self, sample_data, tmp_path):
        out_path = str(tmp_path / "timeline.html")
        render_html_timeline(sample_data, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        for name in ("Dialogue", "Ambience", "Music", "SFX"):
            assert name in content

    def test_contains_span_labels(self, sample_data, tmp_path):
        out_path = str(tmp_path / "timeline.html")
        render_html_timeline(sample_data, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "tina" in content
        assert "INTRO MUSIC" in content

    def test_creates_parent_dirs(self, sample_data, tmp_path):
        out_path = str(tmp_path / "sub" / "dir" / "timeline.html")
        render_html_timeline(sample_data, out_path)
        assert os.path.exists(out_path)

    def test_self_contained_no_cdn(self, sample_data, tmp_path):
        out_path = str(tmp_path / "timeline.html")
        render_html_timeline(sample_data, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        # No external CDN references
        assert "cdn" not in content.lower()
        assert "https://" not in content


# ─── Tests: mix_common label helpers ───

from xil_pipeline import mix_common

StemPlan = mix_common.StemPlan
build_foreground_timeline_only = mix_common.build_foreground_timeline_only
compute_dialogue_labels = mix_common.compute_dialogue_labels
compute_ambience_labels = mix_common.compute_ambience_labels
compute_music_labels = mix_common.compute_music_labels
compute_sfx_labels = mix_common.compute_sfx_labels


def _write_mp3(path, duration_ms=500):
    from pydub.generators import Sine
    Sine(440).to_audio_segment(duration=duration_ms).export(str(path), format="mp3")


class TestBuildForegroundTimelineOnly:
    def test_basic_timeline(self, tmp_path):
        """Foreground stems get sequential positions, background stems don't advance cursor."""
        p1 = tmp_path / "001_act-one_adam.mp3"
        p2 = tmp_path / "002_act-one_sfx.mp3"
        p3 = tmp_path / "003_act-one_amb.mp3"  # background
        _write_mp3(p1, 1000)
        _write_mp3(p2, 500)
        _write_mp3(p3, 2000)

        plans = [
            StemPlan(seq=1, filepath=str(p1), direction_type=None, entry_type="dialogue"),
            StemPlan(seq=2, filepath=str(p2), direction_type="SFX", entry_type="direction"),
            StemPlan(seq=3, filepath=str(p3), direction_type="AMBIENCE", entry_type="direction"),
        ]

        total_ms, timeline = build_foreground_timeline_only(plans, gap_ms=600)

        assert timeline[1] == 0
        # seq 2 starts after seq 1 duration + gap
        assert timeline[2] > 0
        # seq 3 (background) gets current cursor position but doesn't advance it
        assert timeline[3] > timeline[2]
        # Total equals the cursor after all fg stems (bg doesn't advance)
        assert total_ms == timeline[3]

    def test_empty_plans(self):
        total_ms, timeline = build_foreground_timeline_only([], gap_ms=600)
        assert total_ms == 0
        assert timeline == {}


class TestComputeDialogueLabels:
    def test_returns_dialogue_only(self, tmp_path):
        p1 = tmp_path / "001_act-one_adam.mp3"
        p2 = tmp_path / "002_act-one_sfx.mp3"
        _write_mp3(p1, 1000)
        _write_mp3(p2, 500)

        plans = [
            StemPlan(seq=1, filepath=str(p1), direction_type=None, entry_type="dialogue"),
            StemPlan(seq=2, filepath=str(p2), direction_type="SFX", entry_type="direction"),
        ]
        timeline = {1: 0, 2: 1600}

        labels = compute_dialogue_labels(plans, timeline)
        assert len(labels) == 1
        assert labels[0][2] == "adam"


class TestComputeAmbienceLabels:
    def test_ambience_region_boundaries(self, tmp_path):
        p_amb = tmp_path / "003_act-one_sfx.mp3"
        _write_mp3(p_amb, 2000)

        plans = [
            StemPlan(seq=3, filepath=str(p_amb), direction_type="AMBIENCE", entry_type="direction"),
        ]
        timeline = {3: 5000}
        total_ms = 20000

        labels = compute_ambience_labels(plans, timeline, total_ms)
        assert len(labels) == 1
        assert labels[0][0] == pytest.approx(5.0)
        assert labels[0][1] == pytest.approx(20.0)  # extends to end


class TestComputeMusicLabels:
    def test_music_uses_actual_duration(self, tmp_path):
        p = tmp_path / "005_act-one_sfx.mp3"
        _write_mp3(p, 3000)

        plans = [
            StemPlan(seq=5, filepath=str(p), direction_type="MUSIC", entry_type="direction",
                     text="THEME STING"),
        ]
        timeline = {5: 10000}
        total_ms = 30000

        labels = compute_music_labels(plans, timeline, total_ms)
        assert len(labels) == 1
        assert labels[0][0] == pytest.approx(10.0)
        # End should be start + actual duration (~3s)
        assert labels[0][1] == pytest.approx(13.0, abs=0.5)
        assert labels[0][2] == "THEME STING"


class TestComputeSfxLabels:
    def test_sfx_and_beat(self, tmp_path):
        p1 = tmp_path / "010_act-one_sfx.mp3"
        p2 = tmp_path / "011_act-one_sfx.mp3"
        _write_mp3(p1, 1000)
        _write_mp3(p2, 500)

        plans = [
            StemPlan(seq=10, filepath=str(p1), direction_type="SFX", entry_type="direction",
                     text="SFX: DOOR SLAM"),
            StemPlan(seq=11, filepath=str(p2), direction_type="BEAT", entry_type="direction",
                     text="BEAT"),
        ]
        timeline = {10: 5000, 11: 8000}
        total_ms = 20000

        labels = compute_sfx_labels(plans, timeline, total_ms)
        assert len(labels) == 2
        assert labels[0][2] == "SFX: DOOR SLAM"
        assert labels[1][2] == "BEAT"


# ─── Tests: ramp indicators ───

class TestLayerSpanRampFields:
    def test_ramp_fields_default_none(self):
        span = LayerSpan(start_s=0.0, end_s=5.0, label="test")
        assert span.ramp_in_s is None
        assert span.ramp_out_s is None

    def test_ramp_fields_set(self):
        span = LayerSpan(start_s=0.0, end_s=5.0, label="test",
                         ramp_in_s=1.0, ramp_out_s=2.0)
        assert span.ramp_in_s == 1.0
        assert span.ramp_out_s == 2.0


class TestBuildTimelineDataRampTuples:
    def test_five_tuple_ambience_label_populates_ramp_fields(self):
        """5-tuple labels should set ramp_in_s and ramp_out_s on the span."""
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[],
            amb_labels=[(0.0, 30.0, "AMBIENT RAIN", 1.0, 2.0)],
            mus_labels=[],
            sfx_labels=[],
        )
        span = td.layers["ambience"][0]
        assert span.ramp_in_s == 1.0
        assert span.ramp_out_s == 2.0

    def test_five_tuple_music_label_populates_ramp_fields(self):
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[],
            amb_labels=[],
            mus_labels=[(10.0, 30.0, "THEME", 0.5, 1.5)],
            sfx_labels=[],
        )
        span = td.layers["music"][0]
        assert span.ramp_in_s == 0.5
        assert span.ramp_out_s == 1.5

    def test_three_tuple_label_gives_none_ramp(self):
        """Legacy 3-tuples (dialogue, SFX) should produce None ramp fields."""
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[(0.0, 5.0, "adam")],
            amb_labels=[],
            mus_labels=[],
            sfx_labels=[],
        )
        span = td.layers["dialogue"][0]
        assert span.ramp_in_s is None
        assert span.ramp_out_s is None

    def test_five_tuple_none_ramps_preserved(self):
        """5-tuples with None ramp values should produce None fields."""
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[],
            amb_labels=[(0.0, 30.0, "RAIN", None, None)],
            mus_labels=[],
            sfx_labels=[],
        )
        span = td.layers["ambience"][0]
        assert span.ramp_in_s is None
        assert span.ramp_out_s is None


class TestHtmlTimelineRampIndicators:
    def test_ramp_badges_present_when_ramp_set(self, tmp_path):
        """↑ and ↓ badges appear in HTML when a span has ramp data."""
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[],
            amb_labels=[(0.0, 30.0, "RAIN", 1.0, 2.0)],
            mus_labels=[],
            sfx_labels=[],
        )
        out_path = str(tmp_path / "tl.html")
        render_html_timeline(td, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "ramp_in_s" in content
        assert "ramp_out_s" in content
        assert "ramp-badge" in content

    def test_ramp_json_in_html(self, tmp_path):
        """Ramp values should appear in the embedded JSON data."""
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[],
            amb_labels=[],
            mus_labels=[(5.0, 15.0, "THEME", 0.5, 1.5)],
            sfx_labels=[],
        )
        out_path = str(tmp_path / "tl.html")
        render_html_timeline(td, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert '"ramp_in_s": 0.5' in content
        assert '"ramp_out_s": 1.5' in content

    def test_play_duration_badge_in_html(self, tmp_path):
        """`%` badge appears in HTML when a music span has play_duration set."""
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[],
            amb_labels=[],
            mus_labels=[(5.0, 12.0, "THEME", None, None, 60.0)],
            sfx_labels=[],
        )
        out_path = str(tmp_path / "tl.html")
        render_html_timeline(td, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert '"play_duration": 60.0' in content
        assert 'ramp-badge pd' in content

    def test_play_duration_none_serialized_as_null(self, tmp_path):
        """play_duration=None serializes as JSON null (JS skips the badge)."""
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[],
            amb_labels=[],
            mus_labels=[(5.0, 15.0, "THEME", None, None, None)],
            sfx_labels=[],
        )
        out_path = str(tmp_path / "tl.html")
        render_html_timeline(td, out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert '"play_duration": null' in content

    def test_six_tuple_populates_play_duration_field(self):
        """6-tuple music label should set play_duration on the LayerSpan."""
        td = build_timeline_data(
            "TEST", 60.0,
            dlg_labels=[],
            amb_labels=[],
            mus_labels=[(5.0, 15.0, "THEME", 1.0, 2.0, 75.0)],
            sfx_labels=[],
        )
        span = td.layers["music"][0]
        assert span.play_duration == 75.0
        assert span.ramp_in_s == 1.0


class TestComputeMusicLabelsPlayDuration:
    def test_play_duration_shortens_label_end(self, tmp_path):
        """play_duration=50 should halve the duration in the label end time."""
        p = tmp_path / "005_act-one_sfx.mp3"
        _write_mp3(p, 2000)

        plan = StemPlan(seq=5, filepath=str(p), direction_type="MUSIC",
                        entry_type="direction", text="THEME")
        plan.play_duration = 50.0
        timeline = {5: 0}

        labels = compute_music_labels([plan], timeline, 10000)
        start_s, end_s, *_ = labels[0]
        # 50% of ~2s clip ≈ 1s; end should be < 1.5s
        assert (end_s - start_s) < 1.5

    def test_play_duration_none_uses_full_duration(self, tmp_path):
        p = tmp_path / "005_act-one_sfx.mp3"
        _write_mp3(p, 2000)

        plan = StemPlan(seq=5, filepath=str(p), direction_type="MUSIC",
                        entry_type="direction", text="THEME")
        plan.play_duration = None
        timeline = {5: 0}

        labels = compute_music_labels([plan], timeline, 10000)
        start_s, end_s, *_ = labels[0]
        # Full ~2s clip
        assert (end_s - start_s) > 1.5

    def test_music_labels_include_ramp_and_play_duration(self, tmp_path):
        """compute_music_labels should return 8-tuples with ramp, play_duration, and volume data."""
        p = tmp_path / "005_act-one_sfx.mp3"
        _write_mp3(p, 1000)

        plan = StemPlan(seq=5, filepath=str(p), direction_type="MUSIC",
                        entry_type="direction", text="THEME")
        plan.ramp_in_seconds = 0.5
        plan.ramp_out_seconds = 1.0
        plan.play_duration = 60.0
        timeline = {5: 0}

        labels = compute_music_labels([plan], timeline, 10000)
        assert len(labels[0]) == 9
        assert labels[0][3] == 0.5
        assert labels[0][4] == 1.0
        assert labels[0][5] == 60.0
        assert labels[0][7] is None  # volume_pct (not set)
        assert labels[0][8] == 5     # seq


class TestVolumePercentageInTimeline:
    def test_volume_pct_populates_from_8_tuple(self):
        """An 8-tuple label should populate volume_pct on the LayerSpan."""
        data = build_timeline_data(
            tag="TEST", total_s=10.0,
            dlg_labels=[], amb_labels=[],
            mus_labels=[(0.0, 5.0, "MUSIC: THEME", 0.5, 1.0, 75.0, None, 60.0)],
            sfx_labels=[(0.0, 1.0, "SFX: BANG", None, None, None, None, 30.0)],
        )
        assert data.layers["music"][0].volume_pct == 60.0
        assert data.layers["sfx"][0].volume_pct == 30.0

    def test_volume_pct_none_when_absent(self):
        """A 3-tuple label should give volume_pct=None."""
        data = build_timeline_data(
            tag="TEST", total_s=10.0,
            dlg_labels=[(0.0, 2.0, "ADAM")],
            amb_labels=[], mus_labels=[], sfx_labels=[],
        )
        assert data.layers["dialogue"][0].volume_pct is None

    def test_volume_pct_in_html_json(self, tmp_path):
        """volume_pct should appear in the embedded JSON data."""
        data = build_timeline_data(
            tag="TEST", total_s=10.0,
            dlg_labels=[], amb_labels=[],
            mus_labels=[(0.0, 5.0, "THEME", None, None, None, None, 80.0)],
            sfx_labels=[],
        )
        out = str(tmp_path / "test.html")
        render_html_timeline(data, out)
        html = open(out).read()
        assert '"volume_pct": 80.0' in html

    def test_volume_pct_tooltip_text_in_html(self, tmp_path):
        """The JS tooltip code should reference volume_pct."""
        data = build_timeline_data(
            tag="TEST", total_s=10.0,
            dlg_labels=[], amb_labels=[], mus_labels=[], sfx_labels=[],
        )
        out = str(tmp_path / "test.html")
        render_html_timeline(data, out)
        html = open(out).read()
        assert "volume_pct" in html
        assert "vol:" in html
