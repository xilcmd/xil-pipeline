# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""xil-status — make-style staleness checker for episode pipeline artifacts.

An episode flows through an ordered chain of file "waypoints", each produced
from the previous one::

    Google Drive .gdoc → scripts/*.md → parsed/{slug}/parsed_{tag}.json
       → stems/{slug}/{tag}/*.mp3 → daw/{slug}/{tag}/*.wav → masters/{slug}/{tag}_*.mp3

This utility walks that chain and reports, per stage, whether the outputs are
up to date with their inputs — like ``make`` deciding whether a target needs
rebuilding.  A stage is:

  * MISSING — no output files exist yet,
  * STALE   — the newest input is newer than the newest output
    (``max(inputs) > max(outputs)``), i.e. the stage has not run since its
    input last changed,
  * OK      — the stage ran at or after its newest input.

The newest-output rule (rather than strict make's oldest-output) fits this
pipeline's incremental, content-hash-dedup builds: a produce/daw run only
rewrites the outputs that actually changed, so older reused outputs legitimately
coexist with fresh ones.  What matters is whether the stage ran *after* its input
changed, which the newest output captures.

Nothing is ever rebuilt: the tool only reports, then prints the exact ``xil``
commands needed to refresh any stale/missing stages.

The Google Drive source (.gdoc) is included as the chain root when found.  Its
directory defaults to ``$XIL_GDOC_DIR`` or ``/mnt/i/My Drive`` and can be
overridden with ``--gdoc-dir``; a missing mount warns rather than fails.

Exit codes: 0 when every stage is OK, 1 when any stage is STALE or MISSING,
2 on a usage/resolution error.

Usage::

    xil status --episode S01E01
    xil status S01E01 --show "Night Owls"
    xil status --all
    xil status --episode S01E01 --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import derive_paths, get_workspace_root, resolve_slug

logger = get_logger(__name__)

# Default Google Drive mount for the production .gdoc source documents.
_DEFAULT_GDOC_DIR = os.environ.get("XIL_GDOC_DIR", "/mnt/i/My Drive")

# Episode tag pattern, e.g. S01E01, S3E12.
_TAG_RE = re.compile(r"S\d+E\d+", re.IGNORECASE)

# Status constants.
_OK = "OK"
_STALE = "STALE"
_MISSING = "MISSING"
_NONE = "-"  # informational: stage has no inputs (e.g. gdoc dir absent)


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class StageStatus:
    """Freshness result for one pipeline stage."""

    name: str
    status: str
    newest_input: float | None  # mtime epoch, or None
    newest_output: float | None
    oldest_output: float | None  # the value the STALE decision compares against
    output_count: int
    note: str = ""
    refresh: str = ""  # suggested xil command (empty if none / OK)
    inputs_present: bool = True


# ── mtime helpers ─────────────────────────────────────────────────────────────


def _mtimes(paths: list[Path]) -> list[float]:
    """Return mtimes for every existing regular file in *paths* (dirs recursed)."""
    out: list[float] = []
    for p in paths:
        if p.is_dir():
            out.extend(f.stat().st_mtime for f in p.rglob("*") if f.is_file())
        elif p.is_file():
            out.append(p.stat().st_mtime)
    return out


def _fmt_time(mtime: float | None) -> str:
    if mtime is None:
        return "—"
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")


# ── Waypoint discovery ────────────────────────────────────────────────────────


def _gdoc_files(gdoc_dir: Path, tag: str) -> list[Path]:
    """Production .gdoc(s) for *tag*, e.g. ``S01E01_show.md.gdoc``."""
    try:
        if not gdoc_dir.is_dir():
            return []
    except OSError:
        return []
    return sorted(gdoc_dir.glob(f"{tag}*.gdoc")) + sorted(gdoc_dir.glob(f"*{tag}*.gdoc"))


def _script_files(root: Path, tag: str) -> list[Path]:
    """Imported production script(s) under scripts/ for *tag* (excludes revised_)."""
    scripts = root / "scripts"
    if not scripts.is_dir():
        return []
    return sorted(
        p for p in scripts.glob(f"*{tag}*.md") if not p.name.startswith("revised_")
    )


def _glob_in(path_str: str, pattern: str) -> list[Path]:
    """Glob *pattern* inside the directory named by *path_str* (a stems/daw dir)."""
    d = Path(path_str)
    return sorted(d.glob(pattern)) if d.is_dir() else []


def _master_files(root: Path, slug: str, tag: str) -> list[Path]:
    """All master MP3s for *tag*, across every known layout.

    XILP011 writes ``masters/{tag}_{slug}_{date}.mp3`` flat under ``masters/``
    (see XILP011_master_export.py); older/canonical variants live in a slug
    subdirectory or at the workspace root.  Collect them all and de-dup.
    """
    masters = root / "masters"
    found: list[Path] = []
    if masters.is_dir():
        found += masters.glob(f"{tag}_*.mp3")          # flat: {tag}_{slug}_{date}.mp3, {tag}_master.mp3
        sub = masters / slug
        if sub.is_dir():
            found += sub.glob(f"{tag}_*.mp3")           # slug-subdir variant
    legacy = root / f"{slug}_{tag}_master.mp3"          # pre-0.1.8 root layout
    if legacy.is_file():
        found.append(legacy)
    return sorted({p for p in found if p.is_file()})


# ── Stage evaluation ──────────────────────────────────────────────────────────


def _evaluate_stage(
    name: str,
    inputs: list[Path],
    outputs: list[Path],
    refresh: str,
    *,
    count: int | None = None,
) -> StageStatus:
    """Compute a stage's freshness from its input and output files.

    The STALE decision uses ``max(inputs) > max(outputs)`` — the stage is stale
    when its newest input is newer than its newest output, i.e. it has not run
    since the input last changed.  Newest-output (not strict make's oldest) is
    used so that older, dedup-reused outputs don't trigger false staleness.
    Pass *count* to report a different file tally in the FILES column (e.g. the
    stems stage counts MP3s but is judged against MP3s plus the manifest).
    """
    in_times = _mtimes(inputs)
    out_times = _mtimes(outputs)
    newest_in = max(in_times) if in_times else None
    newest_out = max(out_times) if out_times else None
    oldest_out = min(out_times) if out_times else None

    if not out_times:
        status = _MISSING
        suggested = refresh
    elif newest_in is not None and newest_out is not None and newest_in > newest_out:
        status = _STALE
        suggested = refresh
    else:
        status = _OK
        suggested = ""

    return StageStatus(
        name=name,
        status=status,
        newest_input=newest_in,
        newest_output=newest_out,
        oldest_output=oldest_out,
        output_count=count if count is not None else len(out_times),
        refresh=suggested,
        inputs_present=bool(in_times),
    )


def evaluate_episode(slug: str, tag: str, gdoc_dir: Path) -> list[StageStatus]:
    """Build and evaluate the full waypoint chain for one episode."""
    root = get_workspace_root()
    paths = derive_paths(slug, tag)

    gdocs = _gdoc_files(gdoc_dir, tag)
    scripts = _script_files(root, tag)
    parsed = [Path(paths["parsed"])]
    stems = _glob_in(paths["stems"], "*.mp3")
    stems_manifest = _glob_in(paths["stems"], "*_stem_manifest.json")
    daw = _glob_in(paths["daw"], "*.wav")
    masters = _master_files(root, slug, tag)

    # script refresh has no CLI equivalent — it is imported via xil-gui.
    script_refresh = "(re-import the production doc in xil-gui)"
    # Suggest the most recently modified script: with multiple drafts on disk
    # (e.g. *_v1, *_v2, *_v3), the newest is the one that triggered staleness
    # and the one the user most likely wants to (re)parse.
    newest_script = max(scripts, key=lambda p: p.stat().st_mtime) if scripts else None
    parse_refresh = (
        f"xil parse {newest_script.relative_to(root)} --episode {tag}"
        if newest_script
        else f"xil parse <script> --episode {tag}"
    )

    # The manifest is included in the stems stage's OUTPUTS (not daw's inputs):
    # the producer rewrites it on every run, including no-op runs where all stems
    # are dedup-reused, so it is the only signal that advances when re-producing
    # an unchanged episode — without it, a stems stage made stale by a re-parse
    # could never be cleared.  It must NOT count as daw's INPUT, though: daw
    # consumes the stem audio, so a no-op manifest bump (produce re-run that
    # changed nothing) must not invalidate an up-to-date daw.  Hence daw is judged
    # against the stem MP3s only.
    stems_outputs = stems + stems_manifest

    stages = [
        _evaluate_stage("source", [], gdocs, ""),
        _evaluate_stage("script", gdocs, scripts, script_refresh),
        _evaluate_stage("parsed", scripts, parsed, parse_refresh),
        _evaluate_stage(
            "stems", parsed, stems_outputs, f"xil produce --episode {tag}",
            count=len(stems),
        ),
        _evaluate_stage("daw", stems, daw, f"xil daw --episode {tag}"),
        _evaluate_stage("master", daw, masters, f"xil master --episode {tag}"),
    ]

    # The source stage is informational: a missing/empty gdoc dir is not a failure.
    src = stages[0]
    if not gdocs:
        src.status = _NONE
        try:
            _gdoc_is_dir = gdoc_dir.is_dir()
        except OSError:
            _gdoc_is_dir = False
        src.note = "no gdoc dir" if not _gdoc_is_dir else "no source doc"
        src.refresh = ""

    return stages


# ── Reporting ─────────────────────────────────────────────────────────────────


def _worst(stages: list[StageStatus]) -> str:
    """Worst status across stages (ignoring the informational source stage)."""
    relevant = [s.status for s in stages if s.name != "source"]
    if _MISSING in relevant:
        return _MISSING
    if _STALE in relevant:
        return _STALE
    return _OK


def _print_episode(slug: str, tag: str, stages: list[StageStatus]) -> None:
    logger.info(f"Episode {tag} (show: {slug})")
    logger.info("")
    logger.info(
        f"  {'STAGE':<9} {'STATUS':<8} {'NEWEST INPUT':<18} {'NEWEST OUTPUT':<18} FILES"
    )
    for s in stages:
        in_str = _fmt_time(s.newest_input)
        out_str = f"({s.note})" if s.note else _fmt_time(s.newest_output)
        count = "—" if (s.name == "source" and s.status == _NONE) else str(s.output_count)
        marker = ""
        if s.status == _STALE:
            # Newest output (shown in the column) is the deciding value: the
            # stage has not run since its input last changed.
            marker = "   ← input is newer (stage not re-run)"
        elif s.status == _MISSING:
            marker = "   ← not built yet"
        logger.info(
            f"  {s.name:<9} {s.status:<8} {in_str:<18} {out_str:<18} {count}{marker}"
        )

    refreshes = [s.refresh for s in stages if s.refresh]
    if refreshes:
        logger.info("")
        logger.info("Stale/missing — refresh with:")
        for cmd in refreshes:
            logger.info(f"  {cmd}")


def _emit_json(slug: str, tag: str, stages: list[StageStatus]) -> None:
    payload = {
        "show": slug,
        "episode": tag,
        "overall": _worst(stages),
        "stages": [
            {
                "name": s.name,
                "status": s.status,
                "newest_input": s.newest_input,
                "newest_output": s.newest_output,
                "oldest_output": s.oldest_output,
                "output_count": s.output_count,
                "note": s.note,
                "refresh": s.refresh,
            }
            for s in stages
        ],
    }
    print(json.dumps(payload, indent=2))


# ── --all enumeration ─────────────────────────────────────────────────────────


def _discover_tags(slug: str) -> list[str]:
    """Find every episode tag for *slug* across parsed/stems/daw/masters."""
    root = get_workspace_root()
    tags: set[str] = set()

    parsed_dir = root / "parsed" / slug
    if parsed_dir.is_dir():
        for p in parsed_dir.glob("parsed_*.json"):
            if m := _TAG_RE.search(p.stem):
                tags.add(m.group(0).upper())

    for sub in ("stems", "daw"):
        base = root / sub / slug
        if base.is_dir():
            for child in base.iterdir():
                if child.is_dir() and (m := _TAG_RE.fullmatch(child.name)):
                    tags.add(m.group(0).upper())

    masters_dir = root / "masters"
    if masters_dir.is_dir():
        # flat XILP011 output: {tag}_{slug}_{date}.mp3 — scope by slug to avoid
        # pulling in other shows' masters from the shared masters/ directory.
        for p in masters_dir.glob(f"*_{slug}_*.mp3"):
            if m := _TAG_RE.search(p.stem):
                tags.add(m.group(0).upper())
        sub = masters_dir / slug
        if sub.is_dir():
            for p in sub.glob("*.mp3"):
                if m := _TAG_RE.search(p.stem):
                    tags.add(m.group(0).upper())

    return sorted(tags)


def _print_all(slug: str, tags: list[str], gdoc_dir: Path) -> int:
    """Print one summary row per episode. Returns process exit code."""
    if not tags:
        logger.info(f"No episodes found for show '{slug}'.")
        return 0

    logger.info(f"Show: {slug} — {len(tags)} episode(s)")
    logger.info("")
    logger.info(f"  {'EPISODE':<10} {'STATUS':<8} NEXT STEP")

    exit_code = 0
    for tag in tags:
        stages = evaluate_episode(slug, tag, gdoc_dir)
        worst = _worst(stages)
        if worst != _OK:
            exit_code = 1
        next_step = next((s.refresh for s in stages if s.refresh), "")
        logger.info(f"  {tag:<10} {worst:<8} {next_step}")

    return exit_code


# ── Main ──────────────────────────────────────────────────────────────────────


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-status."""
    parser = argparse.ArgumentParser(
        prog="xil-status",
        description=(
            "Make-style staleness checker for episode pipeline artifacts. "
            "Reports, per stage, whether outputs are up to date with their inputs, "
            "and prints the xil commands needed to refresh anything stale. "
            "Nothing is rebuilt."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  xil status --episode S01E01\n"
            '  xil status S01E01 --show "Night Owls"\n'
            "  xil status --all\n"
            "  xil status --episode S01E01 --json\n"
        ),
    )
    parser.add_argument(
        "episode",
        nargs="?",
        metavar="TAG",
        help="Episode tag to check (e.g. S01E01). Omit with --all.",
    )
    parser.add_argument(
        "--episode", "-e",
        dest="episode_flag",
        default=None,
        metavar="TAG",
        help="Episode tag (alternative to the positional argument)",
    )
    parser.add_argument(
        "--show", "-s",
        default=None,
        metavar="SHOW",
        help="Show name or slug (default: resolved from project.json / XIL_PROJECTROOT)",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="Check every episode of the show (summary row per episode)",
    )
    parser.add_argument(
        "--gdoc-dir",
        default=_DEFAULT_GDOC_DIR,
        metavar="DIR",
        help=f"Google Drive dir holding the source .gdoc (default: {_DEFAULT_GDOC_DIR})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit results as JSON (single-episode mode only)",
    )
    return parser


def main() -> None:
    """CLI entry point for episode pipeline staleness checking."""
    configure_logging()
    args = get_parser().parse_args()

    slug = resolve_slug(args.show)
    gdoc_dir = Path(args.gdoc_dir)
    try:
        gdoc_available = gdoc_dir.is_dir()
    except OSError:
        gdoc_available = False
    if not gdoc_available:
        logger.warning(f"Google Drive dir not available: {gdoc_dir} — skipping source check.")

    if args.all:
        if args.json:
            logger.error("--json is not supported with --all.")
            sys.exit(2)
        tags = _discover_tags(slug)
        sys.exit(_print_all(slug, tags, gdoc_dir))

    tag = args.episode_flag or args.episode
    if not tag:
        logger.error("Provide an episode tag (e.g. S01E01) or use --all.")
        sys.exit(2)

    stages = evaluate_episode(slug, tag, gdoc_dir)

    if args.json:
        _emit_json(slug, tag, stages)
    else:
        _print_episode(slug, tag, stages)

    sys.exit(0 if _worst(stages) == _OK else 1)


if __name__ == "__main__":
    main()
