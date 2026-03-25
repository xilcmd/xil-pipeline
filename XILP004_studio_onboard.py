"""Onboard an episode to an ElevenLabs Studio project.

Reads a parsed script JSON and cast configuration, then builds the
``from_content_json`` payload expected by the ElevenLabs Studio Projects
API.  Each dialogue line is tagged with the correct ``voice_id`` so that
speaker names never appear in TTS text.

Usage::

    python XILP004_studio_onboard.py --episode S01E02 --dry-run
    python XILP004_studio_onboard.py --episode S01E02
    python XILP004_studio_onboard.py --episode S01E02 --quality high
"""

import os
import sys
import json
import argparse

from elevenlabs.client import ElevenLabs
from sfx_common import run_banner
from models import resolve_slug, derive_paths

# ---------------------------------------------------------------------------
# ElevenLabs client (lazily used — only needed for non-dry-run)
# ---------------------------------------------------------------------------

client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_episode(episode_tag: str, slug: str | None = None):
    """Load parsed JSON and cast config for *episode_tag* (e.g. ``S01E02``).

    Validates that no cast member has ``voice_id == "TBD"``.

    Returns:
        Tuple of (parsed_data, cast_data).

    Raises:
        SystemExit: If files are missing or a voice_id is TBD.
    """
    s = slug or resolve_slug()
    p = derive_paths(s, episode_tag)
    parsed_path = p["parsed"]
    cast_path = p["cast"]

    if not os.path.exists(parsed_path):
        print(f"[ERROR] Parsed file not found: {parsed_path}")
        sys.exit(1)

    if not os.path.exists(cast_path):
        print(f"[ERROR] Cast file not found: {cast_path}")
        sys.exit(1)

    with open(parsed_path, encoding="utf-8") as f:
        parsed = json.load(f)

    with open(cast_path, encoding="utf-8") as f:
        cast = json.load(f)

    # Validate no TBD voice IDs
    tbd_speakers = [
        key for key, info in cast["cast"].items()
        if info.get("voice_id", "TBD") == "TBD"
    ]
    if tbd_speakers:
        print(f"[ERROR] TBD voice_id for: {', '.join(tbd_speakers)}")
        print("        Assign voice IDs in cast config before onboarding.")
        sys.exit(1)

    return parsed, cast


# ---------------------------------------------------------------------------
# Content JSON builder
# ---------------------------------------------------------------------------

def build_content_json(parsed: dict, cast: dict) -> list[dict]:
    """Transform parsed entries into the ``from_content_json`` chapter list.

    Mapping rules:

    - ``section_header`` → new chapter (``name`` = section text)
    - ``scene_header`` → block with ``sub_type: "h2"``, narrator voice
    - ``dialogue`` → block with ``sub_type: "p"``, speaker's ``voice_id``
    - ``direction`` → **skipped** (SFX/BEAT/AMBIENCE not voiced)

    Returns:
        List of chapter dicts ready for ``json.dumps()``.
    """
    # Determine narrator voice (first Host/Narrator, or first cast member)
    narrator_voice = None
    for key, info in cast["cast"].items():
        if info.get("role") == "Host/Narrator":
            narrator_voice = info["voice_id"]
            break
    if narrator_voice is None:
        # Fallback: first cast member
        narrator_voice = next(iter(cast["cast"].values()))["voice_id"]

    chapters = []
    current_chapter = None

    for entry in parsed["entries"]:
        entry_type = entry["type"]

        if entry_type == "section_header":
            current_chapter = {
                "name": entry["text"],
                "blocks": [],
            }
            chapters.append(current_chapter)

        elif entry_type == "scene_header":
            if current_chapter is None:
                current_chapter = {"name": "Untitled", "blocks": []}
                chapters.append(current_chapter)
            current_chapter["blocks"].append({
                "sub_type": "h2",
                "nodes": [
                    {
                        "type": "tts_node",
                        "text": entry["text"],
                        "voice_id": narrator_voice,
                    }
                ],
            })

        elif entry_type == "dialogue":
            if current_chapter is None:
                current_chapter = {"name": "Untitled", "blocks": []}
                chapters.append(current_chapter)

            speaker_key = entry["speaker"]
            voice_id = cast["cast"].get(speaker_key, {}).get("voice_id", narrator_voice)

            current_chapter["blocks"].append({
                "sub_type": "p",
                "nodes": [
                    {
                        "type": "tts_node",
                        "text": entry["text"],
                        "voice_id": voice_id,
                    }
                ],
            })

        # direction entries are skipped

    return chapters


# ---------------------------------------------------------------------------
# API quota check
# ---------------------------------------------------------------------------

def check_elevenlabs_quota() -> int | None:
    """Display current ElevenLabs API character usage and return remaining."""
    try:
        user_info = client.user.get()
        sub = user_info.subscription
        used = sub.character_count
        limit = sub.character_limit
        remaining = limit - used
        print(f"\n{'=' * 40}")
        print(f"ELEVENLABS API STATUS:")
        print(f"  Tier:      {sub.tier.upper()}")
        print(f"  Usage:     {used:,} / {limit:,} characters")
        print(f"  Remaining: {remaining:,}")
        print(f"{'=' * 40}\n")
        return remaining
    except Exception as e:
        print(f"\n[!] API Error: Unable to fetch subscription data.")
        print(f"    Details: {e}")
        return None


# ---------------------------------------------------------------------------
# Project creation
# ---------------------------------------------------------------------------

def create_project(name: str, content_json: list[dict], *,
                   default_voice_id: str,
                   model_id: str = "eleven_v3",
                   quality: str = "standard"):
    """Create an ElevenLabs Studio project from content JSON.

    Args:
        name: Project name.
        content_json: Chapter/block/node structure.
        default_voice_id: Narrator voice for titles and fallback.
        model_id: TTS model identifier.
        quality: Quality preset (standard/high/ultra/ultra_lossless).

    Returns:
        API response with ``project_id``.
    """
    response = client.studio.projects.create(
        name=name,
        default_title_voice_id=default_voice_id,
        default_paragraph_voice_id=default_voice_id,
        default_model_id=model_id,
        from_content_json=json.dumps(content_json),
        quality_preset=quality,
    )
    return response


# ---------------------------------------------------------------------------
# Dry-run display
# ---------------------------------------------------------------------------

def dry_run(chapters: list[dict], cast: dict) -> None:
    """Pretty-print the content structure without calling the API."""
    # Build reverse map: voice_id → character name
    voice_map = {}
    for key, info in cast["cast"].items():
        voice_map[info["voice_id"]] = info.get("full_name", key)

    total_blocks = 0
    total_chars = 0

    print("\n" + "=" * 60)
    print("STUDIO PROJECT — DRY RUN")
    print("=" * 60)

    for chapter in chapters:
        print(f"\n  Chapter: {chapter['name']}")
        block_count = len(chapter["blocks"])
        total_blocks += block_count
        char_count = sum(
            len(node["text"])
            for block in chapter["blocks"]
            for node in block["nodes"]
        )
        total_chars += char_count

        # Show voice assignments in this chapter
        voices_used = set()
        for block in chapter["blocks"]:
            for node in block["nodes"]:
                vid = node.get("voice_id")
                if vid:
                    voices_used.add(voice_map.get(vid, vid))

        print(f"    Blocks: {block_count}  |  Characters: {char_count:,}")
        print(f"    Voices: {', '.join(sorted(voices_used))}")

    print(f"\n  TOTAL: {len(chapters)} chapters, {total_blocks} blocks, {total_chars:,} characters")
    print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    with run_banner():
        parser = argparse.ArgumentParser(
            description="Onboard an episode to an ElevenLabs Studio project."
        )
        parser.add_argument(
            "--episode", required=True,
            help="Episode tag (e.g. S01E02)"
        )
        parser.add_argument(
            "--show", default=None,
            help="Show name override (default: from project.json)"
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Build and display content JSON without calling the API"
        )
        parser.add_argument(
            "--quality", default="standard",
            choices=["standard", "high", "ultra", "ultra_lossless"],
            help="Quality preset (default: standard)"
        )
        parser.add_argument(
            "--model", default="eleven_v3",
            help="TTS model ID (default: eleven_v3)"
        )

        args = parser.parse_args()

        slug = resolve_slug(args.show)
        parsed, cast = load_episode(args.episode, slug=slug)
        chapters = build_content_json(parsed, cast)

        if args.dry_run:
            dry_run(chapters, cast)
            return

        # Determine narrator voice for defaults
        narrator_voice = None
        for info in cast["cast"].values():
            if info.get("role") == "Host/Narrator":
                narrator_voice = info["voice_id"]
                break
        if narrator_voice is None:
            narrator_voice = next(iter(cast["cast"].values()))["voice_id"]

        show = parsed.get("show", "THE 413")
        title = parsed.get("title", args.episode)
        project_name = f"XILP004 - {show} — {title} ({args.episode})"

        print(f"Creating Studio project: {project_name}")
        check_elevenlabs_quota()

        response = create_project(
            name=project_name,
            content_json=chapters,
            default_voice_id=narrator_voice,
            model_id=args.model,
            quality=args.quality,
        )

        print(f"\nProject created successfully!")
        print(f"  Project ID: {response.project.project_id}")


if __name__ == "__main__":
    main()
