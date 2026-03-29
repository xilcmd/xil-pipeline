# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

#!/usr/bin/env python3
"""Remove stale stems left behind after a parsed script revision.

After running XILP007 (stem migrator), the stems directory may contain
files whose seq numbers now map to a different entry type in the current
parsed JSON.  This script finds those mismatches and deletes them.

Usage:
    python XILP008_stale_stem_cleanup.py --episode S02E03 --dry-run
    python XILP008_stale_stem_cleanup.py --episode S02E03
    python XILP008_stale_stem_cleanup.py \
        --parsed parsed/parsed_<slug>_S02E03.json \
        --stems stems/S02E03 [--dry-run]
"""

import argparse
import glob
import os

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.mix_common import extract_seq, load_entries_index
from xil_pipeline.models import derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)


def _expected_stem_basename(entry: dict) -> str:
    """Build the expected stem basename (no extension) from a parsed entry.

    Mirrors the naming logic in XILP002 / sfx_common: the basename is
    ``{seq:03d}_{section}[-{scene}]_{speaker|sfx}``.
    """
    seq = entry["seq"]
    section = entry.get("section", "")
    scene = entry.get("scene")
    name = f"{seq:03d}_{section}"
    if scene:
        name += f"-{scene}"
    if entry.get("type") == "dialogue":
        name += f"_{entry['speaker']}"
    else:
        name += "_sfx"
    return name


def find_stale_stems(
    stems_dir: str,
    entries_index: dict[int, dict],
) -> list[tuple[str, int, str]]:
    """Return a list of (filepath, seq, reason) for stale stems.

    A stem is stale when:
      - ``_sfx`` suffix but the entry is now ``dialogue``
      - speaker suffix (not ``_sfx``) but the entry is now ``direction``
      - dialogue stem whose speaker suffix doesn't match the parsed speaker
      - seq number not present in the parsed JSON at all
      - duplicate: multiple stems share the same seq; only the one matching
        the expected basename survives
    """
    stale: list[tuple[str, int, str]] = []
    stem_files = sorted(glob.glob(os.path.join(stems_dir, "*.mp3")))

    # Group files by seq to detect duplicates
    by_seq: dict[int, list[str]] = {}
    for filepath in stem_files:
        try:
            seq = extract_seq(filepath)
        except ValueError:
            continue
        by_seq.setdefault(seq, []).append(filepath)

    for seq, filepaths in sorted(by_seq.items()):
        entry = entries_index.get(seq)
        if entry is None:
            for fp in filepaths:
                stale.append((fp, seq, "seq not in parsed JSON"))
            continue

        entry_type = entry.get("type")

        # Header entries (section_header, scene_header) never have stems
        if entry_type not in ("dialogue", "direction"):
            for fp in filepaths:
                stale.append((
                    fp, seq,
                    f"seq {seq} is now a {entry_type} entry"
                ))
            continue

        # When multiple files exist for the same seq, keep only the one
        # whose basename matches the expected filename.
        if len(filepaths) > 1:
            expected = _expected_stem_basename(entry)
            for fp in filepaths:
                bn = os.path.splitext(os.path.basename(fp))[0]
                if bn != expected:
                    stale.append((
                        fp, seq,
                        f"seq {seq} duplicate (expected {expected})"
                    ))
            continue

        # Single file — check type and speaker match
        filepath = filepaths[0]
        basename = os.path.splitext(os.path.basename(filepath))[0]
        suffix = basename.rsplit("_", 1)[-1]
        is_sfx_stem = suffix == "sfx"

        if is_sfx_stem and entry_type == "dialogue":
            stale.append((filepath, seq, f"seq {seq} is now a dialogue entry"))
        elif not is_sfx_stem and entry_type == "direction":
            stale.append((filepath, seq, f"seq {seq} is now a direction entry"))
        elif entry_type == "dialogue":
            # Check speaker suffix matches parsed speaker
            expected_suffix = f"_{entry['speaker']}"
            if not basename.endswith(expected_suffix):
                stale.append((
                    filepath, seq,
                    f"seq {seq} speaker is now {entry['speaker']}"
                ))

    return stale


def main() -> None:
    """CLI entry point for stale stem cleanup."""
    configure_logging()
    with run_banner():
        parser = argparse.ArgumentParser(
            description=(
                "Remove stale stems that no longer match the current parsed "
                "script.  Use --dry-run first to review what would be deleted."
            )
        )
        parser.add_argument(
            "--episode", metavar="TAG",
            help="Episode tag (e.g. S02E03); derives --parsed and --stems",
        )
        parser.add_argument("--show", default=None, help="Show name override (default: from project.json)")
        parser.add_argument(
            "--parsed", metavar="PATH",
            help="Parsed script JSON (overrides --episode)",
        )
        parser.add_argument(
            "--stems", metavar="DIR",
            help="Stems directory (overrides --episode)",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List stale stems without deleting them",
        )

        args = parser.parse_args()

        # Resolve paths
        if args.episode:
            tag = args.episode
            slug = resolve_slug(args.show)
            p = derive_paths(slug, tag)
            parsed_path = args.parsed or p["parsed"]
            stems_dir = args.stems or f"stems/{tag}"
        else:
            if not (args.parsed and args.stems):
                parser.error(
                    "Provide --episode, or both --parsed and --stems."
                )
            parsed_path = args.parsed
            stems_dir = args.stems

        if not os.path.isfile(parsed_path):
            parser.error(f"Parsed JSON not found: {parsed_path}")
        if not os.path.isdir(stems_dir):
            parser.error(f"Stems directory not found: {stems_dir}")

        entries_index = load_entries_index(parsed_path)
        stale = find_stale_stems(stems_dir, entries_index)

        if not stale:
            logger.info("No stale stems found — stems directory is clean.")
            return

        label = "[DRY RUN] " if args.dry_run else ""
        logger.info(f"\n{label}Stale stems ({len(stale)}):\n")

        for filepath, seq, reason in stale:
            logger.info(f"  {os.path.basename(filepath):40s}  ({reason})")

        logger.info("")
        if args.dry_run:
            logger.info(f"  {len(stale)} stale stems would be deleted.")
            logger.info("  Re-run without --dry-run to delete them.")
        else:
            for filepath, _seq, _reason in stale:
                os.remove(filepath)
            logger.info(f"  Deleted {len(stale)} stale stems.")


if __name__ == "__main__":
    main()
