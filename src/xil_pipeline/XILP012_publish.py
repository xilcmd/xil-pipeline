# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILP012 — Social Media Post Draft Generator.

Reads a parsed episode JSON, builds a structured episode summary, and calls
the Claude API to produce three ready-to-edit Facebook post variants. Output
is an editable markdown file the producer reviews and pastes.

Post variants per episode:
    Hype     — new episode announcement, teaser tone, no spoilers past cold open
    Quote    — pull quote from cold open + tune-in call to action
    Spotlight — cast member feature (cycles by episode number mod cast count)

Output: ``posts/{slug}/{tag}_posts.md``

Usage::

    xil publish --episode S04E01 --dry-run
    xil publish --episode S04E01
    xil publish --all
    xil publish --episode S04E01 --platform instagram
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import sys
from typing import TYPE_CHECKING

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import CastConfiguration, derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

POSTS_DIR = "posts"

_SYSTEM_PROMPT = """\
You are a social media copywriter for the Berkshire Talking Chronicle, a radio reading service \
for people with visual impairments and print disabilities broadcasting from Pittsfield, Massachusetts.

You write warm, community-focused Facebook posts for The 413, an original radio drama series about \
WRRS Radio and the people of Berkshire County. The tone is: welcoming, local, proud of the community, \
enthusiastic about storytelling. Avoid corporate-speak. This is a volunteer-run community radio station.

You will produce exactly three post variants — Hype, Quote, and Spotlight — with those exact \
markdown headings. Each post should be complete, ready to copy-paste into Facebook with minimal editing. \
Include relevant emoji sparingly. Keep posts under 280 words each.\
"""


def _section_slug_to_label(slug: str) -> str:
    """Convert a section slug like 'cold-open' to 'Cold Open'."""
    replacements = {
        "cold-open": "Cold Open",
        "opening-credits": "Opening Credits",
        "act1": "Act One",
        "act2": "Act Two",
        "act3": "Act Three",
        "mid-break": "Mid-Episode Break",
        "post-interview": "Post-Interview",
        "closing": "Closing",
        "prologue": "Prologue",
        "epilogue": "Epilogue",
    }
    return replacements.get(slug, slug.replace("-", " ").title())


def extract_episode_summary(
    parsed: dict,
    cast_cfg: dict | None,
    master_path: str | None = None,
) -> dict:
    """Build a structured summary dict from parsed episode data.

    Returns::

        {
            "show": str, "season": int, "episode": int, "tag": str,
            "title": str, "season_title": str | None,
            "cold_open_scene": str,
            "cold_open_lines": [{"speaker": str, "text": str}, ...],
            "cast": [{"key": str, "full_name": str, "role": str}, ...],
            "section_arc": str,
            "runtime_minutes": int | None,
        }
    """
    entries = parsed.get("entries", [])
    stats = parsed.get("stats", {})

    # Cold open: first scene header text + first 3 dialogue lines
    cold_open_scene = ""
    cold_open_lines: list[dict] = []
    for entry in entries:
        if entry.get("section") != "cold-open":
            continue
        if entry.get("type") == "scene_header" and not cold_open_scene:
            cold_open_scene = entry.get("text", "")
        elif entry.get("type") == "dialogue" and len(cold_open_lines) < 3:
            cold_open_lines.append({
                "speaker": entry.get("speaker", ""),
                "text": entry.get("text", ""),
            })

    # Cast list: speakers who appear in stats.speakers AND have a cast config entry
    cast_members: list[dict] = []
    cast_dict = {}
    if cast_cfg:
        cast_dict = cast_cfg.get("cast", {})
    for speaker_key in stats.get("speakers", []):
        if speaker_key in cast_dict:
            cfg = cast_dict[speaker_key]
            full_name = cfg.get("full_name", speaker_key)
            role = cfg.get("role", "")
            # Trim role to first line if multi-line
            role = role.strip().split("\n")[0].strip()
            cast_members.append({"key": speaker_key, "full_name": full_name, "role": role})

    # Section arc: ordered unique section slugs → human labels
    seen: set[str] = set()
    section_labels: list[str] = []
    for entry in entries:
        sec = entry.get("section", "")
        if sec and sec not in seen and sec not in ("preamble", "postamble"):
            seen.add(sec)
            section_labels.append(_section_slug_to_label(sec))
    section_arc = " → ".join(section_labels) if section_labels else "unknown"

    # Runtime from master MP3 if present
    runtime_minutes: int | None = None
    if master_path and os.path.exists(master_path):
        try:
            from mutagen.mp3 import MP3
            audio = MP3(master_path)
            runtime_minutes = int(audio.info.length // 60)
        except Exception:
            pass

    tag = f"S{parsed['season']:02d}E{parsed['episode']:02d}"

    return {
        "show": parsed.get("show", ""),
        "season": parsed.get("season"),
        "episode": parsed.get("episode"),
        "tag": tag,
        "title": parsed.get("title", ""),
        "season_title": parsed.get("season_title"),
        "cold_open_scene": cold_open_scene,
        "cold_open_lines": cold_open_lines,
        "cast": cast_members,
        "section_arc": section_arc,
        "runtime_minutes": runtime_minutes,
    }


def build_user_message(summary: dict, platform: str, spotlight_index: int) -> str:
    """Compose the Claude user message from an episode summary dict."""
    lines: list[str] = []
    show = summary["show"]
    tag = summary["tag"]
    title = summary["title"]
    season_title = summary.get("season_title") or ""

    lines.append(f"Show: {show}")
    lines.append(f"Episode: {tag} — \"{title}\"")
    if season_title:
        lines.append(f"Arc/Season title: {season_title}")
    lines.append(f"Platform: {platform}")
    if summary.get("runtime_minutes"):
        lines.append(f"Runtime: approximately {summary['runtime_minutes']} minutes")
    lines.append("")

    lines.append("Section arc:")
    lines.append(f"  {summary['section_arc']}")
    lines.append("")

    if summary.get("cold_open_scene"):
        lines.append(f"Cold open setting: {summary['cold_open_scene']}")
    if summary.get("cold_open_lines"):
        lines.append("Cold open excerpt (first 3 lines):")
        for dl in summary["cold_open_lines"]:
            # Resolve full name if available from cast
            speaker_key = dl["speaker"]
            display = speaker_key
            for cm in summary.get("cast", []):
                if cm["key"] == speaker_key:
                    display = cm["full_name"]
                    break
            # Truncate very long lines for the prompt
            text = dl["text"]
            if len(text) > 200:
                text = text[:197] + "…"
            lines.append(f"  {display}: \"{text}\"")
    lines.append("")

    if summary.get("cast"):
        lines.append("Cast:")
        for cm in summary["cast"]:
            role_str = f" — {cm['role']}" if cm.get("role") else ""
            lines.append(f"  {cm['full_name']}{role_str}")
        lines.append("")

        # Spotlight target
        spotlight_cast = summary["cast"]
        target = spotlight_cast[spotlight_index % len(spotlight_cast)]
        lines.append(f"Spotlight post subject: {target['full_name']} ({target.get('role', '')})")
        lines.append("")

    lines.append(
        "Write three Facebook post variants using exactly these markdown headings:\n"
        "## Hype Post\n"
        "## Quote Post\n"
        "## Spotlight Post\n\n"
        "Hype: New episode announcement, teaser tone. Mention the show name, episode title, "
        "and Berkshire Talking Chronicle. No spoilers beyond the cold open setting.\n"
        "Quote: Pull a memorable line from the cold open excerpt above. Format as a blockquote "
        "or quoted text. Add a brief tune-in call to action.\n"
        f"Spotlight: Feature the spotlight subject. Connect their character to the episode theme."
    )

    return "\n".join(lines)


def call_claude_api(
    system_prompt: str,
    user_message: str,
    model: str = "claude-haiku-4-5-20251001",
) -> str:
    """Call the Anthropic API and return the text response."""
    try:
        import anthropic
    except ImportError:
        logger.error(
            "anthropic package not installed. Run: pip install 'xil-pipeline[publish]'"
        )
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Export your API key before running xil publish."
        )
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text


def write_posts_file(
    output_path: str,
    posts_text: str,
    summary: dict,
    platform: str,
) -> None:
    """Write the generated posts to a markdown file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    show = summary["show"]
    tag = summary["tag"]
    title = summary["title"]
    today = datetime.date.today().isoformat()

    header = (
        f"# {show} — {tag} \"{title}\" Social Posts\n"
        f"Generated: {today}  |  Platform: {platform}\n\n"
        f"---\n\n"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write(posts_text)
        if not posts_text.endswith("\n"):
            f.write("\n")


def _find_all_parsed(slug: str) -> list[str]:
    """Return sorted list of parsed JSON paths for the given slug."""
    pattern = os.path.join("parsed", slug, "parsed_*.json")
    paths = sorted(glob.glob(pattern))
    # Exclude orig_parsed and pre_splice backups
    return [p for p in paths if os.path.basename(p).startswith("parsed_")]


def publish_episode(
    slug: str,
    tag: str,
    platform: str = "facebook",
    dry_run: bool = False,
    model: str = "claude-haiku-4-5-20251001",
) -> bool:
    """Generate social posts for one episode. Returns True on success."""
    p = derive_paths(slug, tag)
    parsed_path = p["parsed"]
    cast_path = p["cast"]
    master_path = p["master"]
    posts_path = os.path.join(POSTS_DIR, slug, f"{tag}_posts.md")

    if not os.path.exists(parsed_path):
        logger.warning(f"  Skipping {tag} — parsed JSON not found: {parsed_path}")
        return False

    with open(parsed_path, encoding="utf-8") as f:
        parsed = json.load(f)

    cast_cfg: dict | None = None
    if os.path.exists(cast_path):
        with open(cast_path, encoding="utf-8") as f:
            cast_cfg = json.load(f)
    else:
        logger.warning(f"  Cast config not found: {cast_path} — cast list will be empty")

    summary = extract_episode_summary(parsed, cast_cfg, master_path)
    episode_number = summary.get("episode") or 0
    cast_count = max(len(summary.get("cast", [])), 1)
    spotlight_index = (episode_number - 1) % cast_count

    user_message = build_user_message(summary, platform, spotlight_index)

    if dry_run:
        logger.info(f"\n--- Dry run: {tag} ---")
        logger.info("\n[SYSTEM PROMPT]\n" + _SYSTEM_PROMPT)
        logger.info("\n[USER MESSAGE]\n" + user_message)
        import math
        # Rough token estimate: 1 token ≈ 4 chars
        sys_tokens = math.ceil(len(_SYSTEM_PROMPT) / 4)
        user_tokens = math.ceil(len(user_message) / 4)
        logger.info(
            f"\nEstimated input tokens: ~{sys_tokens + user_tokens} "
            f"(system: ~{sys_tokens}, user: ~{user_tokens})"
        )
        logger.info(f"Output would be written to: {posts_path}")
        return True

    logger.info(f"  Generating posts for {tag}...")
    try:
        posts_text = call_claude_api(_SYSTEM_PROMPT, user_message, model=model)
    except Exception as exc:
        logger.error(f"  API error for {tag}: {exc}")
        return False

    write_posts_file(posts_path, posts_text, summary, platform)
    logger.info(f"  Written: {posts_path}")
    return True


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-publish",
        description="Generate social media post drafts from parsed episode data",
    )
    tag_group = parser.add_mutually_exclusive_group()
    tag_group.add_argument("--episode", help="Episode tag (e.g. S04E01)")
    tag_group.add_argument("--tag", help="Raw content tag (e.g. V01C03)")
    parser.add_argument(
        "--all", action="store_true",
        help="Generate posts for every parsed episode under the current show slug",
    )
    parser.add_argument(
        "--show", default=None,
        help="Show name override (default: from project.json)",
    )
    parser.add_argument(
        "--platform", default="facebook", choices=["facebook", "instagram"],
        help="Target platform — affects post length/style guidance (default: facebook)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print prompt and token estimate without making an API call or writing files",
    )
    parser.add_argument(
        "--model", default="claude-haiku-4-5-20251001",
        help="Claude model ID (default: claude-haiku-4-5-20251001)",
    )
    return parser


def main() -> None:
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        if not args.episode and not args.tag and not args.all:
            logger.error("Specify --episode TAG, --tag TAG, or --all")
            sys.exit(1)

        slug = resolve_slug(args.show)

        if args.all:
            parsed_paths = _find_all_parsed(slug)
            if not parsed_paths:
                logger.warning(f"No parsed JSON files found under parsed/{slug}/")
                sys.exit(1)
            logger.info(f"Batch mode: {len(parsed_paths)} episode(s) found for '{slug}'")
            success = 0
            for path in parsed_paths:
                # Derive tag from filename: parsed_S01E01.json → S01E01
                basename = os.path.basename(path)
                episode_tag = basename.removeprefix("parsed_").removesuffix(".json")
                if publish_episode(
                    slug, episode_tag,
                    platform=args.platform,
                    dry_run=args.dry_run,
                    model=args.model,
                ):
                    success += 1
            logger.info(f"\n{success}/{len(parsed_paths)} episodes processed.")
        else:
            tag = args.episode or args.tag
            ok = publish_episode(
                slug, tag,
                platform=args.platform,
                dry_run=args.dry_run,
                model=args.model,
            )
            if not ok:
                sys.exit(1)


if __name__ == "__main__":
    main()
