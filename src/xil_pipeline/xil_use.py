# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``xil use`` — set or display the active show context.

Usage::

    xil use                         # list available shows, mark active one
    xil use "The Woonsocket Wonders" # set by show name
    xil use thewoonsocketwonders      # set by slug
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_active_show, get_workspace_root, set_active_show, show_slug

logger = get_logger(__name__)


def _available_shows(root: Path) -> list[tuple[str, str]]:
    """Return [(slug, show_name), ...] for all shows with a configs/{slug}/project.json."""
    configs_dir = root / "configs"
    if not configs_dir.is_dir():
        return []
    results = []
    for entry in sorted(configs_dir.iterdir()):
        pj = entry / "project.json"
        if entry.is_dir() and pj.exists():
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
                results.append((entry.name, data.get("show", entry.name)))
            except (json.JSONDecodeError, OSError):
                results.append((entry.name, entry.name))
    return results


def main() -> int:
    configure_logging()
    args = sys.argv[1:]

    root = get_workspace_root()
    shows = _available_shows(root)
    current = get_active_show()

    if not args:
        if not shows:
            logger.info("No shows found. Run 'xil init --show \"My Show\"' to create one.")
            return 0
        logger.info("Available shows (* = active):")
        for slug, name in shows:
            marker = "* " if slug == current else "  "
            logger.info("  %s%s  (%s)", marker, name, slug)
        if current and not any(s == current for s, _ in shows):
            logger.warning("Active show '%s' has no project.json in configs/.", current)
        return 0

    query = " ".join(args).strip()
    query_slug = show_slug(query)

    # Match by slug first, then by show name slug
    matched_slug = None
    matched_name = None
    for slug, name in shows:
        if slug == query_slug or slug == query or show_slug(name) == query_slug:
            matched_slug = slug
            matched_name = name
            break

    if matched_slug is None:
        logger.error("No show matching '%s'. Run 'xil use' to list available shows.", query)
        return 1

    set_active_show(matched_slug)
    logger.info("Active show: %s  (%s)", matched_name, matched_slug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
