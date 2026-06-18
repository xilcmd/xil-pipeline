# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Flatten sfx_<tag>.json configs to CSV — one row per effect entry.

Each output row combines:
  • Episode metadata  — show, season, episode, tag
  • Defaults          — all defaults.* fields, prefixed with ``default_``
  • Effect identity   — effect_key (the dict key used in the effects block)
  • Effect fields     — prompt, type, source, duration_seconds, loop,
                        volume_percentage, play_duration,
                        ramp_in_seconds, ramp_out_seconds, prompt_influence

Missing optional fields are left blank.  Useful for spotting misconfigured
effects, auditing prompt coverage, or importing into a spreadsheet.

**Usage:**

```bash
xil sfx-csv                                  # all sfx configs in workspace
xil sfx-csv configs/the413/sfx_S04E02.json   # single file
xil sfx-csv configs/the413/                  # all sfx_*.json in a dir
xil sfx-csv --output sfx_debug.csv           # write to file
xil sfx-csv --json                           # JSON array to stdout
```
"""

import argparse
import csv
import glob
import json
import os
import sys

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

# Canonical column order — defines CSV header.
# Cols 1-5 match parsed-csv; col 6 (effect_key) aligns with seq; col 7 (type) aligns exactly.
_META_COLS = ["file", "tag", "show", "season", "episode"]
_EFFECT_COLS = [
    "effect_key",
    "type",
    "prompt",
    "source",
    "duration_seconds",
    "loop",
    "play_duration",
    "volume_percentage",
    "ramp_in_seconds",
    "ramp_out_seconds",
    "prompt_influence",
]
_DEFAULT_COLS = [
    "default_prompt_influence",
    "default_volume_percentage",
    "default_ramp_in_seconds",
    "default_ramp_out_seconds",
    "default_music_volume_percentage",
    "default_music_ramp_in_seconds",
    "default_music_ramp_out_seconds",
    "default_ambience_volume_percentage",
    "default_ambience_ramp_in_seconds",
    "default_ambience_ramp_out_seconds",
]
ALL_COLS = _META_COLS + _EFFECT_COLS + _DEFAULT_COLS


def _tag_from_path(path: str, data: dict) -> str:
    """Return the canonical episode tag for a config file."""
    if "tag_override" in data:
        return data["tag_override"]
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem[len("sfx_"):] if stem.startswith("sfx_") else stem


def _flatten(path: str) -> list[dict]:
    """Parse one sfx JSON file and return a list of flat row dicts."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    tag = _tag_from_path(path, data)
    defaults = data.get("defaults", {})

    meta = {
        "file": os.path.basename(path),
        "tag": tag,
        "show": data.get("show", ""),
        "season": data.get("season", ""),
        "episode": data.get("episode", ""),
    }
    default_values = {f"default_{k}": v for k, v in defaults.items()}

    rows = []
    for key, effect in data.get("effects", {}).items():
        row = dict.fromkeys(ALL_COLS, "")
        row.update(meta)
        row.update(default_values)
        row["effect_key"] = key
        for field in ("prompt", "type", "source", "duration_seconds",
                      "loop", "play_duration", "volume_percentage",
                      "ramp_in_seconds", "ramp_out_seconds", "prompt_influence"):
            if field in effect:
                row[field] = effect[field]
        rows.append(row)
    return rows


def _collect_files(path: str) -> list[str]:
    """Return sorted list of sfx JSON files to process."""
    if os.path.isfile(path):
        return [os.path.abspath(path)]
    if os.path.isdir(path):
        matches = sorted(glob.glob(os.path.join(path, "sfx_*.json")))
        return [os.path.abspath(p) for p in matches]
    return []


def _run(args: "argparse.Namespace") -> None:
    # quiet: suppress banner/progress when stdout carries data (json or plain CSV)
    quiet = args.json or args.output is None

    if args.path == "__configs_default__":
        try:
            resolved = str(get_workspace_root() / "configs")
        except Exception:
            resolved = os.path.abspath("configs")
    else:
        resolved = os.path.abspath(args.path)

    files = _collect_files(resolved)
    if not files:
        logger.error("No sfx_*.json files found at: %s", resolved)
        return

    if not quiet:
        logger.info("Processing %d sfx config file(s)…", len(files))

    all_rows: list[dict] = []
    for p in files:
        try:
            rows = _flatten(p)
            all_rows.extend(rows)
            if not quiet:
                logger.info("  %s → %d effect(s)", os.path.basename(p), len(rows))
        except Exception as exc:
            logger.warning("Skipping %s — %s", os.path.basename(p), exc)

    if not all_rows:
        if not quiet:
            logger.info("No effect rows produced.")
        return

    if args.json:
        print(json.dumps(all_rows, indent=2))
        return

    # Write CSV to file and/or stdout
    out_path = args.output

    def _write_csv(dest):
        writer = csv.DictWriter(dest, fieldnames=ALL_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    if out_path:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            _write_csv(f)
        logger.info("Written: %s  (%d rows)", out_path, len(all_rows))
    else:
        _write_csv(sys.stdout)

    if not quiet:
        logger.info("Total: %d effect rows from %d file(s)", len(all_rows), len(files))


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-sfx-csv."""
    parser = argparse.ArgumentParser(
        prog="xil-sfx-csv",
        description="Flatten sfx_<tag>.json configs to CSV — one row per effect",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="__configs_default__",
        help="sfx JSON file, or directory containing sfx_*.json files (default: workspace configs/)",
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
    """CLI entry point for sfx config CSV export."""
    args = get_parser().parse_args()
    # When stdout carries data (--json or no --output), skip the banner so the
    # output is clean and pipeable directly to jq / csvkit / etc.
    if args.json or args.output is None:
        _run(args)
    else:
        configure_logging()
        with run_banner():
            _run(args)


if __name__ == "__main__":
    main()
