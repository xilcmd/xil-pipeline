# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Assemble voice stems into the final master audio file.

Reads cast configuration for per-speaker audio settings (pan, filter),
applies effects to each stem, and produces a master MP3.

When a parsed script JSON is available (either supplied via ``--parsed``
or auto-derived from the episode tag), the assembler runs a two-pass
multi-track mix:

- **Foreground pass**: dialogue and one-shot SFX/BEAT stems are
  concatenated sequentially (original behaviour).  The timestamp of
  every stem is recorded in a timeline dict.
- **Background pass**: AMBIENCE stems are looped and overlaid under
  dialogue at their cue points; MUSIC stings are overlaid without
  looping.  Both are ducked slightly below the foreground level.
- Foreground and background are combined with ``AudioSegment.overlay``.

When no parsed JSON is found the assembler falls back to the original
sequential concatenation (all stems in filename order with silence gaps).
No ElevenLabs API calls are made — this module is safe to run at any
time without consuming TTS quota.

Module Attributes:
    STEMS_DIR: Default directory containing generated voice stem MP3 files.
    SILENCE_GAP_MS: Milliseconds of silence inserted between foreground stems.
"""

import argparse
import json
import os
import subprocess

from pydub import AudioSegment

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.mix_common import (
    apply_phone_filter,
    build_ambience_layer,
    build_foreground,
    build_music_layer,
    collect_stem_plans,
    load_entries_index,
)
from xil_pipeline.models import CastConfiguration, SfxConfiguration, VoiceConfig, derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

STEMS_DIR = "stems"
SILENCE_GAP_MS = 600


def assemble_audio(config: dict[str, dict], stems_dir: str, final_output: str, gap_ms: int = 600) -> None:
    """Assemble voice stems sequentially into a master audio file.

    Loads all MP3 stems from the stems directory sorted by filename
    (sequence prefix ensures correct episode order), applies per-speaker
    audio effects (phone filter, stereo panning), concatenates with
    silence gaps, and exports the master file.

    This is the original single-pass assembler.  Used as a fallback
    when no parsed script JSON is available for two-pass mixing.

    Args:
        config: Mapping of speaker keys to voice settings dicts with
            keys ``id``, ``pan``, and ``filter``. Built from cast config
            via ``CastConfiguration`` and ``VoiceConfig``.
        stems_dir: Directory containing voice stem MP3 files.
        final_output: Path for the master MP3 output file.
    """
    import glob
    stem_files = sorted(glob.glob(os.path.join(stems_dir, "*.mp3")))
    if not stem_files:
        logger.warning("No stems found in %s/. Run XILP002 first.", stems_dir)
        return

    logger.info("--- Phase 2: Assembling %d stems (sequential) ---", len(stem_files))
    full_vocals = AudioSegment.empty()

    for stem_file in stem_files:
        # Extract speaker from filename: "003_cold-open_adam.mp3" -> "adam"
        basename = os.path.splitext(os.path.basename(stem_file))[0]
        speaker = basename.rsplit("_", 1)[-1]

        logger.info("   Loading: %s (%s)", stem_file, speaker)
        segment = AudioSegment.from_file(stem_file)

        # Apply per-speaker effects
        if speaker in config:
            if config[speaker]["filter"]:
                segment = apply_phone_filter(segment)
            segment = segment.pan(config[speaker]["pan"])

        full_vocals += segment + AudioSegment.silent(duration=gap_ms)

    full_vocals.export(final_output, format="mp3")
    logger.info("--- Success! Created: %s (Duration: %.1fs) ---", final_output, len(full_vocals)/1000)
    subprocess.run(["mpg123", os.path.abspath(final_output)], check=False)


def assemble_multitrack(
    config: dict[str, dict],
    stems_dir: str,
    parsed_path: str,
    final_output: str,
    sfx_config=None,
    gap_ms: int = 600,
) -> None:
    """Assemble stems using a two-pass multi-track mix.

    Builds a foreground track (dialogue + SFX/BEAT) and a background
    layer (AMBIENCE looped across scenes, MUSIC stings at cue points),
    then overlays them for the final master.

    Requires a parsed script JSON to classify stems by direction_type.
    Falls back to :func:`assemble_audio` if the stems directory is empty.

    Args:
        config: Per-speaker voice settings from cast config.
        stems_dir: Directory containing episode stem MP3 files.
        parsed_path: Path to the parsed script JSON (XILP001 output).
        final_output: Output path for the master MP3.
    """
    entries_index = load_entries_index(parsed_path)
    stem_plans = collect_stem_plans(stems_dir, entries_index, sfx_config=sfx_config)

    if not stem_plans:
        logger.warning("No stems found in %s/. Run XILP002 first.", stems_dir)
        return

    logger.info("--- Phase 2: Assembling %d stems (multi-track) ---", len(stem_plans))

    foreground, timeline = build_foreground(
        stem_plans, config, apply_phone_filter, gap_ms=gap_ms
    )

    if len(foreground) == 0:
        logger.warning("No foreground stems found — only background stems present.")
        return

    total_ms = len(foreground)
    bg_plans = [p for p in stem_plans if p.is_background]
    if bg_plans:
        logger.info("   Mixing %d background stems (ambience/music)...", len(bg_plans))
        ambience, _ = build_ambience_layer(stem_plans, timeline, total_ms)
        music, _ = build_music_layer(stem_plans, timeline, total_ms)
        background = ambience.overlay(music)
        master = foreground.overlay(background)
    else:
        logger.info("   No background stems found — skipping overlay pass.")
        master = foreground

    master.export(final_output, format="mp3")
    logger.info("--- Success! Created: %s (Duration: %.1fs) ---", final_output, len(master)/1000)
    subprocess.run(["mpg123", os.path.abspath(final_output)], check=False)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-assemble",
        description="Audio Assembly — assemble voice stems into master MP3",
    )
    parser.add_argument(
        "--episode", required=True,
        help="Episode tag (e.g. S01E01) — derives cast config path"
    )
    parser.add_argument(
        "--show", default=None,
        help="Show name override (default: from project.json)"
    )
    parser.add_argument(
        "--output", default=None,
        help="Output master MP3 path (default: <slug>_<TAG>_master.mp3)"
    )
    parser.add_argument(
        "--parsed", default=None,
        help="Path to parsed script JSON (default: parsed/parsed_<slug>_<TAG>.json)"
    )
    parser.add_argument(
        "--gap-ms", type=int, default=SILENCE_GAP_MS,
        help=f"Silence gap between foreground stems in ms (default: {SILENCE_GAP_MS})"
    )
    return parser


def main() -> None:
    """CLI entry point for audio assembly.

    Loads cast configuration to determine per-speaker audio settings.
    If a parsed script JSON exists (auto-derived or via ``--parsed``),
    runs two-pass multi-track mixing.  Otherwise falls back to sequential
    concatenation.  Does not require an ElevenLabs API key.
    """
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        slug = resolve_slug(args.show)
        p = derive_paths(slug, args.episode)
        cast_path = p["cast"]
        if not os.path.exists(cast_path):
            logger.error("Cast config not found: %s", cast_path)
            logger.info("Run XILP001 first or check your --episode flag.")
            return
        with open(cast_path, encoding="utf-8") as f:
            cast_data = json.load(f)

        cast_cfg = CastConfiguration(**cast_data)
        tag = cast_cfg.tag
        config = {
            key: VoiceConfig(id=member.voice_id, pan=member.pan, filter=member.filter).model_dump()
            for key, member in cast_cfg.cast.items()
        }

        stems_dir = os.path.join(STEMS_DIR, tag)
        output = args.output or p["master"]

        parsed_path = args.parsed or p["parsed"]
        sfx_path = p["sfx"]
        sfx_config = None
        if os.path.exists(sfx_path):
            with open(sfx_path, encoding="utf-8") as f:
                sfx_config = SfxConfiguration(**json.load(f))

        if os.path.exists(parsed_path):
            assemble_multitrack(
                config, stems_dir, parsed_path, output,
                sfx_config=sfx_config,
                gap_ms=args.gap_ms,
            )
        else:
            logger.info("   No parsed JSON at %r — using sequential assembly.", parsed_path)
            assemble_audio(config, stems_dir, output, gap_ms=args.gap_ms)


if __name__ == "__main__":
    main()
