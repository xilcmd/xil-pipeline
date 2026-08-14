# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Logging configuration for the XIL pipeline CLI tools.

Each module obtains a logger via :func:`get_logger`::

    from xil_pipeline.log_config import get_logger
    logger = get_logger(__name__)

Each ``main()`` entry point calls :func:`configure_logging` once at
startup so that the root handler is installed before any output is
produced::

    from xil_pipeline.log_config import configure_logging

    def main():
        configure_logging()
        ...

Two sinks, two formats
----------------------

**Console (stdout)** — human-readable, unchanged by level except for a prefix:

- ``DEBUG``    → ``[debug] <message>``
- ``INFO``     → ``<message>``  (plain, same as a bare ``print()``)
- ``RUN``      → ``<message>``  (run banners; bare, so they read naturally)
- ``WARNING``  → ``[!] <message>``
- ``ERROR``    → ``[ERROR] <message>``
- ``CRITICAL`` → ``[CRITICAL] <message>``

**File** (``logs/xil_v2_<date>_<host>.log``) — machine-readable, one record per
line::

    2026-07-26T19:03:00-0400|INFO|hibirdy|produce|  > [006] adam via Chatterbox Turbo (282 chars)...
    2026-07-26T19:03:04-0400|INFO|hibirdy|produce|   Saved: stems/tww/S01E01/006_act1_adam.mp3

Fields are ``<iso-8601 ts>|<LEVEL>|<host>|<stage>|<message>``.  The *stage* is
derived from ``sys.argv[0]`` (``xil-produce`` and ``xil produce`` both yield
``produce``).  Multi-line messages are expanded so every physical line carries
the prefix; whitespace-only records are dropped from the file (they are console
spacing only).  **The message may contain ``|``** — consumers must split off
only the four leading fields.

Each invocation is bracketed by ``RUN`` records from
:func:`xil_pipeline.sfx_common.run_banner`, giving parsers a true per-run
boundary::

    2026-07-26T19:03:00-0400|RUN|hibirdy|produce|BEGIN argv="xil produce --episode S01E01" pid=1234 ver=0.3.1 cwd=/…
    2026-07-26T19:05:22-0400|RUN|hibirdy|produce|END elapsed=142.3s

Why the host appears twice
--------------------------

The workspace is often a shared network mount, so several machines can run
pipeline commands against one ``logs/`` directory.  Appends are **not** atomic
across clients on 9p/SMB/NFS, so a single shared file can interleave or lose
lines.  Giving each host its own file removes that hazard entirely; the field
additionally keeps attribution visible when grepping line content or when logs
from several machines are concatenated.

The ``v2`` in the filename marks this format.  Pre-v2 logs are bare stdout
transcripts named ``xil_<date>.log``; ``tools/migrate_logs_v1.py`` renames them
to ``xil_v1_<date>.log`` so the two never mix.

Call ``configure_logging(logging.DEBUG)`` to enable verbose output.
"""

import logging
import os
import platform
import re
import sys
from datetime import date, datetime

#: Level for per-invocation run banners (between INFO and WARNING).  Rendered
#: bare on the console so ``run_banner()`` looks unchanged, but tagged ``RUN``
#: in the structured log so a parser can find invocation boundaries.
RUN = 25
logging.addLevelName(RUN, "RUN")


def _host() -> str:
    """Short, filename-safe hostname identifying the machine writing the log.

    The workspace is routinely a shared network mount, so several machines can
    run pipeline commands against the same ``logs/`` directory.  This is used
    both to give each host its own file (append is not atomic across clients on
    9p/SMB/NFS, so a shared file can interleave) and to label each record.
    """
    name = (platform.node() or "unknown").split(".")[0]
    return re.sub(r"[^A-Za-z0-9._-]", "-", name) or "unknown"


#: Resolved once — the hostname cannot change mid-process.
_HOST = _host()


def _stage_from_argv() -> str:
    """Derive a short stage name from ``sys.argv[0]``.

    Handles both entry-point shapes: the per-stage console scripts
    (``xil-produce`` → ``produce``) and the dispatcher, which rewrites
    ``sys.argv[0]`` to ``"xil produce"`` before handing off.  Falls back to the
    bare program name so non-CLI callers (pytest, imports) still get something
    meaningful.
    """
    name = os.path.basename(sys.argv[0] or "xil")
    if name.startswith("xil ") and len(name) > 4:
        return name[4:].strip()
    if name.startswith("xil-") and len(name) > 4:
        return name[4:]
    return name or "xil"


class _CliFormatter(logging.Formatter):
    """Formatter that adds level prefixes only for WARNING and above."""

    _FORMATS = {
        logging.DEBUG: "[debug] %(message)s",
        logging.INFO: "%(message)s",
        RUN: "%(message)s",
        logging.WARNING: "[!] %(message)s",
        logging.ERROR: "[ERROR] %(message)s",
        logging.CRITICAL: "[CRITICAL] %(message)s",
    }

    def format(self, record: logging.LogRecord) -> str:
        fmt = self._FORMATS.get(record.levelno, "%(message)s")
        return logging.Formatter(fmt).format(record)


class _StructuredFormatter(logging.Formatter):
    """Machine-readable formatter for the log *file* (v2 format).

    Emits ``<iso-ts>|<LEVEL>|<host>|<stage>|<message>``.  Multi-line messages are
    expanded so every physical line carries the prefix and can be parsed
    independently.  The message may itself contain ``|``; consumers must split
    off only the four leading fields (see
    ``XILU008_stem_log_report._strip_v2_prefix``).
    """

    def format(self, record: logging.LogRecord) -> str:
        # Resolved per record, not once at configure time: the dispatcher calls
        # configure_logging() while argv[0] is still bare "xil" and only then
        # rewrites it to "xil <command>" before handing off to the stage.  A
        # cached stage would label every dispatched run "xil".
        ts = datetime.fromtimestamp(record.created).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        prefix = f"{ts}|{record.levelname}|{_HOST}|{_stage_from_argv()}|"
        text = record.getMessage()
        if record.exc_info:
            text = f"{text}\n{self.formatException(record.exc_info)}"
        return "\n".join(prefix + line for line in text.split("\n"))


class _SkipBlankFilter(logging.Filter):
    """Drop whitespace-only records — console spacing that adds nothing to a log."""

    def filter(self, record: logging.LogRecord) -> bool:
        return bool(record.getMessage().strip())


class _SinkFilter(logging.Filter):
    """Honour per-record sink opt-outs.

    A caller may route a record to one sink only via
    ``logger.log(..., extra={"console": False})`` or ``extra={"file": False}``.
    Used by :func:`xil_pipeline.sfx_common.run_banner` to keep the decorative
    bars on the terminal and the machine-readable BEGIN/END records in the file.
    """

    def __init__(self, sink: str) -> None:
        super().__init__()
        self._sink = sink

    def filter(self, record: logging.LogRecord) -> bool:
        return getattr(record, self._sink, True) is not False


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger for CLI output.

    Safe to call multiple times — only the first call installs the
    stdout handler.  Subsequent calls may still update the log level.

    Automatically tees output to ``logs/xil_v2_<date>_<host>.log`` under the
    workspace root.  The ``logs/`` directory is created if it
    does not exist.

    Args:
        level: Logging level threshold (default: ``logging.INFO``).
            Pass ``logging.DEBUG`` to enable verbose output.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_CliFormatter())
        handler.addFilter(_SinkFilter("console"))
        root.addHandler(handler)

        from xil_pipeline.models import get_workspace_root
        log_dir = get_workspace_root() / "logs"
        log_dir.mkdir(exist_ok=True)
        # v2 = structured, one "ts|level|host|stage|message" record per line.
        # Per-host file: appends are not atomic across clients on a shared
        # network mount, so machines must not share one file.  The
        # version is in the filename so parsers never have to sniff: pre-v2
        # files are the bare stdout transcripts (xil_<date>.log, renamed to
        # xil_v1_<date>.log by tools/migrate_logs_v1.py).
        log_path = log_dir / f"xil_v2_{date.today().isoformat()}_{_HOST}.log"
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(_StructuredFormatter())
        fh.addFilter(_SkipBlankFilter())
        fh.addFilter(_SinkFilter("file"))
        root.addHandler(fh)

    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, auto-configuring the root logger if needed.

    Args:
        name: Logger name, typically ``__name__`` of the calling module.

    Returns:
        A :class:`logging.Logger` instance.
    """
    if not logging.getLogger().handlers:
        configure_logging()
    return logging.getLogger(name)
