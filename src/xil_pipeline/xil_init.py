# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scaffold a new xil-pipeline project workspace.

Creates the directory structure, project.json, speakers.json, and a
sample production script so a first-time user can immediately run the
pipeline stages in dry-run mode.

Usage::

    xil-init                         # scaffold in current directory
    xil-init my-show                 # scaffold in ./my-show/
    xil-init --show "Night Owls"     # custom show name
"""

import argparse
import json
import os

from xil_pipeline.log_config import configure_logging, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Sample content templates
# ---------------------------------------------------------------------------

SAMPLE_SPEAKERS = [
    {"display": "HOST", "key": "host"},
    {"display": "CALLER", "key": "caller"},
    {"display": "GUEST", "key": "guest"},
]

SAMPLE_SCRIPT = """\
{show}{season_part} Episode 1: "Pilot"{arc_part}

CAST:
* HOST — the radio host
* CALLER — a mysterious caller
* GUEST — a local expert

===

COLD OPEN

SCENE 1: THE STUDIO

[AMBIENCE: Radio station studio, low hum of equipment, muffled music in background]

HOST
Good evening, and welcome to the show.

[SFX: Phone ringing]

HOST (picking up phone)
We have our first caller of the night. You're on the air.

CALLER (distorted, nervous)
Hi... I wasn't sure I should call, but I saw something last night that I can't explain.

HOST
Take your time. Tell us what happened.

CALLER
I was driving home on Route 9 when the radio cut out. Just static. And then... a voice. Not from any station.

[BEAT]

HOST
What did the voice say?

CALLER
It said my name. And then it said... "Don't go home."

[BEAT — 3 SECONDS]

===

ACT ONE

SCENE 2: THE INTERVIEW

[AMBIENCE: Same studio, quieter now]

HOST
Joining us in the studio tonight is Dr. Alex Reeves. Doctor, you've studied phenomena like this before.

GUEST
That's right. What your caller described is consistent with a pattern we've documented across several counties.

HOST
A pattern?

GUEST (leaning in)
Electromagnetic interference followed by anomalous audio. We've recorded over thirty incidents in the past year alone.

[SFX: Paper rustling]

GUEST
I brought some of the recordings with me, if you'd like to hear them.

HOST
Absolutely. Let's play one for our listeners.

[MUSIC: Eerie ambient drone, building tension]

[BEAT]

HOST
Well... that's certainly unsettling. We'll be right back after this break.

===

END OF EPISODE
"""


def scaffold(
    directory: str,
    show_name: str,
    season: int | None = None,
    season_title: str | None = None,
) -> None:
    """Create a new xil-pipeline workspace in *directory*.

    Args:
        directory: Target directory (created if it doesn't exist).
        show_name: Human-readable show name for project.json.
        season: Optional season number for project.json and the sample script header.
        season_title: Optional season/arc title for project.json and the sample script header.
    """
    os.makedirs(directory, exist_ok=True)

    # project.json
    project_path = os.path.join(directory, "project.json")
    if not os.path.exists(project_path):
        with open(project_path, "w", encoding="utf-8") as f:
            json.dump({"show": show_name, "season": season, "season_title": season_title}, f, indent=2)
            f.write("\n")
        logger.info(f"  Created {project_path}")
    else:
        logger.info(f"  Skipped {project_path} (already exists)")

    # speakers.json
    speakers_path = os.path.join(directory, "speakers.json")
    if not os.path.exists(speakers_path):
        with open(speakers_path, "w", encoding="utf-8") as f:
            json.dump(SAMPLE_SPEAKERS, f, indent=2)
            f.write("\n")
        logger.info(f"  Created {speakers_path}")
    else:
        logger.info(f"  Skipped {speakers_path} (already exists)")

    # Subdirectories
    for subdir in ("scripts", "parsed", "stems", "SFX", "daw", "masters", "cues"):
        path = os.path.join(directory, subdir)
        os.makedirs(path, exist_ok=True)

    # Sample script — build header tokens from provided settings
    season_part = f" Season {season}:" if season is not None else ""
    arc_part = f' Arc: "{season_title}"' if season_title else ""
    script_path = os.path.join(directory, "scripts", "sample_S01E01.md")
    if not os.path.exists(script_path):
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(SAMPLE_SCRIPT.format(
                show=show_name,
                season_part=season_part,
                arc_part=arc_part,
            ))
        logger.info(f"  Created {script_path}")
    else:
        logger.info(f"  Skipped {script_path} (already exists)")


def print_getting_started(directory: str) -> None:
    """Print a getting-started guide after scaffolding."""
    cd_prefix = f"cd {directory} && " if directory != "." else ""
    logger.info(f"""
Getting Started
===============

1. Install the pipeline:
   pip install xil-pipeline

2. Scan the sample script (pre-flight check):
   {cd_prefix}xil-scan scripts/sample_S01E01.md

3. Parse the script into structured JSON:
   {cd_prefix}xil-parse scripts/sample_S01E01.md --episode S01E01

4. Preview voice generation (no API key needed):
   {cd_prefix}xil-produce --episode S01E01 --dry-run

5. To use your own script:
   - Edit speakers.json with your cast
   - Write your script in scripts/
   - Set your ElevenLabs API key: export ELEVENLABS_API_KEY=your-key
   - Run the pipeline stages in order (see README.md)
""")


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-init",
        description="Scaffold a new xil-pipeline project workspace",
    )
    parser.add_argument(
        "directory", nargs="?", default=".",
        help="Target directory (default: current directory)",
    )
    parser.add_argument(
        "--show", default="Sample Show",
        help='Show name for project.json (default: "Sample Show")',
    )
    parser.add_argument(
        "--season", type=int, default=None,
        help="Season number for project.json and the sample script header",
    )
    parser.add_argument(
        "--season-title", default=None, metavar="TITLE",
        help="Season/arc title for project.json and the sample script header",
    )
    return parser


def main() -> None:
    """CLI entry point for project scaffolding."""
    configure_logging()
    args = get_parser().parse_args()

    directory = os.path.abspath(args.directory)
    show_name = args.show

    logger.info(f"\nScaffolding xil-pipeline workspace in: {directory}")
    logger.info(f"Show: {show_name}\n")

    scaffold(directory, show_name, season=args.season, season_title=args.season_title)
    print_getting_started(args.directory)


if __name__ == "__main__":
    main()
