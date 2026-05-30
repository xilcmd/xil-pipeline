# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Profile the audio loudness of MP3 files: peak, average, and minimum dBFS.

Loads each MP3 with pydub and reports three measurements per file::

  peak_dBFS  — loudest single sample moment (segment.max_dBFS)
  avg_dBFS   — RMS loudness across the whole file (segment.dBFS)
  min_dBFS   — quietest 500 ms window that is not pure silence

dBFS values are always ≤ 0.0; 0.0 means full-scale clipping, −60 is quiet.

Usage:

```bash
xil db-profile                          # scan workspace SFX/ folder
xil db-profile SFX/                     # explicit path
xil db-profile stems/the413/S04E02/     # scan one episode's stems
xil db-profile some.mp3                 # single-file mode
xil db-profile SFX/ --json             # machine-readable output
xil db-profile SFX/ --output report.csv
xil db-profile SFX/ --absolute
```
"""

import argparse
import csv
import json
import os

from pydub import AudioSegment

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

_CHUNK_MS = 500
_SILENCE_FLOOR_DB = -96.0  # below this a chunk is treated as silent/dead air


def _profile_file(path: str) -> dict:
    """Load an MP3 and return loudness measurements.

    Args:
        path: Path to an MP3 file.

    Returns:
        Dict with keys: path, duration_s, peak_dBFS, avg_dBFS, min_dBFS.
    """
    seg = AudioSegment.from_file(path)
    peak = seg.max_dBFS
    avg = seg.dBFS
    duration_s = len(seg) / 1000.0

    chunks = [seg[i : i + _CHUNK_MS] for i in range(0, len(seg), _CHUNK_MS)]
    levels = [c.dBFS for c in chunks if c.dBFS > _SILENCE_FLOOR_DB]
    min_db = min(levels) if levels else float("-inf")

    return {
        "path": path,
        "duration_s": round(duration_s, 2),
        "peak_dBFS": round(peak, 2),
        "avg_dBFS": round(avg, 2),
        "min_dBFS": round(min_db, 2),
    }


def _scan_mp3s(root: str) -> list[str]:
    """Recursively collect *.mp3 paths under *root* in sorted order."""
    paths = []
    for dirpath, _dirs, filenames in os.walk(root):
        for fname in sorted(filenames):
            if fname.lower().endswith(".mp3"):
                paths.append(os.path.abspath(os.path.join(dirpath, fname)))
    return paths


def _run(args: "argparse.Namespace") -> None:
    quiet = args.json

    # Resolve path: if user left it at the sentinel default, use workspace SFX/
    if args.path == "__sfx_default__":
        try:
            resolved = str(get_workspace_root() / "SFX")
        except Exception:
            resolved = os.path.abspath("SFX")
    else:
        resolved = os.path.abspath(args.path)

    if os.path.isfile(resolved):
        mp3_paths = [resolved]
        scan_root = os.path.dirname(resolved)
    elif os.path.isdir(resolved):
        if not quiet:
            logger.info("Scanning %s for MP3 files…", resolved)
        mp3_paths = _scan_mp3s(resolved)
        scan_root = resolved
    else:
        logger.error("Not a file or directory: %s", resolved)
        return

    if not mp3_paths:
        if not quiet:
            logger.info("No MP3 files found under %s", resolved)
        return

    records = []
    for i, p in enumerate(mp3_paths, 1):
        if not quiet:
            logger.info("[%d/%d] Profiling %s", i, len(mp3_paths), os.path.basename(p))
        try:
            rec = _profile_file(p)
        except Exception as exc:
            logger.warning("Skipping %s — %s", p, exc)
            continue

        if args.absolute:
            display_path = rec["path"]
        else:
            try:
                display_path = os.path.relpath(rec["path"], scan_root)
            except ValueError:
                display_path = rec["path"]

        rec["path"] = display_path
        records.append(rec)

    if not records:
        return

    if args.json:
        print(json.dumps(records, indent=2))
        return

    # Human-readable table
    col_w = max(len(r["path"]) for r in records)
    col_w = max(col_w, 8)
    header = f"{'filename':<{col_w}}  {'peak_dBFS':>10}  {'avg_dBFS':>9}  {'min_dBFS':>9}  {'dur_s':>7}"
    logger.info(header)
    logger.info("-" * len(header))
    for r in records:
        logger.info(
            "%-*s  %10.2f  %9.2f  %9.2f  %7.2f",
            col_w, r["path"],
            r["peak_dBFS"], r["avg_dBFS"], r["min_dBFS"], r["duration_s"],
        )

    if args.output:
        with open(args.output, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["path", "duration_s", "peak_dBFS", "avg_dBFS", "min_dBFS"])
            writer.writeheader()
            writer.writerows(records)
        if not quiet:
            logger.info("Written: %s", args.output)

    logger.info("Profiled %d MP3 file(s)", len(records))


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-db-profile",
        description="Profile MP3 audio levels: peak, average, and minimum dBFS",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="__sfx_default__",
        help="MP3 file or directory to scan recursively (default: workspace SFX/ folder)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        metavar="FILE",
        help="Write results to FILE as CSV in addition to logging",
    )
    parser.add_argument(
        "--absolute",
        action="store_true",
        help="Print absolute paths (default: relative to scan root)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output a JSON array to stdout — no banner, safe to pipe to jq",
    )
    return parser


def main() -> None:
    """CLI entry point for MP3 dBFS profiling."""
    args = get_parser().parse_args()
    if args.json:
        _run(args)
    else:
        configure_logging()
        with run_banner():
            _run(args)


if __name__ == "__main__":
    main()
