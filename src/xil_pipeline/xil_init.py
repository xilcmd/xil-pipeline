# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scaffold a new xil-pipeline project workspace.

Creates the directory structure, project.json, speakers.json, and a
type-specific sample production script so a first-time user can immediately
run the pipeline stages in dry-run mode.

Usage::

    xil-init                              # scaffold in current directory
    xil-init my-show                      # scaffold in ./my-show/
    xil-init --show "Night Owls"          # custom show name
    xil-init --type podcast               # podcast (default)
    xil-init --type audiobook             # audiobook (V01C01 tags)
    xil-init --type drama                 # drama short / audio drama
    xil-init --type special               # special / one-off
"""

import argparse
import json
import os

from xil_pipeline.log_config import configure_logging, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Type-specific speaker lists
# ---------------------------------------------------------------------------

# Backward compatibility alias (pre-0.1.8 tests may import SAMPLE_SPEAKERS)
SAMPLE_SPEAKERS: list[dict] = [
    {"display": "HOST", "key": "host"},
    {"display": "CO-HOST", "key": "co_host"},
    {"display": "CALLER", "key": "caller"},
    {"display": "NARRATOR", "key": "narrator"},
]

SPEAKERS_BY_TYPE: dict[str, list[dict]] = {
    "podcast": SAMPLE_SPEAKERS,
    "audiobook": [
        {"display": "NARRATOR", "key": "narrator"},
    ],
    "drama": [
        {"display": "NARRATOR", "key": "narrator"},
        {"display": "ALICE", "key": "alice"},
        {"display": "BOB", "key": "bob"},
        {"display": "CHARLIE", "key": "charlie"},
        {"display": "DEZ", "key": "dez"},
    ],
    "special": [
        {"display": "HOST", "key": "host"},
        {"display": "NARRATOR", "key": "narrator"},
    ],
}

# ---------------------------------------------------------------------------
# Type-specific sample scripts
# ---------------------------------------------------------------------------

_PODCAST_SCRIPT = """\
{show}{season_part} Episode 1: "Pilot"{arc_part}

CAST:
* HOST — the radio host
* CO-HOST — the co-host
* CALLER — a mysterious caller

===

COLD OPEN

SCENE 1: THE STUDIO

[AMBIENCE: Radio station studio, low hum of equipment]

HOST
Good evening, and welcome to the show. I'm your host.

CO-HOST
And I'm your co-host. We have a packed show tonight.

[SFX: Phone ringing]

HOST (picking up phone)
We have our first caller of the night. You're on the air.

CALLER (distorted, nervous)
Hi... I wasn't sure I should call, but something strange happened last night.

[BEAT]

HOST
Take your time. Tell us what happened.

CALLER
I was driving home when the radio cut out. Just static. And then... a voice.

[BEAT — 3 SECONDS]

===

ACT ONE

SCENE 2: THE INTERVIEW

[AMBIENCE: Same studio, quieter now]

HOST
We're going to dig into that after this break. Don't go anywhere.

CO-HOST
When we come back — the story that has our phones ringing off the hook.

[MUSIC: Upbeat podcast bumper]

[BEAT]

===

MID-EPISODE BREAK

HOST
You're listening to {show}. I'm your host, back with my co-host.

===

ACT TWO

SCENE 3: WRAP-UP

CO-HOST
Fascinating stuff. Thank you to everyone who called in tonight.

HOST
That's all for this episode. Until next time.

[MUSIC: Closing theme, fade out]

===

CLOSING

HOST
{show} is produced by [Your Name]. Subscribe wherever you get your podcasts.

===

END OF EPISODE
"""

_AUDIOBOOK_SCRIPT = """\
{show}{season_part} Episode 1: "Chapter One"{arc_part}

CAST:
* NARRATOR — the narrator

===

PROLOGUE

NARRATOR
Before we begin, a note from the author.

[BEAT — 2 SECONDS]

NARRATOR
The events in this story are entirely fictional. Any resemblance to actual
persons, living or dead, is purely coincidental.

[BEAT — 3 SECONDS]

===

CHAPTER ONE

NARRATOR
It began, as most things do, on an ordinary morning.

[BEAT]

NARRATOR
The sun rose over the hills, casting long shadows across the valley below.
Nothing about that day seemed remarkable. And yet, by nightfall, everything
would change.

[SFX: Birds chirping, distant wind]

NARRATOR
She stepped onto the porch with her coffee and looked out at the horizon.
Something was different. She couldn't say what — only that the light seemed
wrong, somehow. Too bright. Too still.

[BEAT — 2 SECONDS]

NARRATOR
She went back inside.

[BEAT — 3 SECONDS]

===

CHAPTER TWO

NARRATOR
Three days passed before anyone noticed she was gone.

[BEAT]

NARRATOR
The neighbor across the lane saw the mail piling up, the lights going dark
one by one. She mentioned it to her husband. He said to leave it alone.

[BEAT — 2 SECONDS]

NARRATOR
She didn't leave it alone.

[BEAT — 3 SECONDS]

===

END OF EPISODE
"""

_DRAMA_SCRIPT = """\
{show}{season_part} Episode 1: "Pilot"{arc_part}

CAST:
* NARRATOR — the narrator
* ALICE — protagonist
* BOB — antagonist
* CHARLIE — a bystander

===

ACT ONE

SCENE 1: THE STREET

[AMBIENCE: City street, distant traffic, wind]

NARRATOR
The city never sleeps. But tonight, it should have.

[BEAT]

ALICE (quietly)
I shouldn't be here.

BOB (behind her)
And yet, here you are.

[SFX: Footsteps on wet pavement]

ALICE
How long have you been following me?

BOB (calm)
Long enough to know you found the documents.

[BEAT — 2 SECONDS]

ALICE
I don't know what you're talking about.

BOB
We both know that's not true.

[SFX: Car horn, distant]

CHARLIE (interrupting)
Hey — is everything all right over there?

[BEAT]

BOB (forced smile)
Just old friends catching up.

===

ACT TWO

SCENE 2: THE ALLEY

[AMBIENCE: Narrow alley, dripping water, distant music]

NARRATOR
Alice ran. She didn't look back.

[SFX: Running footsteps, splashing]

ALICE (breathless, to herself)
I need to get to the bridge. I need to warn them.

[BEAT — 3 SECONDS]

NARRATOR
The bridge was three blocks away. Three very long blocks.

[MUSIC: Tense underscore, building]

[BEAT]

CHARLIE (stepping out of shadow)
I was hoping I'd find you first.

ALICE
Charlie? What are you doing here?

CHARLIE (serious)
Keeping you alive. Come with me.

[SFX: Door opening]

===

END OF EPISODE
"""

_SPECIAL_SCRIPT = """\
{show}{season_part} Episode 1: "Special Presentation"{arc_part}

CAST:
* HOST — the host
* NARRATOR — the narrator

===

INTRO

[MUSIC: Fanfare, brief]

HOST
Welcome to this special presentation of {show}.

NARRATOR
What follows is a one-time look at something we don't often discuss.

[BEAT — 2 SECONDS]

===

SEGMENT 1

HOST
Tonight we're exploring something that has fascinated people for generations.

NARRATOR
The question is simple. The answer, anything but.

[SFX: Ambient texture, subtle]

HOST
Let's begin.

[BEAT]

===

SEGMENT 2

HOST
Thank you for staying with us.

NARRATOR
We leave you with this thought: the most important stories are the ones
we tell ourselves.

[BEAT — 3 SECONDS]

HOST
Until next time.

===

OUTRO

[MUSIC: Closing theme]

HOST
This has been {show}. Thank you for listening.

===

END OF EPISODE
"""

SCRIPTS_BY_TYPE: dict[str, str] = {
    "podcast":   _PODCAST_SCRIPT,
    "audiobook": _AUDIOBOOK_SCRIPT,
    "drama":     _DRAMA_SCRIPT,
    "special":   _SPECIAL_SCRIPT,
}

SAMPLE_TAG_BY_TYPE: dict[str, str] = {
    "podcast":   "S01E01",
    "audiobook": "V01C01",
    "drama":     "S01E01",
    "special":   "SP001",
}

GETTING_STARTED_BY_TYPE: dict[str, str] = {
    "podcast":   "S01E01",
    "audiobook": "V01C01",
    "drama":     "S01E01",
    "special":   "SP001",
}


def scaffold(
    directory: str,
    show_name: str,
    content_type: str = "podcast",
    season: int | None = None,
    season_title: str | None = None,
) -> None:
    """Create a new xil-pipeline workspace in *directory*.

    Args:
        directory: Target directory (created if it doesn't exist).
        show_name: Human-readable show name for project.json.
        content_type: Content type — ``"podcast"``, ``"audiobook"``,
            ``"drama"``, or ``"special"``.
        season: Optional season number for project.json and the sample script header.
        season_title: Optional season/arc title for project.json.
    """
    from xil_pipeline.models import show_slug

    os.makedirs(directory, exist_ok=True)
    slug = show_slug(show_name)

    # project.json
    project_path = os.path.join(directory, "project.json")
    if not os.path.exists(project_path):
        project_data: dict = {"show": show_name, "type": content_type}
        if season is not None:
            project_data["season"] = season
        if season_title is not None:
            project_data["season_title"] = season_title
        if content_type == "audiobook":
            project_data["tag_format"] = "V{volume:02d}C{chapter:02d}"
        with open(project_path, "w", encoding="utf-8") as f:
            json.dump(project_data, f, indent=2)
            f.write("\n")
        logger.info(f"  Created {project_path}")
    else:
        logger.info(f"  Skipped {project_path} (already exists)")

    # speakers.json
    speakers_path = os.path.join(directory, "speakers.json")
    if not os.path.exists(speakers_path):
        speakers = SPEAKERS_BY_TYPE.get(content_type, SPEAKERS_BY_TYPE["podcast"])
        with open(speakers_path, "w", encoding="utf-8") as f:
            json.dump(speakers, f, indent=2)
            f.write("\n")
        logger.info(f"  Created {speakers_path}")
    else:
        logger.info(f"  Skipped {speakers_path} (already exists)")

    # Subdirectories (new normalized layout — configs/{slug}/ for episode configs)
    for subdir in ("scripts", f"configs/{slug}", "parsed", "stems", "SFX", "daw", "masters", "cues"):
        path = os.path.join(directory, subdir)
        os.makedirs(path, exist_ok=True)

    # Sample script
    tag = SAMPLE_TAG_BY_TYPE.get(content_type, "S01E01")
    season_part = f" Season {season}:" if season is not None else ""
    arc_part = f' Arc: "{season_title}"' if season_title else ""
    script_path = os.path.join(directory, "scripts", f"sample_{tag}.md")
    if not os.path.exists(script_path):
        template = SCRIPTS_BY_TYPE.get(content_type, SCRIPTS_BY_TYPE["podcast"])
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(template.format(
                show=show_name,
                season_part=season_part,
                arc_part=arc_part,
            ))
        logger.info(f"  Created {script_path}")
    else:
        logger.info(f"  Skipped {script_path} (already exists)")


def print_getting_started(directory: str, content_type: str = "podcast") -> None:
    """Print a getting-started guide after scaffolding."""
    cd_prefix = f"cd {directory} && " if directory != "." else ""
    tag = SAMPLE_TAG_BY_TYPE.get(content_type, "S01E01")
    logger.info(f"""
Getting Started
===============

1. Install the pipeline:
   pip install xil-pipeline

2. Scan the sample script (pre-flight check):
   {cd_prefix}xil-scan scripts/sample_{tag}.md

3. Parse the script into structured JSON:
   {cd_prefix}xil-parse scripts/sample_{tag}.md --episode {tag}

4. Preview voice generation (no API key needed):
   {cd_prefix}xil-produce --episode {tag} --dry-run

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
        "--type", dest="content_type",
        choices=["podcast", "audiobook", "drama", "special"],
        default="podcast",
        help="Content type: podcast (default), audiobook, drama, special",
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
    content_type = args.content_type

    logger.info(f"\nScaffolding xil-pipeline workspace in: {directory}")
    logger.info(f"Show: {show_name}  Type: {content_type}\n")

    scaffold(directory, show_name, content_type=content_type,
             season=args.season, season_title=args.season_title)
    print_getting_started(args.directory, content_type=content_type)


if __name__ == "__main__":
    main()
