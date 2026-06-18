# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILU013 — SFX Source Hydration.

Reads ``sfx_source`` pipe-hint fields from a parsed script JSON and writes
the corresponding ``source`` values into the SFX config — without requiring
a full re-parse.

This is the standalone counterpart to the automatic backfill that runs at the
end of ``xil parse`` when the SFX config already exists.  Use it after adding
pipe-hints to an existing script and re-parsing, or to apply hints from a
``xil regen --sfx`` output without touching the original parsed JSON.

**Usage:**

```bash
xil sfx-hydrate --episode S04E04
xil sfx-hydrate --episode S04E04 --parsed parsed/the413/parsed_the413_S04E04.json
xil sfx-hydrate --episode S04E04 --sfx configs/the413/sfx_S04E04.json
xil sfx-hydrate --episode S04E04 --dry-run
```
"""

import argparse
import json
import os
import sys

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner
from xil_pipeline.XILP001_script_parser import backfill_sfx_sources

logger = get_logger(__name__)

SCRIPT_NAME = os.path.basename(__file__)


def hydrate_sfx_config(
    parsed: dict,
    sfx_path: str,
    dry_run: bool = False,
) -> int:
    """Apply pipe-hint source fields from *parsed* to the SFX config at *sfx_path*.

    Args:
        parsed: Parsed script dict (output of XILP001).
        sfx_path: Path to the SFX config JSON to update in-place.
        dry_run: When True, report changes without writing.

    Returns:
        Number of entries that would be (or were) updated.
    """
    with open(sfx_path, encoding="utf-8") as f:
        sfx_data = json.load(f)

    effects = sfx_data.get("effects", {})
    pending: list[tuple[str, str]] = []   # (clean_key, source_path)

    seen_clean: set[str] = set()
    for entry in parsed.get("entries", []):
        if entry.get("type") != "direction":
            continue
        sfx_source = entry.get("sfx_source")
        if not sfx_source:
            continue
        text = entry["text"]
        if text in seen_clean:
            continue
        seen_clean.add(text)

        # Only queue if source is actually missing
        if text in effects and effects[text].get("source"):
            continue
        pending.append((text, sfx_source))

    if not pending:
        logger.info("  No missing source fields — SFX config is already fully hydrated.")
        return 0

    for clean_key, source in pending:
        fname = os.path.basename(source)
        logger.info(f"  {'[dry-run] ' if dry_run else ''}  {clean_key}  →  {fname}")

    if not dry_run:
        backfill_sfx_sources(parsed, sfx_path)

    return len(pending)


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-sfx-hydrate."""
    parser = argparse.ArgumentParser(
        prog="xil-sfx-hydrate",
        description=(
            "Write pipe-hint source fields from parsed JSON into the SFX config "
            "without re-parsing the script."
        ),
    )
    tag_group = parser.add_mutually_exclusive_group(required=True)
    tag_group.add_argument("--episode", help="Episode tag (e.g. S04E04)")
    tag_group.add_argument("--tag", help="Raw non-episodic tag (e.g. V01C03)")
    parser.add_argument("--show", default=None,
                        help="Show name override (default: from project.json)")
    parser.add_argument("--parsed", default=None,
                        help="Override parsed JSON path")
    parser.add_argument("--sfx", default=None,
                        help="Override SFX config path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report changes without writing")
    return parser


def main() -> None:
    """CLI entry point for SFX config source-field hydration."""
    configure_logging()
    args = get_parser().parse_args()
    tag = args.episode or args.tag

    with run_banner(SCRIPT_NAME):
        slug = resolve_slug(args.show)
        p = derive_paths(slug, tag)
        parsed_path = args.parsed or p["parsed"]
        sfx_path = args.sfx or p["sfx"]

        if not os.path.exists(parsed_path):
            logger.error(f"Parsed JSON not found: {parsed_path}")
            sys.exit(1)

        if not os.path.exists(sfx_path):
            logger.error(f"SFX config not found: {sfx_path}")
            logger.info("Run `xil parse --episode TAG` first to generate it.")
            sys.exit(1)

        with open(parsed_path, encoding="utf-8") as f:
            parsed = json.load(f)

        count = hydrate_sfx_config(parsed, sfx_path, dry_run=args.dry_run)

        if count:
            action = "Would update" if args.dry_run else "Updated"
            logger.info(f"  {action} {count} source field(s) in {sfx_path}")
        if args.dry_run and count:
            logger.info("  Re-run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
