# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Standalone SFX stem generation utility.

Generates sound effect stems from an SFX configuration file, storing them
in the same ``stems/<TAG>/`` directory that XILP002 and XILP003 expect.
This allows SFX stems to be generated independently of dialogue voice
generation, with fine-grained control over API credit spend.

Shared SFX assets are cached in the ``SFX/`` directory so that each
unique effect is only generated once.  Episode stems are copies of the
shared assets with sequence-numbered filenames.

Usage::

    # Preview what will be generated and estimated cost
    python XILU002_generate_SFX.py --episode S01E01 --dry-run

    # Generate only short effects (≤5s) to limit credit usage
    python XILU002_generate_SFX.py --episode S01E01 --max-duration 5.0

    # Generate all SFX stems
    python XILU002_generate_SFX.py --episode S01E01

Module Attributes:
    STEMS_DIR: Base directory for stem subdirectories.
"""

import argparse
import json
import os
import sys

from elevenlabs.client import ElevenLabs

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import CastConfiguration, derive_paths, resolve_slug
from xil_pipeline.sfx_common import dry_run_sfx, generate_sfx, load_sfx_entries, run_banner

logger = get_logger(__name__)

client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))

STEMS_DIR = "stems"


def load_sfx_plan(
    script_json_path: str, sfx_json_path: str, cast_json_path: str,
    max_duration: float | None = None,
    direction_types: set[str] | None = None,
    local_only: bool = False,
) -> tuple[list[dict], str]:
    """Load SFX entries and derive the stems directory path.

    Delegates entry loading to :func:`sfx_common.load_sfx_entries` and
    derives the stems directory from the cast configuration tag.

    Args:
        script_json_path: Path to the parsed script JSON.
        sfx_json_path: Path to the SFX configuration JSON.
        cast_json_path: Path to the cast configuration JSON.
        max_duration: If set, exclude effects with ``duration_seconds``
            exceeding this value. Useful for limiting API credit spend.
        direction_types: If set, only include entries whose
            ``direction_type`` is in this set. ``None`` includes all.
        local_only: If ``True``, skip effects not already present in
            ``SFX/``; no API generation.

    Returns:
        A tuple of ``(sfx_entries, stems_dir)`` where ``sfx_entries`` is
        a list of dicts and ``stems_dir`` is the full path to the episode
        stems directory.
    """
    if not os.path.exists(cast_json_path):
        raise FileNotFoundError(
            f"Cast config not found: {cast_json_path}\n"
            "Run XILP001 first or check your --episode flag."
        )
    with open(cast_json_path, encoding="utf-8") as f:
        cast_data = json.load(f)
    cast_cfg = CastConfiguration(**cast_data)
    stems_dir = os.path.join(STEMS_DIR, cast_cfg.tag)

    sfx_entries = load_sfx_entries(
        script_json_path, sfx_json_path,
        max_duration=max_duration,
        direction_types=direction_types,
        local_only=local_only,
    )
    return sfx_entries, stems_dir


def main() -> None:
    """CLI entry point for standalone SFX stem generation."""
    configure_logging()
    with run_banner():
        parser = argparse.ArgumentParser(
            description="Generate SFX stems from an SFX config (standalone utility)"
        )
        parser.add_argument("--episode", required=True,
                            help="Episode tag (e.g. S01E01) — derives cast and SFX config paths")
        parser.add_argument("--show", default=None,
                            help="Show name override (default: from project.json)")
        parser.add_argument("--script", default=None,
                            help="Path to parsed script JSON (default: derived from cast config)")
        parser.add_argument("--dry-run", action="store_true",
                            help="Preview existing vs. new stems and estimated credit cost")
        parser.add_argument("--max-duration", type=float, default=None,
                            help="Only process effects with duration_seconds <= this value")
        parser.add_argument("--gen-sfx", action="store_true",
                            help="Limit to SFX and BEAT entries only")
        parser.add_argument("--gen-music", action="store_true",
                            help="Limit to MUSIC entries only")
        parser.add_argument("--gen-ambience", action="store_true",
                            help="Limit to AMBIENCE entries only")
        parser.add_argument("--sfx-music", action="store_true",
                            help="(deprecated) shorthand for --gen-sfx --gen-music --gen-ambience")
        parser.add_argument("--local-only", action="store_true",
                            help="Only place stems for effects already present in SFX/; skip API generation")
        args = parser.parse_args()

        if not args.dry_run and not os.environ.get("ELEVENLABS_API_KEY"):
            sys.exit("Error: ELEVENLABS_API_KEY environment variable is not set.")

        # Derive config paths from --episode
        slug = resolve_slug(args.show)
        p = derive_paths(slug, args.episode)
        cast_path = p["cast"]
        sfx_path = p["sfx"]

        # Derive default --script from cast config
        if args.script is None:
            if not os.path.exists(cast_path):
                sys.exit(f"Error: Cast config not found: {cast_path}\nRun XILP001 first or check your --episode flag.")
            with open(cast_path, encoding="utf-8") as f:
                cast_data = json.load(f)
            CastConfiguration(**cast_data)  # validate cast config
            args.script = p["parsed"]

        direction_types: set[str] | None = None
        if args.gen_sfx or args.gen_music or args.gen_ambience or args.sfx_music:
            direction_types = set()
            if args.gen_sfx or args.sfx_music:
                direction_types |= {"SFX", "BEAT"}
            if args.gen_music or args.sfx_music:
                direction_types.add("MUSIC")
            if args.gen_ambience or args.sfx_music:
                direction_types.add("AMBIENCE")

        entries, stems_dir = load_sfx_plan(
            args.script, sfx_path, cast_path,
            max_duration=args.max_duration,
            direction_types=direction_types,
            local_only=args.local_only,
        )

        if not os.path.exists(sfx_path):
            sys.exit(f"Error: SFX config not found: {sfx_path}\nRun XILP001 first or check your --episode flag.")
        with open(sfx_path, encoding="utf-8") as f:
            sfx_config_data = json.load(f)

        if args.dry_run:
            dry_run_sfx(entries, sfx_config_data, stems_dir)
        else:
            generate_sfx(
                entries, sfx_config_data, stems_dir, client=client,
            )


if __name__ == "__main__":
    main()
