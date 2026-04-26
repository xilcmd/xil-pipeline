# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
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

Output format by level:

- ``DEBUG``    → ``[debug] <message>``
- ``INFO``     → ``<message>``  (plain, same as a bare ``print()``)
- ``WARNING``  → ``[!] <message>``
- ``ERROR``    → ``[ERROR] <message>``
- ``CRITICAL`` → ``[CRITICAL] <message>``

Call ``configure_logging(logging.DEBUG)`` to enable verbose output.
"""

import logging
import sys
from datetime import date
from pathlib import Path


class _CliFormatter(logging.Formatter):
    """Formatter that adds level prefixes only for WARNING and above."""

    _FORMATS = {
        logging.DEBUG: "[debug] %(message)s",
        logging.INFO: "%(message)s",
        logging.WARNING: "[!] %(message)s",
        logging.ERROR: "[ERROR] %(message)s",
        logging.CRITICAL: "[CRITICAL] %(message)s",
    }

    def format(self, record: logging.LogRecord) -> str:
        fmt = self._FORMATS.get(record.levelno, "%(message)s")
        return logging.Formatter(fmt).format(record)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger for CLI output.

    Safe to call multiple times — only the first call installs the
    stdout handler.  Subsequent calls may still update the log level.

    Automatically tees output to ``logs/xil_YYYY-MM-DD.log`` in the
    current working directory.  The ``logs/`` directory is created if it
    does not exist.

    Args:
        level: Logging level threshold (default: ``logging.INFO``).
            Pass ``logging.DEBUG`` to enable verbose output.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_CliFormatter())
        root.addHandler(handler)

        from xil_pipeline.models import get_workspace_root
        log_dir = get_workspace_root() / "logs"
        log_dir.mkdir(exist_ok=True)
        log_path = log_dir / f"xil_{date.today().isoformat()}.log"
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(_CliFormatter())
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
