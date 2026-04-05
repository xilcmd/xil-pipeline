# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILP009 — Reverse Script Generator.

Reconstructs a readable markdown production script from a parsed JSON,
using cast config for speaker display names.  Produces a clean "revised"
version that reflects any post-parse edits (speaker reassignments,
section changes, direction_type reclassifications, etc.).
"""

import argparse
import json
import os
import sys

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner
from xil_pipeline.XILP001_script_parser import SECTION_MAP, SPEAKER_KEYS, load_speakers

logger = get_logger(__name__)

SCRIPT_NAME = os.path.basename(__file__)


def _build_reverse_mappings(
    speaker_keys: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build reverse lookup dicts for section slugs and speaker keys.

    Args:
        speaker_keys: Mapping from display names to normalized keys.
            Defaults to the module-level ``SPEAKER_KEYS`` from XILP001.

    Returns:
        A tuple of ``(section_slug_to_display, speaker_key_to_display)``.
    """
    if speaker_keys is None:
        speaker_keys = SPEAKER_KEYS

    # For sections with multiple aliases, prefer the longest key for each slug
    section_slug_to_display: dict[str, str] = {}
    for display, slug in sorted(SECTION_MAP.items(), key=lambda kv: len(kv[0])):
        section_slug_to_display[slug] = display

    # For speakers, prefer the canonical short form (first entry per key value)
    speaker_key_to_display: dict[str, str] = {}
    for display, key in speaker_keys.items():
        if key not in speaker_key_to_display:
            speaker_key_to_display[key] = display

    return section_slug_to_display, speaker_key_to_display


# Module-level defaults (built at import time from built-in speakers)
_SECTION_SLUG_TO_DISPLAY, _SPEAKER_KEY_TO_DISPLAY = _build_reverse_mappings()


def section_display_name(slug: str) -> str:
    """Convert a section slug back to its display header text."""
    return _SECTION_SLUG_TO_DISPLAY.get(slug, slug.upper().replace("-", " "))


def speaker_display_name(key: str) -> str:
    """Convert a speaker key back to its display name."""
    return _SPEAKER_KEY_TO_DISPLAY.get(key, key.upper())


def regenerate_script(parsed: dict, cast: dict | None = None) -> str:
    """Regenerate a markdown production script from parsed JSON.

    Args:
        parsed: The full parsed script dict (with metadata and entries).
        cast: Optional cast config dict for full_name lookups.

    Returns:
        The reconstructed markdown script as a string.
    """
    show = parsed.get("show", "Unknown Show")
    season = parsed.get("season")
    episode = parsed.get("episode", 1)
    title = parsed.get("title", "")
    season_title = parsed.get("season_title", "")

    lines: list[str] = []

    # Header line
    header = f"# {show}"
    if season is not None:
        header += f" Season {season}:"
    header += f" Episode {episode}:"
    if title:
        header += f' "{title}"'
    if season_title:
        header += f' Arc: "{season_title}"'
    lines.append(header)
    lines.append("")

    entries = parsed.get("entries", [])

    # Filter out preamble/postamble (injected by XILP002, not in original script)
    entries = [e for e in entries if e.get("seq", 0) >= 1
               and e.get("section") != "postamble"]

    after_header = False

    for entry in entries:
        entry_type = entry.get("type")
        text = entry.get("text", "")
        speaker = entry.get("speaker")
        direction = entry.get("direction")

        if entry_type == "section_header":
            lines.append("")
            lines.append(f"## {text}")
            lines.append("")
            after_header = True
            continue

        if entry_type == "scene_header":
            lines.append("")
            lines.append(f"## {text}")
            lines.append("")
            after_header = True
            continue

        # Insert === divider before the first direction/dialogue after a header
        if after_header and entry_type in ("direction", "dialogue"):
            lines.append("===")
            lines.append("")
            after_header = False

        if entry_type == "direction":
            lines.append(f"[{text}]")
            lines.append("")
            continue

        if entry_type == "dialogue":
            display_name = speaker_display_name(speaker) if speaker else "UNKNOWN"

            if direction:
                lines.append(f"{display_name} ({direction})")
            else:
                lines.append(display_name)
            lines.append(text)
            lines.append("")
            continue

    # End marker
    lines.append("END OF EPISODE")
    lines.append("")

    return "\n".join(lines)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-regen",
        description="Regenerate a production script markdown from parsed JSON.",
    )
    parser.add_argument("--episode", required=True,
                        help="Episode tag (e.g. S02E03)")
    parser.add_argument("--parsed", default=None,
                        help="Override parsed JSON path")
    parser.add_argument("--cast", default=None,
                        help="Override cast config path")
    parser.add_argument("--show", default=None,
                        help="Show name override (default: from project.json)")
    parser.add_argument("--output", default=None,
                        help="Output markdown path (default: scripts/revised_<slug>_{TAG}.md)")
    parser.add_argument("--speakers", default=None,
                        help="Path to speakers.json (default: auto-detect from CWD, then built-in)")
    return parser


def main():
    configure_logging()
    args = get_parser().parse_args()
    tag = args.episode

    with run_banner(SCRIPT_NAME):
        # Load speakers and rebuild reverse mappings
        _loaded_speakers, loaded_keys = load_speakers(args.speakers)
        global _SECTION_SLUG_TO_DISPLAY, _SPEAKER_KEY_TO_DISPLAY
        _SECTION_SLUG_TO_DISPLAY, _SPEAKER_KEY_TO_DISPLAY = _build_reverse_mappings(loaded_keys)

        slug = resolve_slug(args.show)
        p = derive_paths(slug, tag)
        parsed_path = args.parsed or p["parsed"]
        cast_path = args.cast or p["cast"]
        output_path = args.output or p["revised_script"]

        if not os.path.exists(parsed_path):
            logger.error(f"Parsed JSON not found: {parsed_path}")
            sys.exit(1)

        with open(parsed_path, encoding="utf-8") as f:
            parsed = json.load(f)

        cast = None
        if os.path.exists(cast_path):
            with open(cast_path, encoding="utf-8") as f:
                cast = json.load(f)

        script_text = regenerate_script(parsed, cast)

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(script_text)

        # Summary
        entry_count = len(parsed.get("entries", []))
        dialogue_count = parsed.get("stats", {}).get("dialogue_lines", 0)
        logger.info(f"  Regenerated script from {entry_count} entries ({dialogue_count} dialogue)")
        logger.info(f"  Written to: {output_path}")


if __name__ == "__main__":
    main()
