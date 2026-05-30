# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Export parsed_<tag>.json entries to CSV — one row per script entry.

Produces the same column layout as ``XILP001 --debug`` minus the two
markdown-source columns (``md_line_num``, ``md_raw``) that are only available
during a live parse run.  Use this to inspect, diff, or spreadsheet-import an
already-parsed episode without re-running the parser.

Output columns::
    file, tag, show, season, episode, seq, type, section, scene,
    speaker, direction, direction_type, text

Text is truncated at 200 characters to match the XILP001 debug output.
stdout is clean CSV (no banner) when no --output file is specified, so the
output is safe to pipe directly to csvkit, jq (via --json), etc.

**Usage:**

```bash
xil parsed-csv                                       # all parsed JSONs in workspace
xil parsed-csv parsed/the413/parsed_S04E02.json      # single episode
xil parsed-csv parsed/the413/                        # all in a show directory
xil parsed-csv --output debug.csv                    # write to file (with banner)
xil parsed-csv --json | jq '[.[] | select(.type=="direction")]'
```
"""

import argparse
import csv
import glob as _glob
import json
import os
import sys

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

_TRUNCATE = 200  # matches XILP001._DEBUG_TRUNCATE
_ENTRY_COLS = ["seq", "type", "section", "scene", "speaker", "direction", "direction_type", "text"]
ALL_COLS = ["file", "tag", "show", "season", "episode"] + _ENTRY_COLS


def _tag_from_path(path: str, data: dict) -> str:
    name = os.path.basename(path)
    if name.startswith("parsed_") and name.endswith(".json"):
        return name[len("parsed_"):-len(".json")]
    return data.get("tag_override", name)


def _rows_from_file(path: str) -> list[dict]:
    """Load one parsed JSON and return a flat list of row dicts."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    meta = {
        "file":    os.path.basename(path),
        "tag":     _tag_from_path(path, data),
        "show":    data.get("show", ""),
        "season":  data.get("season", ""),
        "episode": data.get("episode", ""),
    }
    rows = []
    for entry in data.get("entries", []):
        rows.append({
            **meta,
            "seq":            entry["seq"],
            "type":           entry["type"],
            "section":        entry.get("section") or "",
            "scene":          entry.get("scene") or "",
            "speaker":        entry.get("speaker") or "",
            "direction":      entry.get("direction") or "",
            "direction_type": entry.get("direction_type") or "",
            "text":           (entry.get("text") or "")[:_TRUNCATE],
        })
    return rows


def _collect_files(path: str) -> list[str]:
    """Return sorted list of parsed JSON files to process."""
    if os.path.isfile(path):
        return [os.path.abspath(path)]
    if os.path.isdir(path):
        matches = sorted(_glob.glob(os.path.join(path, "parsed_*.json")))
        return [os.path.abspath(p) for p in matches]
    return []


def _run(args: "argparse.Namespace") -> None:
    quiet = args.json or args.output is None

    if args.path == "__parsed_default__":
        try:
            root = get_workspace_root() / "parsed"
        except Exception:
            root = os.path.abspath("parsed")
        # Recurse into show subdirectories
        matches = sorted(_glob.glob(str(root / "**" / "parsed_*.json"), recursive=True))
        files = [os.path.abspath(p) for p in matches]
    else:
        files = _collect_files(os.path.abspath(args.path))

    if not files:
        logger.error("No parsed_*.json files found at: %s", args.path)
        return

    if not quiet:
        logger.info("Processing %d parsed JSON file(s)…", len(files))

    all_rows: list[dict] = []
    for p in files:
        try:
            rows = _rows_from_file(p)
            all_rows.extend(rows)
            if not quiet:
                logger.info("  %s → %d entries", os.path.basename(p), len(rows))
        except Exception as exc:
            logger.warning("Skipping %s — %s", os.path.basename(p), exc)

    if not all_rows:
        if not quiet:
            logger.info("No rows produced.")
        return

    if args.json:
        print(json.dumps(all_rows, indent=2))
        return

    def _write_csv(dest):
        writer = csv.DictWriter(dest, fieldnames=ALL_COLS)
        writer.writeheader()
        writer.writerows(all_rows)

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            _write_csv(f)
        logger.info("Written: %s  (%d rows)", args.output, len(all_rows))
    else:
        _write_csv(sys.stdout)

    if not quiet:
        logger.info("Total: %d entry rows from %d file(s)", len(all_rows), len(files))


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-parsed-csv",
        description="Export parsed_<tag>.json entries to CSV — one row per entry",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="__parsed_default__",
        help="parsed JSON file, or directory containing parsed_*.json files (default: workspace parsed/)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        metavar="FILE",
        help="Write CSV to FILE (default: stdout)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output a JSON array to stdout — no banner, safe to pipe to jq",
    )
    return parser


def main() -> None:
    """CLI entry point for parsed JSON → CSV export."""
    args = get_parser().parse_args()
    if args.json or args.output is None:
        _run(args)
    else:
        configure_logging()
        with run_banner():
            _run(args)


if __name__ == "__main__":
    main()
