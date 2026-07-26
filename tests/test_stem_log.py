"""Tests for XILU008 stem log report."""

import csv
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from xil_pipeline.XILU008_stem_log_report import _parse_log, get_parser, main

# ── fixtures ─────────────────────────────────────────────────────────────────

ELEVEN_LOG = """\
--- Phase 1: Generating ---
  > [006] adam with eleven_v3 (282 chars)...
  Saved: stems/the413/S03E03/006_cold-open_adam.mp3
  SHA256: abc123def456

  > [007] sarah with eleven_v3 (150 chars)...
  Saved: stems/the413/S03E03/007_cold-open_sarah.mp3
  SHA256: 789abcdef012
"""

MULTI_EPISODE_LOG = """\
--- Phase 1: Generating ---
  > [001] adam with eleven_v3 (100 chars)...
  Saved: stems/the413/S03E03/001_cold-open_adam.mp3
  SHA256: aaaa1111

  > [002] maya with eleven_v3 (200 chars)...
  Saved: stems/the413/S04E01/002_cold-open_maya.mp3
  SHA256: bbbb2222
"""

GTTS_LOG = """\
--- Phase 1: Generating ---
  > [005] maya via gTTS (100 chars)...
  Saved: stems/the413/S03E03/005_act-one_maya.mp3
  SHA256: 9900aa99bb00cc11
"""

CHATTERBOX_LOG = """\
--- Phase 1: Generating ---
  > [003] tina via Chatterbox (80 chars)...
  Saved: stems/the413/S03E03/003_cold-open_tina.mp3
  SHA256: cbcbcbcbcbcbcbcb
"""

CHATTERBOX_TURBO_LOG = """\
--- Phase 1: Generating ---
  > [004] tina via Chatterbox Turbo (90 chars)...
  Saved: stems/the413/S03E03/004_cold-open_tina.mp3
  SHA256: dededededededede
"""


# v2 structured log: same events, but every line carries "ts|LEVEL|stage|" and
# the run boundary is run_banner's RUN BEGIN record instead of "--- Phase 1".
V2_LOG = """\
2026-04-01T09:00:00-0400|RUN|produce|BEGIN argv="xil produce --episode S03E03" pid=123 ver=0.3.1 cwd=/ws
2026-04-01T09:00:01-0400|INFO|produce|--- Phase 1: Generating ---
2026-04-01T09:00:02-0400|INFO|produce|  > [006] adam with eleven_v3 (282 chars)...
2026-04-01T09:00:05-0400|INFO|produce|  Saved: stems/the413/S03E03/006_cold-open_adam.mp3
2026-04-01T09:00:05-0400|INFO|produce|  SHA256: abc123def456
2026-04-01T09:00:06-0400|INFO|produce|  > [007] sarah with eleven_v3 (150 chars)...
2026-04-01T09:00:09-0400|INFO|produce|  Saved: stems/the413/S03E03/007_cold-open_sarah.mp3
2026-04-01T09:00:09-0400|INFO|produce|  SHA256: 789abcdef012
2026-04-01T09:00:10-0400|RUN|produce|END elapsed=10.0s
"""

# A v1 table line legitimately contains pipes — it must NOT be mistaken for a
# v2 prefix and mangled.
V1_WITH_PIPES_LOG = """\
--- Phase 1: Generating ---
  003 | preamble         | tina
  > [003] tina via Chatterbox Turbo (80 chars)...
  Saved: stems/the413/S03E03/003_cold-open_tina.mp3
  SHA256: cbcbcbcbcbcbcbcb
"""


def _write_log(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ── _parse_log ────────────────────────────────────────────────────────────────

def test_parse_elevenlabs_entries(tmp_path):
    lf = _write_log(tmp_path, "xil_2026-04-01.log", ELEVEN_LOG)
    records = _parse_log(lf)
    assert len(records) == 2
    r = records[0]
    assert r["seq"] == 6
    assert r["speaker"] == "adam"
    assert r["backend"] == "eleven_v3"
    assert r["char_count"] == 282
    assert r["sha256"] == "abc123def456"
    assert "S03E03" in r["stem_path"]
    assert r["log_date"] == "2026-04-01"


def test_parse_gtts_entry(tmp_path):
    lf = _write_log(tmp_path, "xil_2026-04-02.log", GTTS_LOG)
    records = _parse_log(lf)
    assert len(records) == 1
    assert records[0]["backend"] == "gtts"
    assert records[0]["char_count"] == 100


def test_parse_chatterbox_entry(tmp_path):
    lf = _write_log(tmp_path, "xil_2026-04-03.log", CHATTERBOX_LOG)
    records = _parse_log(lf)
    assert len(records) == 1
    assert records[0]["backend"] == "chatterbox"


def test_parse_chatterbox_turbo_entry(tmp_path):
    lf = _write_log(tmp_path, "xil_2026-04-05.log", CHATTERBOX_TURBO_LOG)
    records = _parse_log(lf)
    assert len(records) == 1
    assert records[0]["backend"] == "chatterbox-turbo"
    assert records[0]["char_count"] == 90


def test_run_index_increments(tmp_path):
    # GTTS_LOG already has its own Phase 1 header; splice it directly
    content = ELEVEN_LOG + GTTS_LOG
    lf = _write_log(tmp_path, "xil_2026-04-04.log", content)
    records = _parse_log(lf)
    assert len(records) == 3
    assert records[0]["run_index"] == 1
    assert records[1]["run_index"] == 1
    assert records[2]["run_index"] == 2


# ── --episode filter ──────────────────────────────────────────────────────────

def test_episode_filter(tmp_path):
    _write_log(tmp_path, "xil_2026-04-05.log", MULTI_EPISODE_LOG)
    args = get_parser().parse_args(["--logs-dir", str(tmp_path), "--episode", "S03E03", "--output", "-"])
    assert args.episode == "S03E03"

    captured = io.StringIO()
    with patch.object(sys, "stdout", captured):
        with patch("sys.argv", ["xil-stem-log", "--logs-dir", str(tmp_path), "--episode", "S03E03", "--output", "-"]):
            main()

    output = captured.getvalue()
    reader = csv.DictReader(io.StringIO(output))
    rows = list(reader)
    assert all("S03E03" in r["stem_path"] for r in rows)
    assert len(rows) == 1


def test_episode_filter_tag_alias(tmp_path):
    _write_log(tmp_path, "xil_2026-04-06.log", MULTI_EPISODE_LOG)
    args = get_parser().parse_args(["--tag", "S04E01", "--logs-dir", str(tmp_path)])
    assert args.episode == "S04E01"


# ── --slug filter ─────────────────────────────────────────────────────────────

def test_slug_filter(tmp_path):
    content = MULTI_EPISODE_LOG + """\
  > [003] host with eleven_v3 (50 chars)...
  Saved: stems/nightowls/S01E01/003_cold-open_host.mp3
  SHA256: 00f1ee2dd3cc4bb5
"""
    _write_log(tmp_path, "xil_2026-04-07.log", content)

    with patch("sys.argv", ["xil-stem-log", "--logs-dir", str(tmp_path), "--slug", "the413", "--output", "-"]):
        captured = io.StringIO()
        with patch.object(sys, "stdout", captured):
            main()

    rows = list(csv.DictReader(io.StringIO(captured.getvalue())))
    assert all("the413" in r["stem_path"] for r in rows)
    assert len(rows) == 2


# ── --since filter ────────────────────────────────────────────────────────────

def test_since_filter(tmp_path):
    _write_log(tmp_path, "xil_2026-03-01.log", ELEVEN_LOG)
    _write_log(tmp_path, "xil_2026-04-10.log", GTTS_LOG)

    with patch("sys.argv", ["xil-stem-log", "--logs-dir", str(tmp_path), "--since", "2026-04-01", "--output", "-"]):
        captured = io.StringIO()
        with patch.object(sys, "stdout", captured):
            main()

    rows = list(csv.DictReader(io.StringIO(captured.getvalue())))
    assert all(r["log_date"] >= "2026-04-01" for r in rows)
    assert len(rows) == 1


# ── --show stdout flag ────────────────────────────────────────────────────────

def test_show_flag_prints_to_stdout(tmp_path):
    _write_log(tmp_path, "xil_2026-04-11.log", ELEVEN_LOG)

    with patch("sys.argv", ["xil-stem-log", "--logs-dir", str(tmp_path), "--show"]):
        captured = io.StringIO()
        with patch.object(sys, "stdout", captured):
            main()

    assert "log_date" in captured.getvalue()


# ── output CSV ────────────────────────────────────────────────────────────────

def test_output_csv_written(tmp_path):
    _write_log(tmp_path, "xil_2026-04-12.log", ELEVEN_LOG)
    out = tmp_path / "report.csv"

    with patch("sys.argv", ["xil-stem-log", "--logs-dir", str(tmp_path), "--output", str(out)]):
        main()

    assert out.exists()
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 2
    assert rows[0]["sha256"] == "abc123def456"


# ── no matching logs ──────────────────────────────────────────────────────────

def test_no_log_files_exits_zero(tmp_path):
    with patch("sys.argv", ["xil-stem-log", "--logs-dir", str(tmp_path)]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0


def test_missing_logs_dir_exits_nonzero(tmp_path):
    with patch("sys.argv", ["xil-stem-log", "--logs-dir", str(tmp_path / "nope")]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code != 0


class TestV2StructuredLogs:
    """v2 logs (ts|LEVEL|stage|msg) parse identically to their v1 equivalents."""

    def test_v2_parses_same_records_as_v1(self, tmp_path):
        v1 = _parse_log(_write_log(tmp_path, "xil_v1_2026-04-01.log", ELEVEN_LOG))
        v2 = _parse_log(_write_log(tmp_path, "xil_v2_2026-04-01.log", V2_LOG))
        fields = ("seq", "speaker", "backend", "char_count", "stem_filename", "sha256")
        assert [{f: r[f] for f in fields} for r in v1] == [{f: r[f] for f in fields} for r in v2]

    def test_v2_run_boundary_counted_once(self, tmp_path):
        """RUN BEGIN is the boundary; the Phase 1 line must not double-count it."""
        records = _parse_log(_write_log(tmp_path, "xil_v2_2026-04-01.log", V2_LOG))
        assert {r["run_index"] for r in records} == {1}

    def test_v1_line_containing_pipes_is_not_stripped(self, tmp_path):
        """A v1 table row with '|' must not be mistaken for a v2 prefix."""
        records = _parse_log(
            _write_log(tmp_path, "xil_v1_2026-04-02.log", V1_WITH_PIPES_LOG)
        )
        assert len(records) == 1
        assert records[0]["speaker"] == "tina"
        assert records[0]["backend"] == "chatterbox-turbo"

    def test_versioned_filenames_still_yield_dates(self, tmp_path):
        for name in ("xil_v1_2026-04-01.log", "xil_v2_2026-04-01.log"):
            records = _parse_log(_write_log(tmp_path, name, V2_LOG if "v2" in name else ELEVEN_LOG))
            assert records and records[0]["log_date"] == "2026-04-01"
