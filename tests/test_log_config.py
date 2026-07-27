# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the v2 structured log file format.

The console (stdout) format is deliberately unchanged; only the log FILE became
machine-readable. These tests pin both halves of that contract.
"""

import logging
import re
import sys

import pytest

from xil_pipeline import log_config
from xil_pipeline.log_config import _HOST, RUN, _host, _stage_from_argv, configure_logging

# ts|LEVEL|host|stage|message
V2_LINE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}\|[A-Z]+\|[^|]*\|[^|]*\|")


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    """Point configure_logging at an isolated workspace, then restore handlers."""
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    root = logging.getLogger()
    saved = root.handlers[:]
    yield tmp_path / "logs"
    for h in root.handlers[:]:
        if h not in saved:
            h.close()
    root.handlers.clear()
    root.handlers.extend(saved)


def _configure(monkeypatch, argv0="xil-produce", *argv):
    """Install xil handlers for a test.

    ``configure_logging`` only installs handlers when the root logger has none,
    and pytest's logging plugin attaches its own around every test phase — so
    clear them first or the file handler is never created.
    """
    monkeypatch.setattr(sys, "argv", [argv0, *argv])
    logging.getLogger().handlers.clear()
    configure_logging()


def _read_log(logdir):
    files = list(logdir.glob("xil_v2_*.log"))
    assert files, f"no v2 log written into {logdir}"
    return files[0].read_text(encoding="utf-8").splitlines()


class TestStageFromArgv:
    """Stage is derived from argv[0] for both entry-point shapes."""

    @pytest.mark.parametrize("argv0,expected", [
        ("xil-produce", "produce"),
        ("xil produce", "produce"),          # dispatcher rewrites argv[0]
        ("/usr/bin/xil-daw", "daw"),
        ("xil", "xil"),
        ("xil-stem-log", "stem-log"),
    ])
    def test_derivation(self, argv0, expected, monkeypatch):
        monkeypatch.setattr(sys, "argv", [argv0])
        assert _stage_from_argv() == expected


class TestStructuredFileFormat:
    def test_every_line_is_structured(self, logdir, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        log = logging.getLogger("x")
        log.info("  > [006] adam with eleven_v3 (282 chars)...")
        log.warning("no voice ref")
        for line in _read_log(logdir):
            assert V2_LINE.match(line), f"unstructured line: {line!r}"

    def test_fields_and_stage(self, logdir, monkeypatch):
        _configure(monkeypatch, "xil produce")
        logging.getLogger("x").info("hello")
        ts, level, host, stage, msg = _read_log(logdir)[0].split("|", 4)
        assert level == "INFO"
        assert host == _HOST
        assert stage == "produce"
        assert msg == "hello"

    def test_message_may_contain_pipes(self, logdir, monkeypatch):
        """Only the three leading fields are the prefix; the message keeps its pipes."""
        _configure(monkeypatch, "xil-produce")
        logging.getLogger("x").info("  003 | preamble | tina")
        *_, msg = _read_log(logdir)[0].split("|", 4)
        assert msg == "  003 | preamble | tina"

    def test_blank_records_dropped_from_file(self, logdir, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        log = logging.getLogger("x")
        log.info("")
        log.info("   ")
        log.info("real")
        lines = _read_log(logdir)
        assert len(lines) == 1
        assert lines[0].endswith("|real")

    def test_multiline_message_prefixes_each_line(self, logdir, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        logging.getLogger("x").info("one\ntwo")
        lines = _read_log(logdir)
        assert len(lines) == 2
        assert all(V2_LINE.match(line) for line in lines)


class TestConsoleUnchanged:
    """The stdout format must not change — only the file became structured."""

    def test_console_has_no_prefix(self, logdir, capsys, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        log = logging.getLogger("x")
        log.info("plain message")
        log.warning("careful")
        out = capsys.readouterr().out
        assert "plain message" in out
        assert "[!] careful" in out
        assert "|INFO|" not in out          # structure stays in the file
        assert not V2_LINE.match(out.splitlines()[0])

    def test_run_level_renders_bare_on_console(self, logdir, capsys, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        logging.getLogger("x").log(RUN, "=" * 10)
        assert capsys.readouterr().out.strip() == "=" * 10


class TestSinkRouting:
    """extra={"console": False} / {"file": False} route a record to one sink."""

    def test_file_only_record_skips_console(self, logdir, capsys, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        logging.getLogger("x").log(RUN, "BEGIN argv=\"xil produce\"", extra={"console": False})
        assert "BEGIN" not in capsys.readouterr().out
        assert any("|RUN|" in line and "BEGIN" in line for line in _read_log(logdir))

    def test_console_only_record_skips_file(self, logdir, capsys, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        log = logging.getLogger("x")
        log.log(RUN, "=" * 10, extra={"file": False})
        log.info("kept")
        assert "=" * 10 in capsys.readouterr().out
        assert not any("=" * 10 in line for line in _read_log(logdir))


class TestRunBanner:
    """run_banner brackets an invocation with machine-readable RUN records."""

    def test_begin_end_in_file_bars_on_console(self, logdir, capsys, monkeypatch):
        from xil_pipeline.sfx_common import run_banner

        _configure(monkeypatch, "xil produce", "--episode", "S01E01")
        with run_banner():
            logging.getLogger("x").info("work")

        out = capsys.readouterr().out
        assert "started" in out and "finished" in out       # console banner intact
        assert "BEGIN argv=" not in out                     # machine record stays out of stdout

        lines = _read_log(logdir)
        begin = [line for line in lines if "|RUN|" in line and "BEGIN argv=" in line]
        end = [line for line in lines if "|RUN|" in line and "END elapsed=" in line]
        assert len(begin) == 1, lines
        assert len(end) == 1, lines
        # The BEGIN record carries the invoking command — the "block header".
        assert 'argv="xil produce --episode S01E01"' in begin[0]
        assert "pid=" in begin[0] and "ver=" in begin[0] and "cwd=" in begin[0]
        assert "|produce|" in begin[0]


def test_run_level_registered():
    assert logging.getLevelName(RUN) == "RUN"
    assert logging.INFO < RUN < logging.WARNING
    assert log_config.RUN == 25


class TestPerHostFile:
    """Each machine writes its own file — appends are not atomic across clients
    on a shared network mount, so hosts must not share one log."""

    def test_filename_carries_host(self, logdir, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        logging.getLogger("x").info("hello")
        names = [p.name for p in logdir.glob("xil_v2_*.log")]
        assert len(names) == 1
        assert names[0].endswith(f"_{_HOST}.log"), names

    def test_host_field_present_on_every_record(self, logdir, monkeypatch):
        _configure(monkeypatch, "xil-produce")
        logging.getLogger("x").info("hello")
        assert _read_log(logdir)[0].split("|")[2] == _HOST

    def test_host_is_filename_safe(self):
        assert "/" not in _host() and " " not in _host()
        assert _host()
