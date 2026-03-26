#!/usr/bin/env python3
"""XILU006 — Splice Parsed JSON Utility.

Insert entries into (or delete entries from) a parsed episode JSON file,
with automatic seq renumbering.  Supports sourcing new entries from another
parsed JSON by seq range, or from a standalone JSON array file.

After splicing, run ``XILP007_stem_migrator.py`` with ``--orig-prefix pre_splice_``
to migrate stems to the new seq numbers, then ``XILP002_producer.py`` to generate
TTS for the newly inserted entries.

Usage::

    python XILU006_splice_parsed.py --episode S02E03 --insert-after 322 \\
        --from-parsed parsed/parsed_the413_S02E02.json --from-seq-range 232-233 \\
        --section post-interview --dry-run

    python XILU006_splice_parsed.py --episode S02E03 --delete-seq-range 100-105

    python XILU006_splice_parsed.py --episode S02E03 --insert-after 322 \\
        --from-json new_entries.json

No ElevenLabs API calls are made — this utility is safe to run freely.
"""

import argparse
import copy
import json
import os

from xil_pipeline.models import derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner

# ── Core functions (importable, no file I/O) ─────────────────────────────────


def renumber_entries(entries: list[dict]) -> list[dict]:
    """Assign contiguous seq 1..N to positive-seq entries.  Preamble (seq<=0) unchanged.

    Returns a new list — does not mutate the input.
    """
    result = []
    for e in entries:
        result.append(copy.deepcopy(e))
    seq = 1
    for e in result:
        if e["seq"] > 0:
            e["seq"] = seq
            seq += 1
    return result


def extract_seq_range(entries: list[dict], start: int, end: int) -> list[dict]:
    """Extract entries within [start, end] inclusive.  Returns deep copies."""
    return [copy.deepcopy(e) for e in entries if start <= e["seq"] <= end]


def splice_entries(
    entries: list[dict],
    insert_after_seq: int,
    new_entries: list[dict],
    section_override: str | None = None,
    scene_override: str | None = None,
) -> list[dict]:
    """Insert *new_entries* after the entry with *insert_after_seq*, renumber all.

    Preamble entries (seq <= 0) are never renumbered.  New entries inherit
    ``section`` and ``scene`` from the insertion point unless overrides are given.

    Raises:
        ValueError: If *insert_after_seq* is <= 0 (preamble zone) or not found.
    """
    if insert_after_seq <= 0:
        raise ValueError(f"Cannot insert after seq {insert_after_seq} (preamble zone)")

    preamble = [copy.deepcopy(e) for e in entries if e["seq"] <= 0]
    body = [copy.deepcopy(e) for e in entries if e["seq"] > 0]

    # Find insertion index
    insert_idx = None
    anchor_entry = None
    for i, e in enumerate(body):
        if e["seq"] == insert_after_seq:
            insert_idx = i + 1
            anchor_entry = e
            break
    if insert_idx is None:
        raise ValueError(f"seq {insert_after_seq} not found in entries")

    # Prepare new entries
    prepared = []
    for e in new_entries:
        ne = copy.deepcopy(e)
        ne["section"] = section_override if section_override else anchor_entry["section"]
        ne["scene"] = scene_override if scene_override is not None else anchor_entry["scene"]
        prepared.append(ne)

    # Splice
    body = body[:insert_idx] + prepared + body[insert_idx:]

    # Renumber body
    for i, e in enumerate(body):
        e["seq"] = i + 1

    return preamble + body


def delete_entries(entries: list[dict], seq_range: tuple[int, int]) -> list[dict]:
    """Remove entries whose seq falls within [start, end] inclusive, renumber remainder.

    Raises:
        ValueError: If *seq_range* includes preamble entries (seq <= 0).
    """
    start, end = seq_range
    if start <= 0:
        raise ValueError(f"Cannot delete preamble entries (seq_range starts at {start})")

    preamble = [copy.deepcopy(e) for e in entries if e["seq"] <= 0]
    body = [copy.deepcopy(e) for e in entries if e["seq"] > 0 and not (start <= e["seq"] <= end)]

    for i, e in enumerate(body):
        e["seq"] = i + 1

    return preamble + body


def update_stats(data: dict) -> None:
    """Recompute ``data['stats']`` from ``data['entries']``.

    Counts only body entries (seq > 0).  Mutates *data* in place.
    """
    body = [e for e in data["entries"] if e["seq"] > 0]
    dialogue = [e for e in body if e["type"] == "dialogue"]
    directions = [e for e in body if e["type"] == "direction"]
    speakers = sorted({e["speaker"] for e in dialogue if e.get("speaker")})
    sections = sorted({e["section"] for e in body if e.get("section")})
    tts_chars = sum(len(e["text"]) for e in dialogue)

    data["stats"] = {
        "total_entries": len(body),
        "dialogue_lines": len(dialogue),
        "direction_lines": len(directions),
        "characters_for_tts": tts_chars,
        "speakers": speakers,
        "sections": sections,
    }


# ── File I/O wrapper ─────────────────────────────────────────────────────────


def run_splice(
    target_path: str,
    insert_after_seq: int | None = None,
    new_entries: list[dict] | None = None,
    delete_range: tuple[int, int] | None = None,
    section_override: str | None = None,
    scene_override: str | None = None,
    dry_run: bool = False,
    backup_path: str | None = "AUTO",
    quiet: bool = False,
) -> dict:
    """Load, splice/delete, and write back a parsed JSON file.

    Args:
        target_path: Path to the parsed JSON file.
        insert_after_seq: Seq number to insert after (None to skip insertion).
        new_entries: Entries to insert (required if *insert_after_seq* is set).
        delete_range: (start, end) seq range to delete (None to skip deletion).
        section_override: Override section on inserted entries.
        scene_override: Override scene on inserted entries.
        dry_run: If True, print plan but do not write files.
        backup_path: Path for backup file, None to skip, "AUTO" is not handled here.
        quiet: If True, suppress per-entry detail.

    Returns:
        The updated parsed data dict.
    """
    with open(target_path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data["entries"]
    original_count = len([e for e in entries if e["seq"] > 0])

    # Delete phase
    if delete_range:
        start, end = delete_range
        deleted = [e for e in entries if start <= e["seq"] <= end]
        entries = delete_entries(entries, delete_range)
        if not quiet:
            print(f"\n  DELETE seq {start}–{end}: {len(deleted)} entries removed")
            for e in deleted:
                print(f"    - seq {e['seq']} [{e['type']}] {e.get('speaker', '')} — {e['text'][:60]}")

    # Insert phase
    if insert_after_seq is not None and new_entries:
        if not quiet:
            anchor = next((e for e in entries if e["seq"] == insert_after_seq), None)
            print(f"\n  INSERT {len(new_entries)} entries after seq {insert_after_seq}"
                  f" ({anchor['text'][:40]}...)" if anchor else "")
            for e in new_entries:
                label = section_override or "(inherit)"
                print(f"    + [{e['type']}] {e.get('speaker', '')} — {e['text'][:60]}  [section={label}]")
        entries = splice_entries(
            entries, insert_after_seq, new_entries,
            section_override=section_override, scene_override=scene_override,
        )

    data["entries"] = entries
    update_stats(data)

    new_count = len([e for e in entries if e["seq"] > 0])
    print(f"\n  Summary: {original_count} → {new_count} entries (body)")

    if dry_run:
        print("  [DRY RUN] No files written.")
        return data

    # Write backup
    if backup_path:
        with open(target_path, encoding="utf-8") as f:
            original_content = f.read()
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(original_content)
        print(f"  Backup written: {backup_path}")

    # Write updated file
    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  Updated: {target_path}")

    return data


# ── CLI ───────────────────────────────────────────────────────────────────────


def _parse_range(s: str) -> tuple[int, int]:
    """Parse 'N-M' into (N, M)."""
    parts = s.split("-", 1)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Expected N-M range, got: {s}")
    return int(parts[0]), int(parts[1])


def main() -> None:
    """Splice entries into or delete entries from a parsed episode JSON."""
    with run_banner():
        parser = argparse.ArgumentParser(
            description="Splice Parsed JSON — insert/delete entries with automatic renumbering",
        )
        parser.add_argument("--episode", required=True,
                            help="Episode tag (e.g. S02E03) — derives target parsed JSON path")
        parser.add_argument("--show", default=None,
                            help="Show name override (default: from project.json)")
        parser.add_argument("--parsed", default=None,
                            help="Override target parsed JSON path")

        # Insert options
        parser.add_argument("--insert-after", type=int, default=None,
                            help="Seq number to insert after")
        parser.add_argument("--from-parsed", default=None, dest="from_parsed",
                            help="Source parsed JSON to extract entries from")
        parser.add_argument("--from-seq-range", default=None, dest="from_seq_range",
                            help="Seq range to extract from source (e.g. 232-233)")
        parser.add_argument("--from-json", default=None, dest="from_json",
                            help="Path to a JSON array of entries to insert")
        parser.add_argument("--section", default=None,
                            help="Override section on inserted entries")
        parser.add_argument("--scene", default=None,
                            help="Override scene on inserted entries")

        # Delete options
        parser.add_argument("--delete-seq-range", default=None, dest="delete_seq_range",
                            help="Seq range to delete (e.g. 100-105)")

        # Output options
        parser.add_argument("--dry-run", action="store_true",
                            help="Show plan without writing files")
        parser.add_argument("--no-backup", action="store_true",
                            help="Skip backup file")
        parser.add_argument("--quiet", action="store_true",
                            help="Summary only, no per-entry detail")

        args = parser.parse_args()

        # Resolve paths
        slug = resolve_slug(args.show)
        paths = derive_paths(slug, args.episode)
        target_path = args.parsed or paths["parsed"]

        if not os.path.exists(target_path):
            print(f"ERROR: Target parsed JSON not found: {target_path}")
            return

        print(f"  Target: {target_path}")

        # Resolve new entries for insertion
        new_entries = None
        if args.insert_after is not None:
            if args.from_parsed and args.from_seq_range:
                start, end = _parse_range(args.from_seq_range)
                with open(args.from_parsed, encoding="utf-8") as f:
                    source_data = json.load(f)
                new_entries = extract_seq_range(source_data["entries"], start, end)
                if not new_entries:
                    print(f"  WARNING: No entries found in seq range {start}–{end} of {args.from_parsed}")
                    return
                print(f"  Source: {args.from_parsed} seq {start}–{end} ({len(new_entries)} entries)")
            elif args.from_json:
                with open(args.from_json, encoding="utf-8") as f:
                    new_entries = json.load(f)
                print(f"  Source: {args.from_json} ({len(new_entries)} entries)")
            else:
                print("ERROR: --insert-after requires --from-parsed + --from-seq-range or --from-json")
                return

        # Resolve delete range
        delete_range = None
        if args.delete_seq_range:
            delete_range = _parse_range(args.delete_seq_range)

        if new_entries is None and delete_range is None:
            print("ERROR: Nothing to do — specify --insert-after or --delete-seq-range")
            return

        # Resolve backup path
        backup_path = None
        if not args.no_backup and not args.dry_run:
            parsed_dir = os.path.dirname(target_path)
            backup_name = f"pre_splice_parsed_{slug}_{args.episode}.json"
            backup_path = os.path.join(parsed_dir, backup_name) if parsed_dir else backup_name

        # Run
        run_splice(
            target_path=target_path,
            insert_after_seq=args.insert_after,
            new_entries=new_entries,
            delete_range=delete_range,
            section_override=args.section,
            scene_override=args.scene,
            dry_run=args.dry_run,
            backup_path=backup_path,
            quiet=args.quiet,
        )

        # Next steps
        if not args.dry_run:
            orig_prefix = "pre_splice_"
            print("\n  Next steps:")
            print(f"    1. python XILP007_stem_migrator.py --episode {args.episode} --orig-prefix {orig_prefix}")
            print(f"    2. python XILP002_producer.py --episode {args.episode}")


if __name__ == "__main__":
    main()
