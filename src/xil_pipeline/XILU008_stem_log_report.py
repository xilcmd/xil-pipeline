# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Stem generation log reporter.

Parses daily ``logs/xil_YYYY-MM-DD.log`` files and produces a CSV
chronology of every dialogue MP3 stem that was generated, including
its backend, SHA-256 hash, and approximate creation context.

Usage::

    xil-stem-log                              # scan logs/ in CWD → stem_log_report.csv
    xil-stem-log --logs-dir logs/             # explicit log directory
    xil-stem-log --output report.csv          # override output path
    xil-stem-log --since 2026-04-01           # only logs on or after this date
    xil-stem-log --episode S03E03             # filter to one episode tag
    xil-stem-log --episode S03E03 --since 2026-04-01
    xil-stem-log --slug the413                # filter to one show slug
    xil-stem-log --show                       # print CSV to stdout instead of file

CSV columns
-----------
log_date        YYYY-MM-DD date from log filename
log_file        log filename
run_index       integer incremented per ``Phase 1: Generating`` block (proxy for
                distinct ``xil produce`` invocations within a day)
log_line        line number of the ``Saved:`` entry (intra-day ordering)
seq             dialogue sequence number
speaker         speaker key (e.g. ``adam``, ``sarah``)
backend         TTS engine: ``eleven_v3``, ``gtts``, or ``chatterbox``
char_count      character count sent to the TTS engine
stem_path       relative path recorded in log (e.g. ``stems/the413/S03E03/…``)
stem_filename   basename only
sha256          SHA-256 hex digest of the generated file
"""

import argparse
import csv
import re
import sys
from datetime import date
from pathlib import Path

# ── regex patterns ────────────────────────────────────────────────────────────

# ElevenLabs: > [006] adam with eleven_v3 (282 chars)...
_RE_ELEVEN = re.compile(
    r"^\s*>\s*\[(\d+)\]\s+(\S+)\s+with\s+(eleven_\S+)\s+\((\d+)\s+chars\)",
)

# gTTS:        > [005] maya via gTTS (100 chars)...
_RE_GTTS = re.compile(
    r"^\s*>\s*\[(\d+)\]\s+(\S+)\s+via\s+gTTS\s+\((\d+)\s+chars\)",
)

# Chatterbox:  > [005] maya via Chatterbox (100 chars)...
_RE_CHATTERBOX = re.compile(
    r"^\s*>\s*\[(\d+)\]\s+(\S+)\s+via\s+Chatterbox\s+\((\d+)\s+chars\)",
    re.IGNORECASE,
)

# Saved:   Saved: stems/the413/S03E03/006_…mp3
_RE_SAVED = re.compile(r"^\s*Saved:\s+(\S+)")

# SHA256:  SHA256: <hex>
_RE_SHA256 = re.compile(r"^\s*SHA256:\s+([0-9a-fA-F]+)")

# Run boundary
_RE_PHASE1 = re.compile(r"^---\s*Phase 1:\s*Generating")


def _parse_log(log_path: Path) -> list[dict]:
    """Return list of stem records parsed from a single log file."""
    log_date = _date_from_filename(log_path.name)
    records: list[dict] = []

    run_index = 0
    pending: dict | None = None  # accumulates fields until SHA256 line

    with log_path.open(encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n")

            if _RE_PHASE1.match(line):
                run_index += 1
                pending = None
                continue

            # Generation line — start a new pending record
            for pattern, backend_key in (
                (_RE_ELEVEN, None),   # backend extracted from match group 3
                (_RE_GTTS, "gtts"),
                (_RE_CHATTERBOX, "chatterbox"),
            ):
                m = pattern.match(line)
                if m:
                    if backend_key is None:
                        seq, speaker, backend, chars = m.group(1), m.group(2), m.group(3), m.group(4)
                    else:
                        seq, speaker, chars = m.group(1), m.group(2), m.group(3)
                        backend = backend_key
                    pending = {
                        "log_date": log_date,
                        "log_file": log_path.name,
                        "run_index": run_index,
                        "log_line": None,
                        "seq": int(seq),
                        "speaker": speaker,
                        "backend": backend,
                        "char_count": int(chars),
                        "stem_path": None,
                        "stem_filename": None,
                        "sha256": None,
                    }
                    break

            if pending is None:
                continue

            m = _RE_SAVED.match(line)
            if m and pending["stem_path"] is None:
                pending["stem_path"] = m.group(1)
                pending["stem_filename"] = Path(m.group(1)).name
                pending["log_line"] = lineno
                continue

            m = _RE_SHA256.match(line)
            if m and pending["stem_path"] is not None and pending["sha256"] is None:
                pending["sha256"] = m.group(1)
                records.append(dict(pending))
                pending = None

    return records


def _date_from_filename(name: str) -> str:
    """Extract YYYY-MM-DD from ``xil_2026-04-17.log``; fall back to today."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", name)
    return m.group(1) if m else date.today().isoformat()


_FIELDNAMES = [
    "log_date", "log_file", "run_index", "log_line",
    "seq", "speaker", "backend", "char_count",
    "stem_path", "stem_filename", "sha256",
]


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-stem-log",
        description="Parse xil-pipeline logs into a stem generation chronology CSV.",
    )
    parser.add_argument(
        "--logs-dir",
        default="logs",
        metavar="DIR",
        help="Directory containing xil_YYYY-MM-DD.log files (default: logs/)",
    )
    parser.add_argument(
        "--output", "-o",
        default="stem_log_report.csv",
        metavar="PATH",
        help="Output CSV path (default: stem_log_report.csv); use - for stdout",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only include log files on or after this date",
    )
    parser.add_argument(
        "--episode", "--tag",
        metavar="TAG",
        dest="episode",
        help="Filter records to a specific episode tag (e.g. S03E03)",
    )
    parser.add_argument(
        "--slug",
        metavar="SLUG",
        help="Filter records to a specific show slug (e.g. the413)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Print CSV to stdout (equivalent to --output -)",
    )
    return parser


def main() -> None:
    args = get_parser().parse_args()

    logs_dir = Path(args.logs_dir)
    if not logs_dir.is_dir():
        print(f"[ERROR] Logs directory not found: {logs_dir}", file=sys.stderr)
        sys.exit(1)

    log_files = sorted(logs_dir.glob("xil_*.log"))
    if args.since:
        log_files = [f for f in log_files if _date_from_filename(f.name) >= args.since]

    if not log_files:
        print("[!] No matching log files found.", file=sys.stderr)
        sys.exit(0)

    all_records: list[dict] = []
    for lf in log_files:
        records = _parse_log(lf)
        all_records.extend(records)
        print(f"  {lf.name}: {len(records)} stems", file=sys.stderr)

    if args.episode:
        tag = args.episode.upper()
        all_records = [r for r in all_records if r.get("stem_path") and tag in (r["stem_path"].upper())]
    if args.slug:
        slug = args.slug.lower()
        all_records = [r for r in all_records if r.get("stem_path") and slug in (r["stem_path"].lower())]

    print(f"Total: {len(all_records)} stem records", file=sys.stderr)

    use_stdout = args.show or args.output == "-"
    out_path = None if use_stdout else Path(args.output)

    if use_stdout:
        _write_csv(sys.stdout, all_records)
    else:
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            _write_csv(fh, all_records)
        print(f"Written: {out_path}", file=sys.stderr)


def _write_csv(fh, records: list[dict]) -> None:
    writer = csv.DictWriter(fh, fieldnames=_FIELDNAMES)
    writer.writeheader()
    writer.writerows(records)


if __name__ == "__main__":
    main()
