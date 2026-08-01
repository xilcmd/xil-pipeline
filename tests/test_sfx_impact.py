# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU021_sfx_impact.py — SFX source-clipping impact analysis.

The tier arithmetic must mirror ``mix_common.collect_stem_plans`` exactly; if
these tests and the mixer ever disagree, the report is lying about what the
audience hears.
"""

import csv
import io
import json

import pytest

from xil_pipeline.XILU021_sfx_impact import (
    CSV_COLUMNS,
    CueImpact,
    ImpactReport,
    analyze,
    classify_placement,
    discover_configs,
    measure_cue,
    render_html,
    write_csv,
)


# A stub probe keeps the tests off the filesystem and off ffmpeg: any path
# ending in "<n>s.mp3" reports n seconds.
def _probe(path: str) -> float:
    name = str(path).rsplit("/", 1)[-1]
    if "missing" in name:
        raise FileNotFoundError(path)
    return float(name.split("s.mp3")[0].split("_")[-1]) * 1000.0


def _measure(cue="SFX: THING", **effect):
    effect.setdefault("source", "SFX/x_10s.mp3")
    return measure_cue("myshow", "S01E01", cue, effect, _probe, __import__("pathlib").Path("/ws"))


# ── placement classification ──────────────────────────────────────────────────

class TestClassifyPlacement:
    @pytest.mark.parametrize("cue,expected", [
        ("INTRO MUSIC", "MUSIC(bg)"),
        ("OUTRO MUSIC", "MUSIC(bg)"),
        ("MUSIC: STING OUT", "MUSIC(bg)"),
        ("AMBIENCE: DINER", "AMBI(bg)"),
        ("BEAT", "BEAT(fg)"),
        ("BEAT — 3 SECONDS", "BEAT(fg)"),
        ("SFX: DOOR OPENS", "SFX(fg)"),
    ])
    def test_placement(self, cue, expected):
        assert classify_placement(cue) == expected


# ── the precedence chain (mirrors mix_common) ─────────────────────────────────

class TestMeasurePrecedence:
    def test_no_source_is_not_measured(self):
        """Generated and silence cues are out of scope."""
        assert measure_cue("s", "t", "BEAT", {"type": "silence"}, _probe, None) is None

    def test_looped_bed_is_excluded(self):
        i = _measure(source="SFX/a_10s.mp3", duration_seconds=30.0, loop=True)
        assert i.tier == "EXCLUDED"
        assert i.plays_now_s == 10.0
        assert i.delta_s == 0.0
        assert "looped bed" in i.note

    def test_explicit_play_duration_wins_over_duration_seconds(self):
        """play_duration takes precedence — this is the mixer's rule."""
        i = _measure(source="SFX/a_10s.mp3", duration_seconds=5.0, play_duration=100)
        assert i.tier == "EXCLUDED"
        assert i.plays_now_s == pytest.approx(10.0)
        assert "play_duration" in i.note

    def test_partial_play_duration_is_still_excluded(self):
        """A deliberate trim is not an accident, however short."""
        i = _measure(source="SFX/a_10s.mp3", duration_seconds=5.0, play_duration=25)
        assert i.tier == "EXCLUDED"
        assert i.plays_now_s == pytest.approx(2.5)

    def test_duration_seconds_clips(self):
        i = _measure(source="SFX/a_30s.mp3", duration_seconds=5.0)
        assert i.plays_now_s == 5.0
        assert i.delta_s == pytest.approx(25.0)
        assert i.tier == "3-review"

    def test_zero_duration_plays_full(self):
        i = _measure(source="SFX/a_30s.mp3", duration_seconds=0)
        assert i.tier == "EXCLUDED"
        assert i.plays_now_s == 30.0
        assert i.delta_s == 0.0

    def test_absent_duration_plays_full(self):
        i = _measure(source="SFX/a_30s.mp3")
        assert i.tier == "EXCLUDED"
        assert i.plays_now_s == 30.0

    def test_duration_longer_than_file_is_not_negative(self):
        """A 30s budget on a 10s file plays 10s, and loses nothing."""
        i = _measure(source="SFX/a_10s.mp3", duration_seconds=30.0)
        assert i.plays_now_s == 10.0
        assert i.delta_s == 0.0
        assert i.tier == "1-nochange"

    def test_unreadable_source_is_missing(self):
        i = _measure(source="SFX/missing.mp3", duration_seconds=5.0)
        assert i.tier == "MISSING"
        assert i.natural_s is None
        assert "unreadable" in i.note


class TestTierThresholds:
    @pytest.mark.parametrize("natural,expected", [
        (5.05, "1-nochange"),    # 0.05s lost
        (6.0, "2-minor"),        # 1.0s lost
        (7.9, "2-minor"),        # 2.9s lost
        (8.1, "3-review"),       # 3.1s lost
        (30.0, "3-review"),
    ])
    def test_boundaries(self, natural, expected):
        i = _measure(source=f"SFX/a_{natural}s.mp3", duration_seconds=5.0)
        assert i.tier == expected

    def test_lost_pct(self):
        i = _measure(source="SFX/a_20s.mp3", duration_seconds=5.0)
        assert i.lost_pct == pytest.approx(75.0)


class TestRemediation:
    def test_impacted_cue_gets_a_concrete_edit(self):
        i = _measure(source="SFX/a_30s.mp3", duration_seconds=5.0)
        assert i.remediation == "play_duration: 100"

    def test_remediation_field_is_journaled(self):
        """The whole point: the recommended fix must survive a skeleton rebuild.

        ``duration_seconds`` is NOT in SFX_EDIT_FIELDS, so an edit to it reverts
        to the parser's 5.0 default on the next regeneration.  Recommending it
        would hand the creative a decision that silently undoes itself.
        """
        from xil_pipeline.sfx_common import SFX_EDIT_FIELDS

        i = _measure(source="SFX/a_30s.mp3", duration_seconds=5.0)
        field = i.remediation.split(":")[0].strip()
        assert field in SFX_EDIT_FIELDS, (
            f"remediation targets {field!r}, which the edit journal does not "
            f"replay (journaled fields: {SFX_EDIT_FIELDS})"
        )

    def test_recommended_fix_actually_un_clips(self):
        """Applying the remediation must move the cue out of an impacted tier."""
        effect = {"source": "SFX/a_30s.mp3", "duration_seconds": 5.0}
        assert _measure(**effect).tier == "3-review"

        effect["play_duration"] = 100          # apply the recommendation
        fixed = _measure(**effect)
        assert fixed.tier == "EXCLUDED"
        assert fixed.plays_now_s == pytest.approx(30.0)
        assert fixed.delta_s == 0.0

    @pytest.mark.parametrize("kwargs", [
        {"source": "SFX/a_10s.mp3", "duration_seconds": 30.0},   # 1-nochange
        {"source": "SFX/a_10s.mp3", "loop": True},               # EXCLUDED
        {"source": "SFX/missing.mp3"},                           # MISSING
    ])
    def test_unimpacted_cues_get_none(self, kwargs):
        assert _measure(**kwargs).remediation == ""


# ── discovery ─────────────────────────────────────────────────────────────────

def _workspace(tmp_path, layout):
    for slug, episodes in layout.items():
        d = tmp_path / "configs" / slug
        d.mkdir(parents=True)
        for tag, effects in episodes.items():
            (d / f"sfx_{tag}.json").write_text(json.dumps({"effects": effects}), encoding="utf-8")
    return tmp_path


class TestDiscovery:
    @pytest.fixture
    def ws(self, tmp_path):
        return _workspace(tmp_path, {
            "showa": {"S01E01": {}, "S01E02": {}},
            "showb": {"S01E01": {}},
        })

    def test_sweeps_every_show(self, ws):
        found = discover_configs(ws)
        assert [(s, t) for s, t, _ in found] == [
            ("showa", "S01E01"), ("showa", "S01E02"), ("showb", "S01E01")]

    def test_show_filter(self, ws):
        assert {s for s, _, _ in discover_configs(ws, show="showb")} == {"showb"}

    def test_episode_filter(self, ws):
        found = discover_configs(ws, episode="S01E02")
        assert [(s, t) for s, t, _ in found] == [("showa", "S01E02")]

    def test_missing_configs_dir_is_empty(self, tmp_path):
        assert discover_configs(tmp_path) == []

    def test_edit_journals_are_not_configs(self, ws):
        (ws / "configs" / "showa" / "sfx_S01E01_edits.jsonl").write_text("{}", encoding="utf-8")
        assert len(discover_configs(ws)) == 3


# ── end-to-end sweep ──────────────────────────────────────────────────────────

class TestAnalyze:
    @pytest.fixture
    def ws(self, tmp_path):
        return _workspace(tmp_path, {"showa": {"S01E01": {
            "OUTRO MUSIC": {"source": "SFX/a_30s.mp3", "duration_seconds": 5.0},
            "AMBIENCE: RIVER": {"source": "SFX/b_10s.mp3", "duration_seconds": 30.0, "loop": True},
            "MUSIC: STING": {"prompt": "a sting", "duration_seconds": 15.0},
            "BEAT": {"type": "silence", "duration_seconds": 1.0},
        }}})

    def test_only_source_cues_are_reported(self, ws):
        report = analyze(ws, duration_fn=_probe)
        assert report.configs_scanned == 1
        assert {i.cue for i in report.impacts} == {"OUTRO MUSIC", "AMBIENCE: RIVER"}

    def test_tally_and_actionable(self, ws):
        report = analyze(ws, duration_fn=_probe)
        assert report.tally()["3-review"] == 1
        assert report.tally()["EXCLUDED"] == 1
        assert [i.cue for i in report.actionable] == ["OUTRO MUSIC"]

    def test_by_show(self, ws):
        assert list(analyze(ws, duration_fn=_probe).by_show()) == ["showa"]

    def test_malformed_config_is_skipped_not_fatal(self, ws):
        (ws / "configs" / "showa" / "sfx_S01E09.json").write_text("{not json", encoding="utf-8")
        report = analyze(ws, duration_fn=_probe)
        assert report.configs_scanned == 1        # the broken one did not count
        assert len(report.impacts) == 2


# ── output surfaces ───────────────────────────────────────────────────────────

class TestOutputs:
    @pytest.fixture
    def report(self):
        return ImpactReport(impacts=[
            CueImpact(show="s", episode="S01E01", cue='OUTRO, "MUSIC"',
                      source_file="a.mp3", duration_seconds=5.0, natural_s=30.0,
                      plays_now_s=5.0, delta_s=25.0, lost_pct=83.3, tier="3-review",
                      remediation="play_duration: 100"),
        ], configs_scanned=1)

    def test_csv_columns_and_quoting(self, report):
        buf = io.StringIO()
        write_csv(report, buf)
        rows = list(csv.DictReader(io.StringIO(buf.getvalue())))
        assert list(rows[0]) == CSV_COLUMNS
        assert rows[0]["cue"] == 'OUTRO, "MUSIC"'      # commas/quotes survive
        assert rows[0]["delta_s"] == "25.0"

    def test_csv_blanks_for_none(self):
        report = ImpactReport(impacts=[
            CueImpact(show="s", episode="e", cue="c", source_file="m.mp3", tier="MISSING")])
        buf = io.StringIO()
        write_csv(report, buf)
        assert list(csv.DictReader(io.StringIO(buf.getvalue())))[0]["natural_s"] == ""

    def test_html_is_self_contained(self, report):
        out = render_html(report, "myshow")
        assert out.startswith("<!doctype html>")
        assert "<style>" in out
        # No external requests — the page must open from a file share as-is.
        for token in ("http://", "https://", "<script"):
            assert token not in out

    def test_html_escapes_cue_text(self, report):
        report.impacts[0].cue = '<script>alert(1)</script>'
        out = render_html(report, "myshow")
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;" in out

    def test_html_reports_counts(self, report):
        out = render_html(report, "myshow")
        assert "1 source-backed cue(s)" in out
        assert "myshow" in out


class TestStdoutStreaming:
    """``--output -`` makes stdout the CSV stream; nothing else may touch it."""

    def test_banner_is_skipped_when_streaming(self, tmp_path, monkeypatch, capsys):
        from xil_pipeline import XILU021_sfx_impact as mod

        _workspace(tmp_path, {"showa": {"S01E01": {
            "OUTRO MUSIC": {"source": "SFX/a_30s.mp3", "duration_seconds": 5.0},
        }}})
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        monkeypatch.setattr(mod, "analyze",
                            lambda *a, **k: analyze(tmp_path, duration_fn=_probe))
        monkeypatch.setattr("sys.argv",
                            ["xil-sfx-impact", "--output", "-", "--quiet"])
        mod.main()

        out = capsys.readouterr().out
        # First line must be the CSV header — no banner, no log lines.
        assert out.splitlines()[0] == ",".join(CSV_COLUMNS)
        assert "=====" not in out
        rows = list(csv.DictReader(io.StringIO(out)))
        assert len(rows) == 1
        assert rows[0]["tier"] == "3-review"
