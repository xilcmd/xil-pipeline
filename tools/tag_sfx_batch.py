#!/usr/bin/env python3
"""Batch-set ID3 tags on all MP3s in an SFX directory.

Applies album, contributing artist, year, and genre to every file.
Title is only written when the file has none; it is derived from the
filename (underscores and hyphens → spaces, duplicate spaces collapsed).

Usage:
    python tools/tag_sfx_batch.py SFX/thewoonsocketwonders \
        --album "The Woonsocket Wonders" --artist ElevenLabs

    # or rely on defaults inferred from the directory name:
    python tools/tag_sfx_batch.py SFX/thewoonsocketwonders
"""

import argparse
import re
import sys
from pathlib import Path

try:
    from mutagen.id3 import ID3, ID3NoHeaderError, TALB, TDRC, TCON, TIT2, TPE1
except ImportError:
    sys.exit("mutagen is required: pip install mutagen")


def _title_from_stem(stem: str) -> str:
    """Convert a filename stem to a readable title."""
    title = stem.replace("_", " ").replace("-", " ").strip()
    return re.sub(r" {2,}", " ", title)


def tag_directory(
    directory: Path,
    album: str,
    artist: str,
    year: str,
    genre: str,
    dry_run: bool = False,
) -> None:
    mp3s = sorted(directory.glob("*.mp3"))
    if not mp3s:
        print(f"No MP3 files found in {directory}")
        return

    print(f"{'[DRY RUN] ' if dry_run else ''}Tagging {len(mp3s)} files in {directory}\n")
    updated = kept = errors = 0

    for mp3 in mp3s:
        try:
            try:
                tags = ID3(str(mp3))
            except ID3NoHeaderError:
                tags = ID3()

            tags["TALB"] = TALB(encoding=3, text=album)
            tags["TPE1"] = TPE1(encoding=3, text=artist)
            tags["TDRC"] = TDRC(encoding=3, text=year)
            tags["TCON"] = TCON(encoding=3, text=genre)

            existing_title = str(tags["TIT2"]).strip() if "TIT2" in tags else ""
            if existing_title:
                title_note = f"kept  → {existing_title[:70]}"
                kept += 1
            else:
                new_title = _title_from_stem(mp3.stem)
                tags["TIT2"] = TIT2(encoding=3, text=new_title)
                title_note = f"set   → {new_title[:70]}"

            if not dry_run:
                tags.save(str(mp3))

            print(f"  ✓  {mp3.name[:72]}")
            print(f"       title {title_note}")
            updated += 1

        except Exception as exc:
            print(f"  ✗  {mp3.name}: {exc}")
            errors += 1

    print(f"\n{'='*60}")
    print(f"  Files processed : {updated}")
    print(f"  Titles kept     : {kept}")
    print(f"  Titles derived  : {updated - kept}")
    print(f"  Errors          : {errors}")
    if dry_run:
        print("  (dry run — no files written)")


def get_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tag_sfx_batch",
        description="Batch-set ID3 tags on all MP3s in an SFX directory",
    )
    p.add_argument("directory", help="Path to the SFX directory (e.g. SFX/thewoonsocketwonders)")
    p.add_argument("--album", default=None,
                   help="Album tag (default: directory name with underscores → spaces, title-cased)")
    p.add_argument("--artist", default="ElevenLabs",
                   help="Contributing artist tag (default: ElevenLabs)")
    p.add_argument("--year", default="2026",
                   help="Year tag (default: 2026)")
    p.add_argument("--genre", default="Podcast",
                   help="Genre/type tag (default: Podcast)")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change without writing any files")
    return p


def main() -> None:
    args = get_parser().parse_args()
    directory = Path(args.directory)
    if not directory.is_dir():
        sys.exit(f"Directory not found: {directory}")

    album = args.album or directory.name.replace("_", " ").replace("-", " ").title()

    tag_directory(
        directory=directory,
        album=album,
        artist=args.artist,
        year=args.year,
        genre=args.genre,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
