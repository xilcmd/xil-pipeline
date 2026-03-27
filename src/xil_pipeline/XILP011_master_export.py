# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILP011 — Final Master MP3 Export.

Overlays the four DAW layer WAV files produced by XILP005 into a single
stereo MP3 file suitable for podcast distribution.

Output format:
    - Stereo, 48 kHz sample rate
    - VBR MP3, quality target 145–185 kbps (LAME VBR quality ~2)
    - Filename: ``S01E01_the413_2026-03-24.mp3``

Usage::

    python XILP011_master_export.py --episode S02E03 --dry-run
    python XILP011_master_export.py --episode S02E03
    python XILP011_master_export.py --episode S02E03 --show "Night Owls"

No ElevenLabs API calls are made — this stage is safe to run freely.
"""

import argparse
import datetime
import json
import os

from pydub import AudioSegment

from xil_pipeline.models import CastConfiguration, derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner, tag_mp3

MASTERS_DIR = "masters"
DAW_DIR = "daw"
LAYER_SUFFIXES = ("dialogue", "ambience", "music", "sfx")
SAMPLE_RATE = 48000


def load_layer_wavs(daw_dir: str, tag: str) -> list[tuple[str, str]]:
    """Locate the four layer WAV files for an episode.

    Returns:
        List of (layer_name, file_path) tuples for layers that exist on disk.
    """
    found = []
    for suffix in LAYER_SUFFIXES:
        fname = f"{tag}_layer_{suffix}.wav"
        path = os.path.join(daw_dir, fname)
        if os.path.exists(path):
            found.append((suffix, path))
    return found


def mix_layers(layer_paths: list[tuple[str, str]]) -> AudioSegment:
    """Overlay all layer WAVs into a single AudioSegment.

    All layers are assumed to be the same duration and aligned at t=0
    (as produced by XILP005).
    """
    combined = None
    for name, path in layer_paths:
        seg = AudioSegment.from_wav(path)
        if combined is None:
            combined = seg
        else:
            combined = combined.overlay(seg)
    return combined


def export_master(
    combined: AudioSegment,
    output_path: str,
    show_name: str,
    tag: str,
    title: str | None = None,
    artist: str | None = None,
) -> None:
    """Export the mixed audio as a stereo 48 kHz VBR MP3.

    Uses LAME VBR quality 2 which targets ~170–210 kbps ABR, producing
    a VBR stream in the 145–185 kbps range for spoken-word content.
    """
    # Ensure stereo
    if combined.channels == 1:
        combined = combined.set_channels(2)

    # Resample to 48 kHz
    combined = combined.set_frame_rate(SAMPLE_RATE)

    combined.export(
        output_path,
        format="mp3",
        parameters=[
            "-q:a", "2",          # LAME VBR quality (lower = higher bitrate)
            "-ar", str(SAMPLE_RATE),
        ],
    )

    # Write ID3 metadata
    tag_mp3(
        output_path,
        show=show_name,
        title=title or tag,
        artist=artist,
    )


def main() -> None:
    """CLI entry point for final master MP3 export."""
    with run_banner():
        parser = argparse.ArgumentParser(
            description="Final Master MP3 Export — mix DAW layers into a single podcast-ready MP3"
        )
        parser.add_argument(
            "--episode", required=True,
            help="Episode tag (e.g. S02E03) — derives DAW layer paths",
        )
        parser.add_argument(
            "--show", default=None,
            help="Show name override (default: from project.json)",
        )
        parser.add_argument(
            "--daw-dir", default=None,
            help="DAW layer directory (default: daw/<TAG>/)",
        )
        parser.add_argument(
            "--output", default=None,
            help="Output MP3 path (default: masters/<TAG>_<slug>_<date>.mp3)",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be exported without writing files",
        )
        args = parser.parse_args()

        slug = resolve_slug(args.show)
        tag = args.episode
        daw_dir = args.daw_dir or os.path.join(DAW_DIR, tag)

        # Load cast config for metadata (title, artist)
        p = derive_paths(slug, tag)
        cast_path = p["cast"]
        show_name = None
        episode_title = None
        artist = None
        if os.path.exists(cast_path):
            with open(cast_path, encoding="utf-8") as f:
                cast_cfg = CastConfiguration(**json.load(f))
            show_name = cast_cfg.show
            episode_title = cast_cfg.title
            artist = cast_cfg.artist

        show_name = show_name or "Sample Show"
        today = datetime.date.today().isoformat()

        # Derive output path
        if args.output:
            output_path = args.output
        else:
            os.makedirs(MASTERS_DIR, exist_ok=True)
            output_path = os.path.join(
                MASTERS_DIR, f"{tag}_{slug}_{today}.mp3"
            )

        # Find layer WAVs
        layers = load_layer_wavs(daw_dir, tag)
        missing = [s for s in LAYER_SUFFIXES if s not in {l[0] for l in layers}]

        print(f"  Episode    : {tag}")
        print(f"  Show       : {show_name} (slug: {slug})")
        print(f"  DAW dir    : {daw_dir}")
        print(f"  Output     : {output_path}")
        print(f"  Format     : Stereo, {SAMPLE_RATE} Hz, VBR MP3 (~145-185 kbps)")
        print(f"  Layers     : {len(layers)}/{len(LAYER_SUFFIXES)} found")
        for name, path in layers:
            print(f"    [{name:>9s}] {path}")
        if missing:
            print(f"  Missing    : {', '.join(missing)}")
        print()

        if not layers:
            print("[!] No layer WAVs found. Run XILP005 first.")
            return

        if args.dry_run:
            print("--- Dry run — no files written ---")
            return

        # Mix and export
        print("--- Mixing layers ---")
        combined = mix_layers(layers)
        duration_s = len(combined) / 1000.0
        minutes = int(duration_s // 60)
        seconds = duration_s % 60

        print(f"  Duration   : {minutes}:{seconds:05.2f}")
        print("--- Exporting master MP3 ---")

        title = f"{show_name} — {episode_title}" if episode_title else tag
        export_master(
            combined, output_path,
            show_name=show_name,
            tag=tag,
            title=title,
            artist=artist,
        )

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        print(f"  Written    : {output_path} ({file_size_mb:.1f} MB)")
        print("--- Done! ---")


if __name__ == "__main__":
    main()
