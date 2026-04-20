# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Migrate a pre-0.1.8 workspace to the normalized directory layout.

Old (flat) layout::

    cast_{slug}_{tag}.json          → configs/{slug}/cast_{tag}.json
    sfx_{slug}_{tag}.json           → configs/{slug}/sfx_{tag}.json
    parsed/parsed_{slug}_{tag}.json → parsed/{slug}/parsed_{tag}.json
    parsed/parsed_{slug}_{tag}.csv  → parsed/{slug}/parsed_{tag}.csv
    parsed/annotated_{slug}_{tag}*  → parsed/{slug}/annotated_{tag}.*
    parsed/orig_parsed_{slug}_{tag} → parsed/{slug}/orig_parsed_{tag}.json
    daw/{tag}/                      → daw/{slug}/{tag}/
    masters/{slug}_{tag}_master.mp3 → masters/{slug}/{tag}_master.mp3
    cues/cues_{slug}_{tag}.md       → cues/{slug}/cues_{tag}.md
    cues/cues_manifest_{tag}.json   → cues/{slug}/cues_manifest_{tag}.json

``stems/{slug}/{tag}/`` is already normalized — no move needed.

Usage::

    xil migrate-workspace --dry-run    # preview what would move
    xil migrate-workspace              # execute moves
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import derive_paths_legacy, show_slug
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

# Pattern matching legacy root-level cast config: cast_{slug}_{tag}.json
_CAST_RE = re.compile(r"^cast_([a-z0-9]+)_([A-Z0-9]+)\.json$")
# Pattern matching legacy sfx config at root
_SFX_RE = re.compile(r"^sfx_([a-z0-9]+)_([A-Z0-9]+)\.json$")
# Pattern matching legacy parsed JSON: parsed/parsed_{slug}_{tag}.json
_PARSED_RE = re.compile(r"^parsed_([a-z0-9]+)_([A-Z0-9]+)\.json$")
_PARSED_CSV_RE = re.compile(r"^parsed_([a-z0-9]+)_([A-Z0-9]+)\.csv$")
_ANNOTATED_RE = re.compile(r"^parsed_([a-z0-9]+)_([A-Z0-9]+)_annotated\.csv$")
_ORIG_RE = re.compile(r"^orig_parsed_([a-z0-9]+)_([A-Z0-9]+)\.json$")
_PRE_SPLICE_RE = re.compile(r"^pre_splice_parsed_([a-z0-9]+)_([A-Z0-9]+)\.json$")
# Legacy daw dir: daw/{tag}/
_DAW_RE = re.compile(r"^([A-Z0-9]+)$")
# Legacy master: masters/{slug}_{tag}_master.mp3 or root {slug}_{tag}_master.mp3
_MASTER_RE = re.compile(r"^([a-z0-9]+)_([A-Z0-9]+)_master\.mp3$")
# Legacy cues: cues/cues_{slug}_{tag}.md
_CUES_MD_RE = re.compile(r"^cues_([a-z0-9]+)_([A-Z0-9]+)\.md$")
_CUES_MANIFEST_RE = re.compile(r"^cues_manifest_([A-Z0-9]+)\.json$")


def _discover_moves(workspace: str = ".") -> list[tuple[str, str]]:
    """Return a list of (src, dst) absolute path pairs for all legacy files found."""
    moves: list[tuple[str, str]] = []

    def _abs(rel: str) -> str:
        return os.path.normpath(os.path.join(workspace, rel))

    def _add(src_rel: str, dst_rel: str) -> None:
        src = _abs(src_rel)
        dst = _abs(dst_rel)
        if os.path.exists(src) and src != dst:
            moves.append((src, dst))

    # --- Root-level cast configs: cast_{slug}_{tag}.json ---
    for path in glob.glob(os.path.join(workspace, "cast_*.json")):
        m = _CAST_RE.match(os.path.basename(path))
        if m:
            slug, tag = m.group(1), m.group(2)
            _add(f"cast_{slug}_{tag}.json", f"configs/{slug}/cast_{tag}.json")

    # --- Root-level sfx configs: sfx_{slug}_{tag}.json ---
    for path in glob.glob(os.path.join(workspace, "sfx_*.json")):
        m = _SFX_RE.match(os.path.basename(path))
        if m:
            slug, tag = m.group(1), m.group(2)
            _add(f"sfx_{slug}_{tag}.json", f"configs/{slug}/sfx_{tag}.json")

    # --- parsed/ directory ---
    parsed_dir = os.path.join(workspace, "parsed")
    if os.path.isdir(parsed_dir):
        for fname in os.listdir(parsed_dir):
            m = _PARSED_RE.match(fname)
            if m:
                slug, tag = m.group(1), m.group(2)
                _add(f"parsed/parsed_{slug}_{tag}.json", f"parsed/{slug}/parsed_{tag}.json")
                continue
            m = _PARSED_CSV_RE.match(fname)
            if m:
                slug, tag = m.group(1), m.group(2)
                _add(f"parsed/parsed_{slug}_{tag}.csv", f"parsed/{slug}/parsed_{tag}.csv")
                continue
            m = _ANNOTATED_RE.match(fname)
            if m:
                slug, tag = m.group(1), m.group(2)
                _add(
                    f"parsed/parsed_{slug}_{tag}_annotated.csv",
                    f"parsed/{slug}/annotated_{tag}.csv",
                )
                continue
            m = _ORIG_RE.match(fname)
            if m:
                slug, tag = m.group(1), m.group(2)
                _add(f"parsed/orig_parsed_{slug}_{tag}.json", f"parsed/{slug}/orig_parsed_{tag}.json")
                continue
            m = _PRE_SPLICE_RE.match(fname)
            if m:
                slug, tag = m.group(1), m.group(2)
                _add(
                    f"parsed/pre_splice_parsed_{slug}_{tag}.json",
                    f"parsed/{slug}/pre_splice_parsed_{tag}.json",
                )

    # --- daw/ directory: daw/{tag}/ → daw/{slug}/{tag}/ ---
    daw_dir = os.path.join(workspace, "daw")
    if os.path.isdir(daw_dir):
        for entry in os.listdir(daw_dir):
            entry_path = os.path.join(daw_dir, entry)
            if not os.path.isdir(entry_path):
                continue
            # Legacy: daw/{tag}/ where {tag} looks like S01E01, V01C01, SP001 etc.
            if _DAW_RE.match(entry):
                # Infer slug from cast config at new or legacy location
                slug = _infer_slug_from_daw(workspace, entry)
                if slug:
                    src_dir = os.path.join(daw_dir, entry)
                    dst_dir = os.path.join(daw_dir, slug, entry)
                    if not os.path.exists(dst_dir):
                        # Add all files within the dir (shutil.move works on dirs)
                        moves.append((src_dir, dst_dir))

    # --- masters/ directory ---
    masters_dir = os.path.join(workspace, "masters")
    for search_dir, is_root in [(workspace, True), (masters_dir, False)]:
        if not os.path.isdir(search_dir) and not is_root:
            continue
        pattern = os.path.join(search_dir if not is_root else workspace, "*_master.mp3")
        for path in glob.glob(pattern):
            m = _MASTER_RE.match(os.path.basename(path))
            if m:
                slug, tag = m.group(1), m.group(2)
                prefix = "" if is_root else "masters/"
                _add(
                    f"{prefix}{slug}_{tag}_master.mp3",
                    f"masters/{slug}/{tag}_master.mp3",
                )

    # --- cues/ directory ---
    cues_dir = os.path.join(workspace, "cues")
    if os.path.isdir(cues_dir):
        for fname in os.listdir(cues_dir):
            m = _CUES_MD_RE.match(fname)
            if m:
                slug, tag = m.group(1), m.group(2)
                _add(f"cues/cues_{slug}_{tag}.md", f"cues/{slug}/cues_{tag}.md")
                continue
            m = _CUES_MANIFEST_RE.match(fname)
            if m:
                tag = m.group(1)
                # Need slug — look up from a cast config for this tag
                slug = _infer_slug_from_tag(workspace, tag)
                if slug:
                    _add(f"cues/cues_manifest_{tag}.json", f"cues/{slug}/cues_manifest_{tag}.json")

    return moves


def _infer_slug_from_tag(workspace: str, tag: str) -> str | None:
    """Try to infer show slug for a tag by looking at cast config filenames."""
    # Check new layout first
    for path in glob.glob(os.path.join(workspace, "configs", "*", f"cast_{tag}.json")):
        return os.path.basename(os.path.dirname(path))
    # Then legacy root layout
    for path in glob.glob(os.path.join(workspace, f"cast_*_{tag}.json")):
        m = _CAST_RE.match(os.path.basename(path))
        if m:
            return m.group(1)
    return None


def _infer_slug_from_daw(workspace: str, tag: str) -> str | None:
    """Infer slug for a daw/{tag}/ directory by cross-referencing cast configs."""
    return _infer_slug_from_tag(workspace, tag)


def _execute_moves(moves: list[tuple[str, str]], dry_run: bool = True) -> tuple[int, int]:
    """Move files/directories. Returns (moved, skipped) counts."""
    moved = skipped = 0
    for src, dst in moves:
        dst_dir = os.path.dirname(dst)
        if os.path.exists(dst):
            logger.info(f"  SKIP   {_rel(dst)} (already exists at target)")
            skipped += 1
            continue
        if dry_run:
            logger.info(f"  MOVE   {_rel(src)}  →  {_rel(dst)}")
            moved += 1
        else:
            os.makedirs(dst_dir, exist_ok=True)
            shutil.move(src, dst)
            logger.info(f"  MOVED  {_rel(src)}  →  {_rel(dst)}")
            moved += 1
    return moved, skipped


def _rel(path: str) -> str:
    """Return path relative to CWD for display."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def migrate(workspace: str = ".", dry_run: bool = True) -> int:
    """Discover and optionally execute workspace migration moves.

    Args:
        workspace: Root workspace directory (default: current directory).
        dry_run: If ``True``, prints what would move without touching files.

    Returns:
        Exit code (0 = success, 1 = nothing to migrate).
    """
    run_banner("XILU009", "migrate-workspace")
    moves = _discover_moves(workspace)
    if not moves:
        logger.info("Nothing to migrate — workspace already uses the normalized layout.")
        return 0

    mode = "DRY RUN — " if dry_run else ""
    logger.info(f"\n{mode}Workspace migration plan ({len(moves)} moves):\n")
    moved, skipped = _execute_moves(moves, dry_run=dry_run)

    if dry_run:
        logger.info(f"\n{moved} file(s) would be moved, {skipped} already at target.")
        logger.info("Run without --dry-run to execute.")
    else:
        logger.info(f"\n{moved} file(s) moved, {skipped} skipped.")
        logger.info("Migration complete. Run 'xil migrate-workspace --dry-run' to verify.")
    return 0


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-migrate-workspace",
        description="Migrate a pre-0.1.8 workspace to the normalized directory layout",
    )
    parser.add_argument(
        "--workspace", default=".",
        help="Workspace root directory (default: current directory)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview moves without touching files (default: enabled for safety)",
    )
    return parser


def main() -> int:
    configure_logging()
    args = get_parser().parse_args()
    return migrate(workspace=args.workspace, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
