# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""xil-remove-show — remove all workspace files for a given show.

Deletes every workspace artifact belonging to the named show:
  * configs/{slug}/
  * parsed/{slug}/
  * stems/{slug}/
  * daw/{slug}/
  * masters/{slug}/
  * cues/{slug}/
  * posts/{slug}/
  * legacy root files: cast_{slug}_*.json, sfx_{slug}_*.json
  * legacy parsed files: parsed/parsed_{slug}_*.json, parsed/annotated_{slug}_*.json,
    parsed/pre_splice_parsed_{slug}_*.json, parsed/orig_parsed_{slug}_*.json
  * .active_show — cleared if it points to the removed show
  * scripts/*_{slug}_*.md — only with --include-scripts

Shared assets (SFX/, logs/) are never touched.

Usage::

    xil remove-show mypodcast --dry-run
    xil remove-show mypodcast --yes
    xil remove-show "My Podcast" --dry-run
    xil remove-show mypodcast --include-scripts --dry-run
    xil remove-show mypodcast --include-scripts --yes
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root, show_slug

logger = get_logger(__name__)

# ── Data model ──────────────────────────────────────────────────────────────


@dataclass
class _Dir:
    path: Path
    label: str = ""

    def file_count(self) -> int:
        if not self.path.exists():
            return 0
        return sum(1 for _ in self.path.rglob("*") if _.is_file())

    def total_bytes(self) -> int:
        if not self.path.exists():
            return 0
        return sum(f.stat().st_size for f in self.path.rglob("*") if f.is_file())


@dataclass
class _File:
    path: Path
    label: str = ""

    def file_count(self) -> int:
        return 1 if self.path.exists() else 0

    def total_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0


RemovalItem = _Dir | _File


# ── Collection ───────────────────────────────────────────────────────────────


def _collect(slug: str, include_scripts: bool) -> list[RemovalItem]:
    root = get_workspace_root()
    items: list[RemovalItem] = []

    # Per-show directories (normalized layout)
    for category in ("configs", "parsed", "stems", "daw", "masters", "cues", "posts"):
        d = root / category / slug
        if d.exists():
            items.append(_Dir(d))

    # Legacy root cast/sfx configs
    for pattern in (f"cast_{slug}_*.json", f"sfx_{slug}_*.json"):
        for p in sorted(root.glob(pattern)):
            items.append(_File(p, "legacy root"))

    # Legacy flat parsed files
    parsed_dir = root / "parsed"
    if parsed_dir.is_dir():
        for prefix in ("parsed", "annotated", "pre_splice_parsed", "orig_parsed"):
            for p in sorted(parsed_dir.glob(f"{prefix}_{slug}_*.json")):
                items.append(_File(p, "legacy parsed"))

    # Scripts (opt-in)
    if include_scripts:
        scripts_dir = root / "scripts"
        if scripts_dir.is_dir():
            for p in sorted(scripts_dir.glob("*.md")):
                if f"_{slug}_" in p.name or p.name.endswith(f"_{slug}.md"):
                    items.append(_File(p, "script"))

    # .active_show — only if it points to this show
    active_file = root / ".active_show"
    if active_file.exists():
        try:
            current = active_file.read_text(encoding="utf-8").strip()
            if current == slug:
                items.append(_File(active_file, ".active_show"))
        except OSError:
            pass

    return items


# ── Formatting ───────────────────────────────────────────────────────────────


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _report(items: list[RemovalItem], slug: str, dry_run: bool) -> tuple[int, int]:
    """Print the removal plan. Returns (total_files, total_bytes)."""
    root = get_workspace_root()

    total_files = sum(i.file_count() for i in items)
    total_bytes = sum(i.total_bytes() for i in items)

    if not items:
        logger.info(f"Nothing found for show '{slug}' — workspace is already clean.")
        return 0, 0

    action = "Would remove" if dry_run else "Removing"
    logger.info(f"{action} show '{slug}':")
    logger.info("")

    for item in items:
        fc = item.file_count()
        if fc == 0:
            continue
        try:
            rel = item.path.relative_to(root)
        except ValueError:
            rel = item.path
        size_str = _fmt_bytes(item.total_bytes())
        tag = f"  ({item.label})" if item.label else ""
        if isinstance(item, _Dir):
            logger.info(f"  [DIR]  {rel}/  — {fc} file(s), {size_str}{tag}")
        else:
            logger.info(f"  [FILE] {rel}  — {size_str}{tag}")

    # Also list dirs/files that exist on-disk but are empty (zero files)
    empty = [i for i in items if i.file_count() == 0 and i.path.exists()]
    if empty:
        logger.info("")
        for item in empty:
            try:
                rel = item.path.relative_to(root)
            except ValueError:
                rel = item.path
            kind = "[DIR] " if isinstance(item, _Dir) else "[FILE]"
            logger.info(f"  {kind} {rel}  — empty")

    logger.info("")
    logger.info(
        f"Total: {total_files} file(s), {_fmt_bytes(total_bytes)}"
        + (" across all matched items" if total_files else "")
    )

    return total_files, total_bytes


# ── Deletion ─────────────────────────────────────────────────────────────────


def _delete(items: list[RemovalItem]) -> int:
    """Delete all items. Returns count of files removed."""
    removed = 0
    for item in items:
        if not item.path.exists():
            continue
        if isinstance(item, _Dir):
            fc = item.file_count()
            shutil.rmtree(item.path)
            removed += fc
            logger.info(f"  removed {item.path}")
        else:
            item.path.unlink()
            removed += 1
            logger.info(f"  removed {item.path}")
    return removed


# ── Resolution ───────────────────────────────────────────────────────────────


def _resolve_slug(name_or_slug: str) -> str:
    """Return the slug for the given show name or slug.

    Accepts either a raw slug ('mypodcast') or a show name ('My Podcast').
    Validates that configs/{slug}/project.json exists in the workspace.
    Falls back to slugifying the input if no match is found but at least the
    configs/{slug}/ directory exists.
    """
    root = get_workspace_root()
    candidate = show_slug(name_or_slug)

    # Direct slug hit
    if (root / "configs" / candidate / "project.json").exists():
        return candidate
    if (root / "configs" / candidate).is_dir():
        return candidate

    # Check all shows by reading project.json show names
    configs_dir = root / "configs"
    if configs_dir.is_dir():
        for slug_dir in sorted(configs_dir.iterdir()):
            pj = slug_dir / "project.json"
            if pj.exists():
                try:
                    import json
                    data = json.loads(pj.read_text(encoding="utf-8"))
                    if show_slug(data.get("show", "")) == candidate:
                        return slug_dir.name
                except Exception:
                    pass

    # Also accept the slug even without a project.json (legacy workspaces with
    # root cast configs but no configs/ directory).
    has_legacy = bool(
        list((root).glob(f"cast_{candidate}_*.json"))
        or (root / "parsed" / candidate).exists()
        or (root / "stems" / candidate).exists()
    )
    if has_legacy:
        return candidate

    return candidate  # best-effort; caller will warn if nothing found


# ── Main ─────────────────────────────────────────────────────────────────────


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-remove-show."""
    parser = argparse.ArgumentParser(
        prog="xil-remove-show",
        description=(
            "Remove all workspace files for a given show. "
            "Shared assets (SFX/, logs/) are never touched."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  xil remove-show mypodcast --dry-run\n"
            "  xil remove-show mypodcast --yes\n"
            '  xil remove-show "My Podcast" --dry-run\n'
            "  xil remove-show mypodcast --include-scripts --yes\n"
        ),
    )
    parser.add_argument(
        "show",
        metavar="SHOW",
        help="Show name or slug to remove (e.g. mypodcast or 'My Podcast')",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be removed without deleting anything",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip the confirmation prompt",
    )
    parser.add_argument(
        "--include-scripts",
        action="store_true",
        help=(
            "Also remove scripts/*_{slug}_*.md files whose filename contains the show slug "
            "(caution: source material)"
        ),
    )
    return parser


def main() -> None:
    configure_logging()
    args = get_parser().parse_args()

    slug = _resolve_slug(args.show)
    items = _collect(slug, args.include_scripts)

    total_files, total_bytes = _report(items, slug, dry_run=args.dry_run)

    if args.dry_run:
        if total_files > 0 or any(i.path.exists() for i in items):
            logger.info("")
            logger.info("Dry run — nothing deleted. Run without --dry-run to remove.")
        sys.exit(0)

    if total_files == 0 and not any(i.path.exists() for i in items):
        sys.exit(0)

    if not args.yes:
        logger.info("")
        confirm = input(
            f'⚠️  This will permanently delete {total_files} file(s) ({_fmt_bytes(total_bytes)}) '
            f'for show "{slug}".\n'
            f'    Type "{slug}" to confirm (or Ctrl-C to abort): '
        ).strip()
        if confirm != slug:
            logger.info("Aborted — input did not match. Nothing deleted.")
            sys.exit(1)

    logger.info("")
    removed = _delete(items)
    logger.info("")
    logger.info(f"✓ Removed {removed} file(s) for show '{slug}'.")


if __name__ == "__main__":
    main()
