# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

#!/usr/bin/env python3
"""Migrate episode stems when a parsed script is revised.

Compares an old and new parsed JSON, copies unchanged stems to their new
seq-numbered filenames, and reports which entries need fresh TTS/SFX
generation.  Run XILP002 afterwards — it skips stems that already exist
on disk, so only the gaps get generated.

Usage::

    python XILP007_stem_migrator.py --episode S02E03 [--dry-run] [--strict]
    python XILP007_stem_migrator.py \\
        --old parsed/orig_parsed_<slug>_S02E03.json \\
        --new parsed/parsed_<slug>_S02E03.json \\
        --stems stems/S02E03 [--dry-run] [--strict]
"""

import argparse
import json
import os
import re
import shutil
from dataclasses import dataclass

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

# ── Status codes ──────────────────────────────────────────────────────────────
COPY = "COPY"       # unchanged — will be copied to new filename
SPEAKER = "SPEAKER" # speaker reassigned — needs fresh generation
NEW = "NEW"         # no old entry matched — needs fresh generation
MISSING = "MISSING" # match found but old stem file absent from disk
SKIP = "SKIP"       # no stem produced for this entry type

# Entry types that produce a stem file
STEM_TYPES = {"dialogue", "direction", "silence"}


_SNIP = 55  # characters to display before truncating text snippets


def _snip(text: str | None) -> str:
    """Return a truncated text snippet for display in migration reports."""
    if not text:
        return ""
    t = text.strip()
    return t[:_SNIP] + "\u2026" if len(t) > _SNIP else t


@dataclass
class MigrationAction:
    """Describes what should happen to one new parsed entry."""
    status: str
    new_seq: int
    new_stem: str       # just the filename, e.g. "019_act1-scene-1_maya.mp3"
    old_seq: int | None = None
    old_stem: str | None = None
    reason: str = ""
    entry_type: str = ""
    speaker: str | None = None
    new_text: str = ""  # truncated text of the new entry (for dry-run display)
    old_text: str = ""  # truncated text of the matched old entry (for dry-run display)


def normalize_text(text: str | None, strict: bool = False) -> str:
    """Normalize entry text for comparison.

    Always collapses whitespace.  In fuzzy mode (default) also normalises
    em-dashes, ellipsis, and curly quotes so that punctuation-only edits
    don't force unnecessary regeneration.
    """
    if text is None:
        return ""
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    if not strict:
        text = re.sub(r"\s*\u2014\s*", " - ", text)    # em-dash → " - "
        text = text.replace("\u2026", "...")             # ellipsis
        text = text.replace("\u2018", "'").replace("\u2019", "'")
        text = text.replace("\u201c", '"').replace("\u201d", '"')
    return text


def make_stem_name(entry: dict) -> str:
    """Return the expected stem filename (no directory) for a parsed entry.

    Dialogue:  {seq:03d}_{section}[-{scene}]_{speaker}.mp3
    Direction: {seq:03d}_{section}[-{scene}]_sfx.mp3
    Preamble:  n{abs(seq):03d}_{section}_{speaker_or_sfx}.mp3
    """
    seq = entry["seq"]
    section = entry.get("section") or "unknown"
    scene = entry.get("scene")
    speaker = entry.get("speaker") or "sfx"

    prefix = f"n{abs(seq):03d}" if seq < 0 else f"{seq:03d}"
    mid = f"{section}-{scene}" if scene else section

    if entry.get("type") == "dialogue":
        return f"{prefix}_{mid}_{speaker}.mp3"
    return f"{prefix}_{mid}_sfx.mp3"


def _match_key(entry: dict, strict: bool) -> tuple[str, str]:
    """Return (normalized_text, role) as a deduplication key."""
    role = entry.get("speaker") or "sfx"
    return (normalize_text(entry.get("text"), strict), role)


def build_old_index(
    old_entries: list[dict],
    stems_dir: str,
    strict: bool = False,
) -> tuple[dict[tuple[str, str], dict], dict[str, dict]]:
    """Build two lookups from old entries.

    Returns:
        exact_index:  (normalized_text, role) → record  — primary match
        text_index:   normalized_text → record  — fallback for speaker-change detection

    When multiple old entries share the same key (e.g. repeated BEATs)
    the first occurrence is kept so that many-to-one cases favour reuse.
    """
    exact: dict[tuple[str, str], dict] = {}
    text_only: dict[str, dict] = {}
    for entry in old_entries:
        if entry.get("type") not in STEM_TYPES:
            continue
        stem_name = make_stem_name(entry)
        record = {
            "entry": entry,
            "old_stem": stem_name,
            "exists": os.path.isfile(os.path.join(stems_dir, stem_name)),
        }
        ekey = _match_key(entry, strict)
        if ekey not in exact:
            exact[ekey] = record
        tkey = normalize_text(entry.get("text"), strict)
        if tkey not in text_only:
            text_only[tkey] = record
    return exact, text_only


def plan_migration(
    old_entries: list[dict],
    new_entries: list[dict],
    stems_dir: str,
    strict: bool = False,
) -> list[MigrationAction]:
    """Compare old and new parsed entries and produce a migration plan.

    Each new entry that produces a stem gets one MigrationAction whose
    status is COPY, SPEAKER, NEW, MISSING, or SKIP.

    Matching is two-phase:
      1. Exact: (normalized_text, speaker) — safe to copy or detect MISSING.
      2. Text-only fallback (dialogue only): same text, different speaker →
         SPEAKER status so the user knows *why* regeneration is needed.
    """
    exact_index, text_index = build_old_index(old_entries, stems_dir, strict)
    used_exact: set[tuple[str, str]] = set()
    used_text: set[str] = set()
    actions: list[MigrationAction] = []

    for entry in new_entries:
        etype = entry.get("type")
        new_seq = entry["seq"]

        if etype not in STEM_TYPES:
            actions.append(MigrationAction(
                status=SKIP, new_seq=new_seq,
                new_stem="", entry_type=etype or "",
            ))
            continue

        new_stem = make_stem_name(entry)
        new_speaker = entry.get("speaker")
        ekey = _match_key(entry, strict)
        tkey = normalize_text(entry.get("text"), strict)

        # Phase 1: exact (text + speaker) match
        match = exact_index.get(ekey)
        if match is not None and ekey not in used_exact:
            used_exact.add(ekey)
            used_text.add(tkey)
            old_entry = match["entry"]
            old_stem = match["old_stem"]
            old_seq = old_entry["seq"]
            if not match["exists"]:
                actions.append(MigrationAction(
                    status=MISSING, new_seq=new_seq, new_stem=new_stem,
                    old_seq=old_seq, old_stem=old_stem,
                    reason="old stem file not on disk",
                    entry_type=etype, speaker=new_speaker,
                    new_text=_snip(entry.get("text")),
                    old_text=_snip(old_entry.get("text")),
                ))
            else:
                actions.append(MigrationAction(
                    status=COPY, new_seq=new_seq, new_stem=new_stem,
                    old_seq=old_seq, old_stem=old_stem,
                    entry_type=etype, speaker=new_speaker,
                    new_text=_snip(entry.get("text")),
                    old_text=_snip(old_entry.get("text")),
                ))
            continue

        # Phase 2: text-only fallback (dialogue only) → speaker-change detection
        if etype == "dialogue":
            text_match = text_index.get(tkey)
            if text_match is not None and tkey not in used_text:
                used_text.add(tkey)
                old_entry = text_match["entry"]
                old_speaker = old_entry.get("speaker")
                if old_speaker != new_speaker:
                    actions.append(MigrationAction(
                        status=SPEAKER, new_seq=new_seq, new_stem=new_stem,
                        old_seq=old_entry["seq"], old_stem=text_match["old_stem"],
                        reason=f"speaker: {old_speaker} → {new_speaker}",
                        entry_type=etype, speaker=new_speaker,
                        new_text=_snip(entry.get("text")),
                        old_text=_snip(old_entry.get("text")),
                    ))
                    continue

        # No match at all
        actions.append(MigrationAction(
            status=NEW, new_seq=new_seq, new_stem=new_stem,
            reason="no matching old entry",
            entry_type=etype, speaker=new_speaker,
            new_text=_snip(entry.get("text")),
        ))

    return actions


def execute_migration(
    actions: list[MigrationAction],
    stems_dir: str,
    dry_run: bool = True,
) -> dict[str, int]:
    """Copy files according to the plan; return status counts.

    Only COPY actions with differing src/dst paths result in file I/O.
    All other statuses are counted but produce no side effects.
    """
    counts: dict[str, int] = {COPY: 0, SPEAKER: 0, NEW: 0, MISSING: 0, SKIP: 0}

    for action in actions:
        counts[action.status] = counts.get(action.status, 0) + 1
        if action.status != COPY:
            continue
        src = os.path.join(stems_dir, action.old_stem)
        dst = os.path.join(stems_dir, action.new_stem)
        if src == dst:
            continue
        if not dry_run:
            shutil.copy2(src, dst)

    return counts


def print_report(actions: list[MigrationAction], dry_run: bool) -> None:
    """Print per-stem migration details."""
    label = "[DRY RUN] " if dry_run else ""
    stem_actions = [a for a in actions if a.status != SKIP]
    logger.info(f"\n{label}Migration plan ({len(stem_actions)} stem entries):\n")

    for action in stem_actions:
        if action.status == COPY:
            if action.old_stem == action.new_stem:
                logger.info(f"  COPY     {action.new_stem}  (seq unchanged)")
            else:
                logger.info(f"  COPY     {action.new_stem}  ← {action.old_stem}")
            if action.new_text:
                logger.info(f"           \"{action.new_text}\"")
        elif action.status == SPEAKER:
            logger.info(f"  SPEAKER  {action.new_stem}  ({action.reason})")
            if action.new_text:
                logger.info(f"           \"{action.new_text}\"")
        elif action.status == MISSING:
            logger.info(f"  MISSING  {action.new_stem}  (matched seq {action.old_seq} but file absent)")
            if action.new_text:
                logger.info(f"           \"{action.new_text}\"")
        elif action.status == NEW:
            logger.info(f"  NEW      {action.new_stem}  ({action.reason})")
            if action.new_text:
                logger.info(f"           \"{action.new_text}\"")


def print_summary(counts: dict[str, int], dry_run: bool) -> None:
    """Print a one-page summary."""
    need_gen = counts.get(SPEAKER, 0) + counts.get(NEW, 0) + counts.get(MISSING, 0)
    label = "[DRY RUN] " if dry_run else ""
    logger.info(f"\n{label}─── Summary ───")
    logger.info(f"  COPY    : {counts.get(COPY, 0):4d}  (unchanged — reused, no TTS call)")
    logger.info(f"  SPEAKER : {counts.get(SPEAKER, 0):4d}  (speaker changed → must regenerate)")
    logger.info(f"  NEW     : {counts.get(NEW, 0):4d}  (no old match → must generate)")
    logger.info(f"  MISSING : {counts.get(MISSING, 0):4d}  (old match but file absent → generate)")
    logger.info(f"  SKIP    : {counts.get(SKIP, 0):4d}  (non-stem entries, no action)")
    logger.info("  ─────────────────────────────────────")
    logger.info(f"  Need generation : {need_gen}")
    logger.info("")
    if dry_run:
        logger.info("  Re-run without --dry-run to copy the COPY stems.")
    else:
        logger.info(f"  {counts.get(COPY, 0)} stems copied.")
    logger.info("  Then run:  python XILP002_producer.py --episode <TAG>")
    logger.info("  XILP002 skips stems already on disk — only gaps get generated.")


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-migrate",
        description=(
            "Migrate episode stems from an old parsed JSON to a revised one. "
            "Copies unchanged stems to their new seq-numbered filenames; "
            "reports what still needs TTS/SFX generation. "
            "Run XILP002 afterwards to fill the gaps."
        ),
    )
    parser.add_argument(
        "--episode", metavar="TAG",
        help="Episode tag (e.g. S02E03); derives --old, --new, and --stems paths automatically",
    )
    parser.add_argument(
        "--tag", metavar="TAG",
        help="Raw tag for non-episodic content (e.g. V01C03, D01); same as --episode but skips format validation",
    )
    parser.add_argument("--show", default=None, help="Show name override (default: from project.json)")
    parser.add_argument("--old", metavar="PATH", help="Old parsed JSON (overrides --episode)")
    parser.add_argument("--new", metavar="PATH", help="New parsed JSON (overrides --episode)")
    parser.add_argument("--stems", metavar="DIR", help="Stems directory (overrides --episode)")
    parser.add_argument(
        "--orig-prefix", default="orig_",
        help="Filename prefix for the old parsed JSON (default: orig_)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show plan without copying any files",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help=(
            "Exact text match only. Default is fuzzy: ignores em-dash/ellipsis "
            "variants so punctuation-only edits don't force unnecessary regen."
        ),
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Print only the summary, not per-stem details",
    )
    return parser


def main() -> None:
    """CLI entry point for stem migration."""
    configure_logging()
    with run_banner("XILP007 stem migrator"):
        parser = get_parser()
        args = parser.parse_args()

        # Resolve paths
        if args.episode or args.tag:
            tag = args.episode or args.tag
            slug = resolve_slug(args.show)
            p = derive_paths(slug, tag)
            old_path = args.old or f"parsed/{args.orig_prefix}parsed_{slug}_{tag}.json"
            new_path = args.new or p["parsed"]
            stems_dir = args.stems or f"stems/{slug}/{tag}"
        else:
            if not (args.old and args.new and args.stems):
                parser.error("Provide --episode, or all three of --old, --new, and --stems.")
            old_path, new_path, stems_dir = args.old, args.new, args.stems

        for p, label in [(old_path, "--old"), (new_path, "--new")]:
            if not os.path.isfile(p):
                parser.error(f"{label} file not found: {p}")

        logger.info(f"  Old parsed : {old_path}")
        logger.info(f"  New parsed : {new_path}")
        logger.info(f"  Stems dir  : {stems_dir}")
        logger.info(f"  Match mode : {'strict' if args.strict else 'fuzzy (ignores em-dash / ellipsis variants)'}")
        logger.info(f"  Dry run    : {args.dry_run}")

        with open(old_path) as f:
            old_data = json.load(f)
        with open(new_path) as f:
            new_data = json.load(f)

        actions = plan_migration(
            old_data.get("entries", []),
            new_data.get("entries", []),
            stems_dir,
            strict=args.strict,
        )

        if not args.quiet:
            print_report(actions, args.dry_run)

        counts = execute_migration(actions, stems_dir, dry_run=args.dry_run)
        print_summary(counts, args.dry_run)


if __name__ == "__main__":
    main()
