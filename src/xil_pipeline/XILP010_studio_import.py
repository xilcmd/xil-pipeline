"""XILP010 — ElevenLabs Studio Export Importer.

Extracts dialogue and direction stems from an ElevenLabs Studio export ZIP
and renames them to the pipeline's stem naming convention, placing them in
``stems/{TAG}/`` ready for downstream tools (XILP003, XILP005).

ElevenLabs Studio exports one MP3 per parsed entry (including headers and
directions), sequentially numbered as ``NNN_Chapter N.mp3``.  Dialogue
entries are always extracted.  Direction entries (SFX, MUSIC, BEAT) can be
included with ``--gen-sfx``, ``--gen-music``, ``--gen-beats``, or ``--all``.

Usage::

    python XILP010_studio_import.py --episode S02E02 \\
        --zip "ElevenLabs_exports/export.zip" --dry-run
    python XILP010_studio_import.py --episode S02E02 \\
        --zip "ElevenLabs_exports/export.zip" --gen-sfx --gen-music --gen-beats
    python XILP010_studio_import.py --episode S02E02 --zip "..." --all
    python XILP010_studio_import.py --episode S02E02 --zip "..." --force
"""

import argparse
import json
import os
import re
import zipfile

from xil_pipeline.models import derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner
from xil_pipeline.XILP007_stem_migrator import make_stem_name

SCRIPT_NAME = "XILP010 · Studio Import"


def _parse_zip_seq(filename: str) -> int | None:
    """Extract the sequence number from a ZIP entry filename.

    ElevenLabs Studio exports files as ``NNN_Chapter N.mp3``.

    Args:
        filename: The ZIP member filename (e.g. ``"042_Chapter 1.mp3"``).

    Returns:
        The integer sequence number, or ``None`` if the filename doesn't
        match the expected pattern.
    """
    basename = os.path.basename(filename)
    m = re.match(r"^(\d+)_", basename)
    if m:
        return int(m.group(1))
    return None


def extract_stems(
    zip_path: str,
    parsed: dict,
    stems_dir: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    include_dtypes: set[str] | None = None,
) -> dict:
    """Extract and rename stems from an ElevenLabs Studio export ZIP.

    Args:
        zip_path: Path to the Studio export ZIP file.
        parsed: Parsed episode dict (from ``parse_script()``).
        stems_dir: Target directory for extracted stems.
        dry_run: If ``True``, print the plan without writing files.
        force: If ``True``, overwrite existing stems on disk.
        include_dtypes: Set of ``direction_type`` values to extract
            (e.g. ``{"SFX", "MUSIC", "BEAT"}``).  Dialogue entries are
            always extracted.  Headers are always skipped.  An empty set
            or ``None`` extracts dialogue only.

    Returns:
        A stats dict with counts: ``extracted``, ``skipped_exists``,
        ``skipped_type``, ``skipped_header``, ``missing_seq``.
    """
    if include_dtypes is None:
        include_dtypes = set()
    entries_by_seq = {e["seq"]: e for e in parsed["entries"]}

    stats = {
        "extracted": 0,
        "skipped_exists": 0,
        "skipped_type": 0,
        "skipped_header": 0,
        "missing_seq": 0,
    }

    if not dry_run:
        os.makedirs(stems_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = sorted(zf.namelist())
        for member in members:
            if not member.lower().endswith(".mp3"):
                continue

            seq = _parse_zip_seq(member)
            if seq is None:
                continue

            entry = entries_by_seq.get(seq)
            if entry is None:
                stats["missing_seq"] += 1
                print(f"  [MISSING]  {member}  → seq {seq} not in parsed JSON")
                continue

            entry_type = entry["type"]
            text_preview = (entry.get("text") or "")[:50]
            direction_type = entry.get("direction_type") or ""

            # Always skip headers — no audio value
            if entry_type in ("section_header", "scene_header"):
                stats["skipped_header"] += 1
                print(f"  [HEADER]   {member}  — {entry_type}: {text_preview}")
                continue

            # Skip directions whose direction_type is not in include_dtypes
            if entry_type == "direction" and direction_type not in include_dtypes:
                stats["skipped_type"] += 1
                label = f"{direction_type}: " if direction_type else ""
                print(f"  [SKIP]     {member}  — {label}{text_preview}")
                continue

            stem_name = make_stem_name(entry)
            dest = os.path.join(stems_dir, stem_name)

            if os.path.exists(dest) and not force:
                stats["skipped_exists"] += 1
                print(f"  [EXISTS]   {member}  → {stem_name}")
                continue

            marker = "EXTRACT" if not dry_run else "DRY-RUN"
            speaker = entry.get("speaker") or "sfx"
            print(
                f"  [{marker}]  {member}  → {stem_name}"
                f"  ({speaker}: {text_preview})"
            )

            if not dry_run:
                data = zf.read(member)
                with open(dest, "wb") as f:
                    f.write(data)

            stats["extracted"] += 1

    return stats


def print_summary(stats: dict, dry_run: bool = False) -> None:
    """Print a summary of the extraction results.

    Args:
        stats: Stats dict from :func:`extract_stems`.
        dry_run: Whether the run was a dry run.
    """
    mode = "DRY-RUN" if dry_run else "COMPLETE"
    print(f"\n{'─'*50}")
    print(f"  SUMMARY ({mode})")
    print(f"{'─'*50}")
    print(f"  Extracted:       {stats['extracted']:>4}")
    print(f"  Skipped (exist): {stats['skipped_exists']:>4}")
    print(f"  Skipped (type):  {stats['skipped_type']:>4}")
    print(f"  Skipped (header):{stats['skipped_header']:>4}")
    if stats["missing_seq"]:
        print(f"  Missing seq:     {stats['missing_seq']:>4}  ⚠")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Import ElevenLabs Studio export ZIP into pipeline stems.",
    )
    parser.add_argument(
        "--episode",
        required=True,
        help="Episode tag (e.g. S02E02) — derives parsed JSON and stems dir",
    )
    parser.add_argument("--show", default=None, help="Show name override (default: from project.json)")
    parser.add_argument(
        "--zip",
        required=True,
        dest="zip_path",
        help="Path to the ElevenLabs Studio export ZIP file",
    )
    parser.add_argument(
        "--parsed",
        help="Override parsed JSON path (default: parsed/parsed_<slug>_{TAG}.json)",
    )
    parser.add_argument(
        "--stems-dir",
        help="Override stems output directory (default: stems/{TAG})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show extraction plan without writing files",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing stems on disk",
    )
    parser.add_argument(
        "--gen-sfx",
        action="store_true",
        help="Include SFX direction entries",
    )
    parser.add_argument(
        "--gen-music",
        action="store_true",
        help="Include MUSIC direction entries",
    )
    parser.add_argument(
        "--gen-beats",
        action="store_true",
        help="Include BEAT direction entries",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_types",
        help="Include all direction types (SFX, MUSIC, BEAT, AMBIENCE)",
    )
    args = parser.parse_args()

    # Build set of included direction types
    include_dtypes: set[str] = set()
    if args.gen_sfx or args.all_types:
        include_dtypes.add("SFX")
    if args.gen_music or args.all_types:
        include_dtypes.add("MUSIC")
    if args.gen_beats or args.all_types:
        include_dtypes.add("BEAT")
    if args.all_types:
        include_dtypes.add("AMBIENCE")

    tag = args.episode
    slug = resolve_slug(args.show)
    p = derive_paths(slug, tag)
    parsed_path = args.parsed or p["parsed"]
    stems_dir = args.stems_dir or f"stems/{tag}"

    with run_banner(SCRIPT_NAME):
        # Validate inputs
        if not os.path.isfile(args.zip_path):
            print(f"ERROR: ZIP file not found: {args.zip_path}")
            return
        if not os.path.isfile(parsed_path):
            print(f"ERROR: Parsed JSON not found: {parsed_path}")
            return

        with open(parsed_path) as f:
            parsed = json.load(f)

        total = len(parsed["entries"])
        dialogue = sum(1 for e in parsed["entries"] if e["type"] == "dialogue")
        direction = sum(1 for e in parsed["entries"] if e["type"] == "direction")
        headers = total - dialogue - direction

        print(f"  Episode:    {tag}")
        print(f"  ZIP:        {args.zip_path}")
        print(f"  Parsed:     {parsed_path}  ({total} entries)")
        print(f"  Stems dir:  {stems_dir}")
        print(f"  Entries:    {dialogue} dialogue, {direction} directions, {headers} headers")
        mode_parts = []
        if args.dry_run:
            mode_parts.append("dry-run")
        if args.force:
            mode_parts.append("force")
        if include_dtypes:
            mode_parts.append(f"include: {', '.join(sorted(include_dtypes))}")
        if mode_parts:
            print(f"  Mode:       {', '.join(mode_parts)}")
        print()

        stats = extract_stems(
            args.zip_path,
            parsed,
            stems_dir,
            dry_run=args.dry_run,
            force=args.force,
            include_dtypes=include_dtypes,
        )

        print_summary(stats, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
