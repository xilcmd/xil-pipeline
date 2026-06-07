# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Cross-reference Whisper transcripts from xil-stem-verify against the parsed script.

Reads a stem_verify JSON report (produced by XILU015) and a parsed script JSON
(produced by XILP001), joins on ``seq``, and flags dialogue stems where the Whisper
transcript differs significantly from the scripted line.

Status codes
------------
ok              similarity >= threshold (silent — not written to flags list)
garbled         similarity < threshold and transcript is non-empty
silent          transcript.text is empty/whitespace (Whisper heard nothing)
no_stem         dialogue entry in parsed has no matching stem in the verify report
not_transcribed stem exists but transcript is null (xil-stem-verify --no-transcribe)

SFX stems are always excluded — Whisper output on sound effects is meaningless.

Usage::

    xil-stem-compare --episode S01E01
    xil-stem-compare --show the413 --episode S01E01 --threshold 0.70
    xil-stem-compare --stem-verify path/to/report.json --parsed path/to/parsed.json
    xil-stem-compare --episode S01E01 --output compare_S01E01.json
    xil-stem-compare --episode S01E01 --csv
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root, resolve_slug
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def _load_stem_index(stem_verify_path: Path) -> dict[int, dict]:
    """Build {seq: file_entry} from stem_verify JSON, excluding sfx stems."""
    import json
    with open(stem_verify_path, encoding="utf-8") as f:
        data = json.load(f)
    return {
        entry["seq"]: entry
        for entry in data["files"]
        if entry.get("seq") is not None and entry.get("speaker") != "sfx"
    }


def _load_dialogue_entries(parsed_path: Path) -> list[dict]:
    """Return parsed entries where type == 'dialogue' and direction_type is null."""
    import json
    with open(parsed_path, encoding="utf-8") as f:
        data = json.load(f)
    return [
        e for e in data["entries"]
        if e.get("type") == "dialogue" and e.get("direction_type") is None
    ]


def _compare(
    dialogue_entries: list[dict],
    stem_index: dict[int, dict],
    threshold: float,
) -> tuple[list[dict], dict]:
    """Run comparison and return (flags, summary_counts)."""
    counts: dict[str, int] = {"ok": 0, "garbled": 0, "silent": 0, "no_stem": 0, "not_transcribed": 0}
    flags: list[dict] = []

    for entry in dialogue_entries:
        seq = entry["seq"]
        stem = stem_index.get(seq)

        if stem is None:
            counts["no_stem"] += 1
            flags.append({
                "seq": seq,
                "section": entry.get("section"),
                "scene": entry.get("scene"),
                "speaker": entry.get("speaker"),
                "status": "no_stem",
                "similarity": None,
                "original": entry["text"],
                "transcript": None,
            })
            continue

        transcript_obj = stem.get("transcript")
        if transcript_obj is None:
            counts["not_transcribed"] += 1
            flags.append({
                "seq": seq,
                "section": entry.get("section"),
                "scene": entry.get("scene"),
                "speaker": entry.get("speaker"),
                "status": "not_transcribed",
                "similarity": None,
                "original": entry["text"],
                "transcript": None,
            })
            continue

        transcript_text = transcript_obj.get("text", "")
        if not transcript_text or not transcript_text.strip():
            counts["silent"] += 1
            flags.append({
                "seq": seq,
                "section": entry.get("section"),
                "scene": entry.get("scene"),
                "speaker": entry.get("speaker"),
                "status": "silent",
                "similarity": 0.0,
                "original": entry["text"],
                "transcript": "",
            })
            continue

        sim = round(_similarity(entry["text"], transcript_text), 4)
        if sim < threshold:
            counts["garbled"] += 1
            flags.append({
                "seq": seq,
                "section": entry.get("section"),
                "scene": entry.get("scene"),
                "speaker": entry.get("speaker"),
                "status": "garbled",
                "similarity": sim,
                "original": entry["text"],
                "transcript": transcript_text,
            })
        else:
            counts["ok"] += 1

    return flags, counts


def _print_summary(counts: dict, flags: list[dict], threshold: float) -> None:
    total = sum(counts.values())
    ok_pct = (counts["ok"] / total * 100) if total else 0.0
    logger.info("  Threshold      : %.2f", threshold)
    logger.info("  Dialogue stems : %d", total)
    logger.info("  OK             : %d  (%.1f%%)", counts["ok"], ok_pct)
    logger.info("  Garbled        : %d", counts["garbled"])
    logger.info("  Silent         : %d", counts["silent"])
    logger.info("  No stem        : %d", counts["no_stem"])
    logger.info("  Not transcribed: %d", counts["not_transcribed"])

    if not flags:
        logger.info("")
        logger.info("  No issues found.")
        return

    logger.info("")
    logger.info("--- Flagged entries ---")
    for f in flags:
        status = f["status"]
        seq_str = f"{f['seq']:03d}"
        speaker = (f["speaker"] or "?")[:12]
        if status == "garbled":
            logger.info("[garbled] seq=%s  %-12s sim=%.2f", seq_str, speaker, f["similarity"])
            logger.info("  ORIGINAL  : %s", f["original"])
            logger.info("  TRANSCRIPT: %s", f["transcript"])
        elif status == "silent":
            logger.info("[silent]  seq=%s  %s", seq_str, speaker)
            logger.info("  ORIGINAL  : %s", f["original"])
        elif status == "no_stem":
            logger.info("[no_stem] seq=%s  %s", seq_str, speaker)
            logger.info("  ORIGINAL  : %s", f["original"])
        elif status == "not_transcribed":
            logger.info("[no_xscr] seq=%s  %s", seq_str, speaker)
            logger.info("  ORIGINAL  : %s", f["original"])


def _print_csv(flags: list[dict]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(["seq", "section", "scene", "speaker", "status", "similarity", "original", "transcript"])
    for f in flags:
        writer.writerow([
            f["seq"], f["section"], f["scene"], f["speaker"],
            f["status"], f["similarity"], f["original"], f["transcript"],
        ])


def _write_json(
    flags: list[dict],
    counts: dict,
    show: str,
    episode: str,
    threshold: float,
    stem_verify_path: Path,
    parsed_path: Path,
    output_path: Path,
) -> None:
    import json
    report = {
        "show": show,
        "episode": episode,
        "generated": datetime.now().replace(microsecond=0).isoformat(),
        "threshold": threshold,
        "stem_verify_path": str(stem_verify_path.resolve()),
        "parsed_path": str(parsed_path.resolve()),
        "summary": {
            "total_dialogue": sum(counts.values()),
            **counts,
        },
        "flags": flags,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info("Written: %s", output_path)


def _run(args: argparse.Namespace) -> None:
    workspace = get_workspace_root()
    slug = resolve_slug(args.show)
    episode = args.episode or "unknown"

    if args.stem_verify:
        stem_verify_path = Path(args.stem_verify)
    else:
        if not args.episode:
            logger.error("--episode is required unless --stem-verify is provided")
            sys.exit(1)
        stem_verify_path = workspace / "parsed" / slug / f"stem_verify_{args.episode}.json"

    if args.parsed:
        parsed_path = Path(args.parsed)
    else:
        if not args.episode:
            logger.error("--episode is required unless --parsed is provided")
            sys.exit(1)
        parsed_path = workspace / "parsed" / slug / f"parsed_{args.episode}.json"

    if not stem_verify_path.exists():
        logger.error("stem_verify JSON not found: %s", stem_verify_path)
        sys.exit(1)
    if not parsed_path.exists():
        logger.error("parsed JSON not found: %s", parsed_path)
        sys.exit(1)

    logger.info("  stem_verify : %s", stem_verify_path)
    logger.info("  parsed      : %s", parsed_path)

    stem_index = _load_stem_index(stem_verify_path)
    dialogue_entries = _load_dialogue_entries(parsed_path)
    flags, counts = _compare(dialogue_entries, stem_index, args.threshold)

    if args.csv:
        _print_csv(flags)
    else:
        _print_summary(counts, flags, args.threshold)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(flags, counts, slug, episode, args.threshold, stem_verify_path, parsed_path, output_path)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-stem-compare",
        description="Cross-reference Whisper transcripts against parsed script dialogue to flag garbled stems.",
    )
    parser.add_argument("--show", "-s", default=None, metavar="SLUG",
                        help="Show slug (default: resolved from project.json)")
    parser.add_argument("--episode", "-e", default=None, metavar="TAG",
                        help="Episode tag, e.g. S01E01 (derives both JSON paths if not overridden)")
    parser.add_argument("--stem-verify", default=None, metavar="FILE",
                        help="Path to stem_verify JSON (default: <workspace>/parsed/<slug>/stem_verify_<episode>.json)")
    parser.add_argument("--parsed", default=None, metavar="FILE",
                        help="Path to parsed script JSON (default: <workspace>/parsed/<slug>/parsed_<episode>.json)")
    parser.add_argument("--threshold", type=float, default=0.75, metavar="FLOAT",
                        help="Similarity below this marks a stem as garbled (default: 0.75)")
    parser.add_argument("--output", "-o", default=None, metavar="FILE",
                        help="Write full JSON report to this file (in addition to terminal output)")
    parser.add_argument("--csv", action="store_true",
                        help="Print flagged entries as CSV to stdout instead of the banner summary")
    return parser


def main() -> None:
    """CLI entry point for Whisper transcript vs. script cross-reference."""
    configure_logging()
    args = get_parser().parse_args()
    with run_banner():
        _run(args)


if __name__ == "__main__":
    main()
