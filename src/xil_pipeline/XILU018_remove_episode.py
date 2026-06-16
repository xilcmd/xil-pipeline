# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""xil-remove-episode — remove workspace files for a single episode.

Deletes every workspace artifact belonging to the named episode while leaving
the source production script untouched.  The following are removed:

  * configs/{slug}/cast_{tag}.json
  * configs/{slug}/sfx_{tag}.json
  * parsed/{slug}/parsed_{tag}.json  (and .csv, orig_, pre_splice_, stem_verify_)
  * cues/{slug}/cues_{tag}.md  +  cues_manifest_{tag}.json
  * stems/{slug}/{tag}/          (directory)
  * daw/{slug}/{tag}/            (directory)
  * masters/{slug}/{tag}_*.mp3   (glob — date-tagged masters from xil-master)
  * posts/{slug}/{tag}_posts.md
  * voice_samples/{tag}/         (directory — from xil-sample)
  * legacy root files: cast_{slug}_{tag}.json, sfx_{slug}_{tag}.json
  * legacy parsed files in parsed/ (flat naming, pre-0.1.8 layout)

Shared assets (SFX/, logs/) and the source script (scripts/) are never touched.

Usage::

    xil remove-episode S01E01 --dry-run
    xil remove-episode S01E01 --yes
    xil remove-episode S01E01 --show "Night Owls" --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root, resolve_slug

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


def _collect(slug: str, tag: str) -> list[RemovalItem]:
    root = get_workspace_root()
    items: list[RemovalItem] = []

    def _file(p: Path, label: str = "") -> None:
        items.append(_File(p, label))

    def _dir(p: Path, label: str = "") -> None:
        if p.exists():
            items.append(_Dir(p, label))

    # ── Normalized layout (0.1.8+) ──────────────────────────────────────────
    cfg = root / "configs" / slug
    _file(cfg / f"cast_{tag}.json")
    _file(cfg / f"sfx_{tag}.json")

    psd = root / "parsed" / slug
    for prefix in ("parsed", "orig_parsed", "pre_splice_parsed", "stem_verify"):
        _file(psd / f"{prefix}_{tag}.json")
    _file(psd / f"parsed_{tag}.csv")
    _file(psd / f"annotated_{tag}.csv")

    cues = root / "cues" / slug
    _file(cues / f"cues_{tag}.md")
    _file(cues / f"cues_manifest_{tag}.json")

    _dir(root / "stems" / slug / tag)
    _dir(root / "daw" / slug / tag)

    masters = root / "masters" / slug
    if masters.is_dir():
        for p in sorted(masters.glob(f"{tag}_*.mp3")):
            _file(p, "master")
    # canonical name without date suffix
    _file(masters / f"{tag}_master.mp3", "master")

    _file(root / "posts" / slug / f"{tag}_posts.md")

    # voice_samples/{tag}/ — per-tag, not per-show
    _dir(root / "voice_samples" / tag)

    # ── Legacy layout (pre-0.1.8) ────────────────────────────────────────────
    _file(root / f"cast_{slug}_{tag}.json", "legacy root")
    _file(root / f"sfx_{slug}_{tag}.json", "legacy root")

    legacy_psd = root / "parsed"
    for prefix in ("parsed", "orig_parsed", "pre_splice_parsed", "annotated"):
        _file(legacy_psd / f"{prefix}_{slug}_{tag}.json", "legacy parsed")
    _file(legacy_psd / f"parsed_{slug}_{tag}.csv", "legacy parsed")

    # legacy daw — flat tag directory under daw/ (no slug subdirectory)
    legacy_daw = root / "daw" / tag
    if legacy_daw.exists() and legacy_daw != root / "daw" / slug / tag:
        _dir(legacy_daw, "legacy daw")

    # legacy master at root
    _file(root / f"{slug}_{tag}_master.mp3", "legacy master")

    # Deduplicate by path (masters glob may overlap with canonical name)
    seen: set[Path] = set()
    deduped: list[RemovalItem] = []
    for item in items:
        if item.path not in seen:
            seen.add(item.path)
            deduped.append(item)

    return deduped


# ── Formatting ───────────────────────────────────────────────────────────────


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


def _report(items: list[RemovalItem], slug: str, tag: str, dry_run: bool) -> tuple[int, int]:
    """Print the removal plan. Returns (total_files, total_bytes)."""
    root = get_workspace_root()

    present = [i for i in items if i.path.exists()]
    total_files = sum(i.file_count() for i in present)
    total_bytes = sum(i.total_bytes() for i in present)

    if not present:
        logger.info(f"Nothing found for episode '{tag}' (show '{slug}') — workspace is already clean.")
        return 0, 0

    action = "Would remove" if dry_run else "Removing"
    logger.info(f"{action} episode '{tag}' (show '{slug}'):")
    logger.info("")

    for item in present:
        fc = item.file_count()
        try:
            rel = item.path.relative_to(root)
        except ValueError:
            rel = item.path
        size_str = _fmt_bytes(item.total_bytes())
        tag_str = f"  ({item.label})" if item.label else ""
        if isinstance(item, _Dir):
            logger.info(f"  [DIR]  {rel}/  — {fc} file(s), {size_str}{tag_str}")
        else:
            logger.info(f"  [FILE] {rel}  — {size_str}{tag_str}")

    logger.info("")
    logger.info(f"Total: {total_files} file(s), {_fmt_bytes(total_bytes)}")

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


# ── Main ─────────────────────────────────────────────────────────────────────


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-remove-episode."""
    parser = argparse.ArgumentParser(
        prog="xil-remove-episode",
        description=(
            "Remove all workspace files for a single episode. "
            "The source production script is never touched. "
            "Shared assets (SFX/, logs/) are never touched."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  xil remove-episode S01E01 --dry-run\n"
            "  xil remove-episode S01E01 --yes\n"
            '  xil remove-episode S01E01 --show "Night Owls" --dry-run\n'
        ),
    )
    parser.add_argument(
        "episode",
        metavar="TAG",
        help="Episode tag to remove (e.g. S01E01)",
    )
    parser.add_argument(
        "--show", "-s",
        default=None,
        metavar="SHOW",
        help="Show name or slug (default: resolved from project.json / XIL_PROJECTROOT)",
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
    return parser


def main() -> None:
    configure_logging()
    args = get_parser().parse_args()

    slug = resolve_slug(args.show)
    tag = args.episode
    items = _collect(slug, tag)

    total_files, total_bytes = _report(items, slug, tag, dry_run=args.dry_run)

    if args.dry_run:
        if total_files > 0:
            logger.info("")
            logger.info("Dry run — nothing deleted. Run without --dry-run to remove.")
        sys.exit(0)

    if total_files == 0:
        sys.exit(0)

    if not args.yes:
        logger.info("")
        confirm = input(
            f'⚠️  This will permanently delete {total_files} file(s) ({_fmt_bytes(total_bytes)}) '
            f'for episode "{tag}" (show "{slug}").\n'
            f'    Type "{tag}" to confirm (or Ctrl-C to abort): '
        ).strip()
        if confirm != tag:
            logger.info("Aborted — input did not match. Nothing deleted.")
            sys.exit(1)

    logger.info("")
    removed = _delete(items)
    logger.info("")
    logger.info(f"✓ Removed {removed} file(s) for episode '{tag}' (show '{slug}').")


if __name__ == "__main__":
    main()
