# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pre-flight scanner for production scripts.

Reads a raw markdown script, applies the same two-pass normalization that
XILP001 uses, then scans every ALL-CAPS candidate line and reports which
speakers and sections are recognized vs. unknown — before any parsing state
machine runs.

Use this to catch missing KNOWN_SPEAKERS or SECTION_MAP entries before they
cause silent failures in XILP001.

Usage::

    python XILP000_script_scanner.py "scripts/<script>.md"
    python XILP000_script_scanner.py "scripts/<script>.md" --json
"""

import argparse
import json
import os
import sys

from xil_pipeline.sfx_common import run_banner
from xil_pipeline.XILP001_script_parser import (
    SECTION_MAP,
    is_divider,
    is_scene_header,
    is_section_header,
    is_stage_direction,
    load_speakers,
    parse_script_header,
    strip_markdown_escapes,
    strip_markdown_formatting,
    try_match_speaker,
)

# ---------------------------------------------------------------------------
# Candidate heuristic
# ---------------------------------------------------------------------------

def is_all_caps_candidate(line: str) -> bool:
    """Return True if *line* is a bare ALL-CAPS line worth classifying.

    Excludes dividers, stage directions, scene headers, and very short or very
    long strings.  Anything that passes is either a speaker name, a section
    header, or an unrecognized ALL-CAPS label.
    """
    if len(line) < 2 or len(line) >= 80:
        return False
    if line != line.upper():
        return False
    if is_divider(line):
        return False
    if is_stage_direction(line):
        return False
    if is_scene_header(line):
        return False
    if line.endswith(":"):          # metadata labels like "CAST:"
        return False
    return True


# ---------------------------------------------------------------------------
# Loader / normalizer
# ---------------------------------------------------------------------------

def load_and_normalize(path: str) -> list[str]:
    """Read *path* and apply the two-pass markdown normalization.

    Returns a list of individual lines (including blank lines) after both
    ``strip_markdown_escapes`` and ``strip_markdown_formatting`` have been
    applied.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text = strip_markdown_escapes(text)
    text = strip_markdown_formatting(text)
    return text.split("\n")


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def scan_script(
    lines: list[str],
    known_speakers: list[str] | None = None,
    speaker_keys: dict[str, str] | None = None,
) -> dict:
    """Scan normalized *lines* and classify every ALL-CAPS candidate.

    Args:
        lines: Normalized script lines.
        known_speakers: Ordered list of speaker display names (longest-first).
            Defaults to the module-level speakers from XILP001.
        speaker_keys: Mapping from display names to normalized keys.
            Defaults to the module-level speakers from XILP001.

    Returns a dict::

        {
            "sections":     [{"text": str, "slug": str, "line": int}, ...],
            "speakers":     {key: {"display": str, "count": int, "lines": [int, ...]}, ...},
            "unrecognized": [{"text": str, "lines": [int, ...]}, ...],
        }
    """
    sections: list[dict] = []
    speakers: dict[str, dict] = {}
    unrecognized: dict[str, dict] = {}   # keyed by text for deduplication

    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        lineno = i + 1

        if not line:
            continue

        # Stop at end-of-script markers
        if line.startswith("END OF"):
            break

        if not is_all_caps_candidate(line):
            continue

        # Try section header first
        if is_section_header(line):
            sections.append({
                "text": line,
                "slug": SECTION_MAP[line.strip()],
                "line": lineno,
            })
            continue

        # Try speaker match
        match = try_match_speaker(line, known_speakers, speaker_keys)
        if match:
            speaker_key, _direction, _spoken = match
            display = line.split("(")[0].strip()  # display name without direction
            if speaker_key not in speakers:
                speakers[speaker_key] = {
                    "display": display,
                    "count": 0,
                    "lines": [],
                }
            speakers[speaker_key]["count"] += 1
            speakers[speaker_key]["lines"].append(lineno)
            continue

        # Unrecognized candidate
        if line not in unrecognized:
            unrecognized[line] = {"text": line, "lines": []}
        unrecognized[line]["lines"].append(lineno)

    return {
        "sections": sections,
        "speakers": speakers,
        "unrecognized": list(unrecognized.values()),
    }


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def format_report(scan: dict, header: dict) -> str:
    """Render *scan* results as a human-readable text report."""
    lines: list[str] = []

    show = header.get("show", "")
    season = header.get("season", "")
    episode = header.get("episode", "")
    title = header.get("title", "")
    ep_tag = f"S{season:02d}E{episode:02d}" if season and episode else ""
    headline = " — ".join(filter(None, [show, ep_tag, title]))
    if headline:
        lines.append(f"=== {headline} ===")
    lines.append("")

    # Sections
    n_sections = len(scan["sections"])
    lines.append(f"SECTIONS ({n_sections} found)")
    if n_sections:
        for s in scan["sections"]:
            lines.append(f"  ✓  {s['text']:<30} → {s['slug']}")
    else:
        lines.append("  (none)")
    lines.append("")

    # Speakers
    n_speakers = len(scan["speakers"])
    lines.append(f"SPEAKERS ({n_speakers} found)")
    if n_speakers:
        for key, info in sorted(scan["speakers"].items()):
            lines.append(
                f"  ✓  {info['display']:<18} → {key:<18} ({info['count']} lines)"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    # Unrecognized
    n_unknown = len(scan["unrecognized"])
    if n_unknown:
        lines.append(f"UNRECOGNIZED CANDIDATES ({n_unknown} — action needed before XILP001)")
        for u in scan["unrecognized"]:
            line_list = ", ".join(str(ln) for ln in u["lines"][:5])
            if len(u["lines"]) > 5:
                line_list += f" (+{len(u['lines']) - 5} more)"
            lines.append(f"  ⚠  {u['text']:<30}  lines: {line_list}")
        lines.append("")
        lines.append(
            f"⚠️  {n_unknown} unrecognized candidate(s). "
            "Add to speakers.json or SECTION_MAP before parsing."
        )
    else:
        lines.append("UNRECOGNIZED CANDIDATES")
        lines.append("  (none)")
        lines.append("")
        lines.append("✅  All sections and speakers recognized — safe to run XILP001.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    with run_banner():
        parser = argparse.ArgumentParser(
            description="Pre-flight scanner: check a production script for unknown speakers/sections."
        )
        parser.add_argument("path", help="Path to the markdown production script")
        parser.add_argument(
            "--json", action="store_true",
            help="Output machine-readable JSON instead of the human report"
        )
        parser.add_argument("--speakers", default=None,
                            help="Path to speakers.json (default: auto-detect from CWD, then built-in)")
        args = parser.parse_args()

        if not os.path.exists(args.path):
            print(f"[ERROR] File not found: {args.path}")
            sys.exit(1)

        # Load speakers
        known_speakers, speaker_keys = load_speakers(args.speakers)

        lines = load_and_normalize(args.path)

        # Extract header for display — parse_script_header returns (show, season, episode, title)
        header = {}
        for line in lines[:10]:
            if line.strip():
                result = parse_script_header(line)
                if result:
                    show, season, episode, title = result
                    header = {"show": show, "season": season, "episode": episode, "title": title}
                break

        scan = scan_script(lines, known_speakers, speaker_keys)

        if args.json:
            print(json.dumps(scan, indent=2))
        else:
            print(format_report(scan, header))

        if scan["unrecognized"]:
            sys.exit(1)


if __name__ == "__main__":
    main()
