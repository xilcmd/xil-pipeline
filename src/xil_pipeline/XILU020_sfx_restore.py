# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Replay journaled timeline sound edits onto an episode's SFX config.

Every save from the GUI timeline's sound profile editor is appended to
``configs/{slug}/sfx_{tag}_edits.jsonl``. When ``sfx_{tag}.json`` is cleared
and regenerated, ``xil parse`` reapplies the journal automatically; this
command does the same on demand — e.g. to force-reapply edits onto an
existing config, or to preview what a replay would change.

Usage::

    xil sfx-restore --episode S01E01
    xil sfx-restore --episode S01E01 --show mypodcast --dry-run
"""

import argparse
import os
import sys

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import derive_paths, resolve_slug
from xil_pipeline.sfx_common import replay_sfx_edits, run_banner, sfx_edits_path

logger = get_logger(__name__)

SCRIPT_NAME = "XILU020_sfx_restore"


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-sfx-restore."""
    parser = argparse.ArgumentParser(
        prog="xil-sfx-restore",
        description=(
            "Reapply journaled timeline sound edits (sfx_<tag>_edits.jsonl) "
            "onto the episode's SFX config — recovery for a cleared or "
            "regenerated sfx_<tag>.json."
        ),
    )
    tag_group = parser.add_mutually_exclusive_group(required=True)
    tag_group.add_argument("--episode", help="Episode tag (e.g. S04E04)")
    tag_group.add_argument("--tag", help="Raw non-episodic tag (e.g. V01C03)")
    parser.add_argument("--show", default=None,
                        help="Show name override (default: from project.json)")
    parser.add_argument("--sfx", default=None,
                        help="Override SFX config path")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be reapplied without writing")
    return parser


def main() -> None:
    """CLI entry point for SFX edit-journal replay."""
    configure_logging()
    args = get_parser().parse_args()
    tag = args.episode or args.tag

    with run_banner(SCRIPT_NAME):
        slug = resolve_slug(args.show)
        sfx_path = args.sfx or derive_paths(slug, tag)["sfx"]

        if not os.path.exists(sfx_path):
            logger.error(f"SFX config not found: {sfx_path}")
            logger.info("Run `xil parse --episode TAG` first to generate it "
                        "(the journal is reapplied automatically).")
            sys.exit(1)

        journal = sfx_edits_path(sfx_path)
        if not os.path.exists(journal):
            logger.error(f"No edit journal found: {journal}")
            logger.info("The journal is created the first time a sound profile "
                        "is saved in the GUI timeline editor.")
            sys.exit(1)

        applied, orphans = replay_sfx_edits(sfx_path, dry_run=args.dry_run)

        action = "Would reapply" if args.dry_run else "Reapplied"
        logger.info(f"  {action} {applied} edit record(s) from {journal}")
        for key in orphans:
            logger.warning(f"  Orphaned key (not in current effects): {key!r}")
        if args.dry_run and applied:
            logger.info("  Re-run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
