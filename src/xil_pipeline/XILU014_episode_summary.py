# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILU014 — Episode Summary CSV.

Scans all parsed_<tag>.json files under <workspace>/parsed/ and writes a
one-row-per-episode summary CSV with dialogue line count, word count, and
TTS character count.

Usage:

```bash
xil episode-summary                          # write episode_summary.csv in workspace root
xil episode-summary --output summary.csv     # custom output path
xil episode-summary --show "THE 413"         # filter to one show
xil episode-summary --stdout                 # write CSV to stdout (no banner)
```
"""

import argparse
import csv
import glob as _glob
import json
import os
import sys
from pathlib import Path

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

SCRIPT_NAME = os.path.basename(__file__)

_SKIP_PREFIXES = ("roundtrip_", "pre_splice_")

ALL_COLS = [
    "show", "tag", "season", "episode", "title", "season_title",
    "dialogue_lines", "words", "tts_chars",
]


def _collect_files(parsed_root: Path) -> list[str]:
    matches = sorted(_glob.glob(str(parsed_root / "**" / "parsed_*.json"), recursive=True))
    return [
        p for p in matches
        if not any(os.path.basename(p).startswith(pfx) for pfx in _SKIP_PREFIXES)
    ]


def _sort_key(row: dict) -> tuple:
    try:
        s = int(row["season"])
    except (ValueError, TypeError):
        s = 999
    try:
        e = int(row["episode"])
    except (ValueError, TypeError):
        e = 999
    return (row["show"], s, e, row["tag"])


def build_summary(parsed_root: Path, show_filter: str | None = None) -> list[dict]:
    """Return one summary dict per episode found under parsed_root."""
    files = _collect_files(parsed_root)
    rows = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("Skipping %s — %s", os.path.basename(path), exc)
            continue

        show = data.get("show", "")
        if show_filter and show.lower() != show_filter.lower():
            continue

        tag = os.path.basename(path).removeprefix("parsed_").removesuffix(".json")
        season  = data.get("season")
        episode = data.get("episode")
        stats   = data.get("stats", {})

        words = sum(
            len(entry.get("text", "").split())
            for entry in data.get("entries", [])
            if entry.get("type") == "dialogue"
        )

        rows.append({
            "show":           show,
            "tag":            tag,
            "season":         season if season is not None else "",
            "episode":        episode if episode is not None else "",
            "title":          data.get("title", ""),
            "season_title":   data.get("season_title", ""),
            "dialogue_lines": stats.get("dialogue_lines", 0),
            "words":          words,
            "tts_chars":      stats.get("characters_for_tts", 0),
        })

    rows.sort(key=_sort_key)
    return rows


def _write_csv(rows: list[dict], dest) -> None:
    writer = csv.DictWriter(dest, fieldnames=ALL_COLS)
    writer.writeheader()
    writer.writerows(rows)


def _run(args: argparse.Namespace) -> None:
    try:
        workspace = get_workspace_root()
    except Exception:
        workspace = Path(os.getcwd())

    parsed_root = workspace / "parsed"
    if not parsed_root.is_dir():
        logger.error("parsed/ directory not found at: %s", parsed_root)
        sys.exit(1)

    rows = build_summary(parsed_root, show_filter=args.show)

    if not rows:
        logger.warning("No parsed_*.json files found under %s", parsed_root)
        return

    if args.stdout:
        _write_csv(rows, sys.stdout)
        return

    output = Path(args.output) if args.output else workspace / "episode_summary.csv"
    with open(output, "w", newline="", encoding="utf-8") as f:
        _write_csv(rows, f)

    logger.info("  Episodes:  %d", len(rows))
    logger.info("  Written:   %s", output)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-episode-summary",
        description="Write a one-row-per-episode summary CSV from all parsed_<tag>.json files.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        metavar="FILE",
        help="Output CSV path (default: <workspace>/episode_summary.csv)",
    )
    parser.add_argument(
        "--show",
        default=None,
        metavar="NAME",
        help="Filter to a single show name, e.g. 'THE 413' (case-insensitive)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write CSV to stdout — no banner, safe to pipe",
    )
    return parser


def main() -> None:
    configure_logging()
    args = get_parser().parse_args()
    if args.stdout:
        _run(args)
    else:
        with run_banner(SCRIPT_NAME):
            _run(args)


if __name__ == "__main__":
    main()
