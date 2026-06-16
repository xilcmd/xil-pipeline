# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILP011 — Final Master MP3 Export.

Overlays the five DAW layer WAV files produced by XILP005 into a single
stereo MP3 file suitable for podcast distribution.

Output format:
    - Stereo, 48 kHz sample rate
    - VBR MP3, quality target 145–185 kbps (LAME VBR quality ~2)
    - Filename: ``S01E01_<slug>_YYYY-MM-DD.mp3``

**Usage:**

```bash
python XILP011_master_export.py --episode S02E03 --dry-run
python XILP011_master_export.py --episode S02E03
python XILP011_master_export.py --episode S02E03 --show "Night Owls"
```

No ElevenLabs API calls are made — this stage is safe to run freely.
"""

import argparse
import datetime
import json
import os

from pydub import AudioSegment

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import CastConfiguration, derive_paths, get_workspace_root, resolve_slug
from xil_pipeline.sfx_common import run_banner, tag_mp3

logger = get_logger(__name__)

MASTERS_DIR = str(get_workspace_root() / "masters")
DAW_DIR = str(get_workspace_root() / "daw")
LAYER_SUFFIXES = ("dialogue", "ambience", "music", "sfx", "vintage_filter")
SAMPLE_RATE = 48000


def _find_cover_art(slug: str) -> str | None:
    """Return the path to configs/<slug>/cover_art.* if one exists, else None."""
    configs_dir = get_workspace_root() / "configs" / slug
    for name in ("cover_art.PNG", "cover_art.png", "cover_art.jpg", "cover_art.jpeg"):
        candidate = configs_dir / name
        if candidate.exists():
            return str(candidate)
    return None


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
    cover_art_path: str | None = None,
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
        cover_art_path=cover_art_path,
    )


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-master."""
    parser = argparse.ArgumentParser(
        prog="xil-master",
        description="Final Master MP3 Export — mix DAW layers into a single podcast-ready MP3",
    )
    tag_group = parser.add_mutually_exclusive_group(required=True)
    tag_group.add_argument("--episode",
                           help="Episode tag (e.g. S02E03) — derives DAW layer paths")
    tag_group.add_argument("--tag",
                           help="Raw tag for non-episodic content (e.g. V01C03, D01)")
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
    return parser


def main() -> None:
    """CLI entry point for final master MP3 export."""
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        slug = resolve_slug(args.show)
        tag = args.episode or args.tag

        # Load cast config for metadata (title, artist)
        p = derive_paths(slug, tag)
        daw_dir = args.daw_dir or p["daw"]
        cast_path = p["cast"]
        parsed_path = p["parsed"]
        show_name = None
        episode_title = None
        artist = None
        if os.path.exists(cast_path):
            with open(cast_path, encoding="utf-8") as f:
                cast_cfg = CastConfiguration(**json.load(f))
            show_name = cast_cfg.show
            episode_title = cast_cfg.title
            artist = cast_cfg.artist

        # Fallback: fill missing ID3 fields from parsed JSON script header
        if (show_name is None or episode_title is None) and os.path.exists(parsed_path):
            with open(parsed_path, encoding="utf-8") as f:
                parsed = json.load(f)
            if show_name is None:
                show_name = parsed.get("show")
            if episode_title is None:
                episode_title = parsed.get("title")
            if show_name or episode_title:
                logger.info("  Metadata sourced from parsed JSON (cast config fields absent)")

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

        logger.info(f"  Episode    : {tag}")
        logger.info(f"  Show       : {show_name} (slug: {slug})")
        logger.info(f"  DAW dir    : {daw_dir}")
        logger.info(f"  Output     : {output_path}")
        logger.info(f"  Format     : Stereo, {SAMPLE_RATE} Hz, VBR MP3 (~145-185 kbps)")
        logger.info(f"  Layers     : {len(layers)}/{len(LAYER_SUFFIXES)} found")
        for name, path in layers:
            logger.info(f"    [{name:>9s}] {path}")
        if missing:
            logger.info(f"  Missing    : {', '.join(missing)}")
        logger.info("")

        if not layers:
            logger.warning("No layer WAVs found. Run XILP005 first.")
            return

        if args.dry_run:
            logger.info("--- Dry run — no files written ---")
            return

        # Mix and export
        logger.info("--- Mixing layers ---")
        combined = mix_layers(layers)
        duration_s = len(combined) / 1000.0
        minutes = int(duration_s // 60)
        seconds = duration_s % 60

        logger.info(f"  Duration   : {minutes}:{seconds:05.2f}")
        logger.info("--- Exporting master MP3 ---")

        title = f"{show_name} — {episode_title}" if episode_title else tag
        cover_art_path = _find_cover_art(slug)
        if cover_art_path:
            logger.info(f"  Cover art  : {cover_art_path}")
        export_master(
            combined, output_path,
            show_name=show_name,
            tag=tag,
            title=title,
            artist=artist,
            cover_art_path=cover_art_path,
        )

        file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
        logger.info(f"  Written    : {output_path} ({file_size_mb:.1f} MB)")
        logger.info("--- Done! ---")


if __name__ == "__main__":
    main()
