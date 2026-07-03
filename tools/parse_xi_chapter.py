"""
parse_xi_chapter.py
-------------------
Parse an ElevenLabs Studio chapter content JSON (the chapter endpoint XHR)
into a normalized, pipeline-ready dialog block list.

The chapter endpoint returns the full script content for a single chapter:
  GET /v1/studio/projects/{project_id}/chapters/{chapter_id}/content
  (or equivalent — captured via DevTools Network tab)

Usage:
    # Standalone (voice IDs only, no character name resolution):
    python parse_xi_chapter.py <chapter.json> [-o output.json]

    # With project JSON for character name resolution:
    python parse_xi_chapter.py <chapter.json> -p <project.json> [-o output.json]

    # With explicit episode tag:
    python parse_xi_chapter.py <chapter.json> -p <project.json> --episode S02E03 [-o output.json]

Output schema:
    {
      "chapter": { meta },
      "episode": "S02E03",           # if --episode provided
      "stats": { block counts, conversion status },
      "blocks": [
        {
          "index":        0,          # sequential position in script
          "block_id":     "...",
          "order":        "a0",       # ElevenLabs sort key
          "voice_id":     "6wrQFQPr...",
          "character":    "ADAM",     # resolved if project JSON provided
          "text":         "It's 7:14 AM...",
          "is_converted": false,
          "duration_ms":  null,       # populated if block was generated
          "muted":        false,
          "volume_gain_db": 0.0,
          "last_change_unix_ms": ...,
          "last_converted_at_unix_ms": null
        },
        ...
      ]
    }
"""

import json
import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Voice map loader (from project JSON via parse_xi_project logic)
# ---------------------------------------------------------------------------

def load_voice_map(project_path: str) -> dict:
    """
    Load voice_id -> character name from a project JSON.
    Uses base_voices name as the character label.
    Returns empty dict if project_path is None.
    """
    if not project_path:
        return {}

    p = Path(project_path)
    if not p.exists():
        print(f"WARNING: project file {p} not found, skipping voice resolution",
              file=sys.stderr)
        return {}

    with p.open() as f:
        project = json.load(f)

    # If it's already a parsed output from parse_xi_project.py
    if "voice_map" in project:
        return {vid: v["name"] for vid, v in project["voice_map"].items()}

    # Raw XHR project JSON
    base = {v["voice_id"]: v.get("name", "") for v in project.get("base_voices", [])}
    return base


# ---------------------------------------------------------------------------
# Block normalizer
# ---------------------------------------------------------------------------

def normalize_block(block: dict, index: int, voice_map: dict) -> dict:
    children = block.get("children", [])
    # All observed blocks have exactly one tts_node child
    child = children[0] if children else {}

    voice_id = child.get("settings", {}).get("project_voice_ref_id", "") or ""
    character = voice_map.get(voice_id, "") if voice_map else ""

    # duration_ms: from tts_element if block was generated
    tts_element = child.get("tts_element") or {}
    duration_ms = tts_element.get("duration_ms")  # None if not yet generated

    return {
        "index":                   index,
        "block_id":                block.get("block_id"),
        "order":                   block.get("order"),
        "track_id":                block.get("track_id"),
        "sub_type":                block.get("sub_type"),
        "voice_id":                voice_id,
        "character":               character,
        "text":                    (child.get("text") or "").strip(),
        "is_converted":            block.get("is_converted", False),
        "duration_ms":             duration_ms,
        "muted":                   child.get("muted", False),
        "volume_gain_db":          child.get("volume_gain_db", 0.0),
        "fade_in_ms":              child.get("fade_in_ms", 0),
        "fade_out_ms":             child.get("fade_out_ms", 0),
        "regeneration_count":      block.get("regeneration_count", 0),
        "last_change_unix_ms":     block.get("last_change_unix_ms"),
        "last_converted_at_unix_ms": block.get("last_converted_at_unix_ms"),
    }


# ---------------------------------------------------------------------------
# Chapter meta
# ---------------------------------------------------------------------------

def normalize_chapter_meta(chapter: dict) -> dict:
    return {
        "chapter_id":              chapter.get("chapter_id"),
        "name":                    chapter.get("name"),
        "state":                   chapter.get("state"),
        "can_be_downloaded":       chapter.get("can_be_downloaded", False),
        "has_video":               chapter.get("has_video", False),
        "last_conversion_date_unix": chapter.get("last_conversion_date_unix"),
        "last_message_at_ms":      chapter.get("last_message_at_ms"),
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def build_stats(blocks: list) -> dict:
    total = len(blocks)
    converted = sum(1 for b in blocks if b["is_converted"])
    has_duration = sum(1 for b in blocks if b["duration_ms"] is not None)
    total_duration_ms = sum(b["duration_ms"] for b in blocks if b["duration_ms"])
    chars_by_voice = {}
    lines_by_character = {}
    for b in blocks:
        vid = b["voice_id"]
        char = b["character"] or vid[:8]
        chars_by_voice[char] = chars_by_voice.get(char, 0) + len(b["text"])
        lines_by_character[char] = lines_by_character.get(char, 0) + 1

    return {
        "total_blocks":         total,
        "converted_blocks":     converted,
        "unconverted_blocks":   total - converted,
        "blocks_with_duration": has_duration,
        "total_duration_ms":    total_duration_ms,
        "total_duration_tc":    _ms_to_tc(total_duration_ms),
        "total_chars":          sum(len(b["text"]) for b in blocks),
        "estimated_runtime_min": round(sum(len(b["text"]) for b in blocks) / 776, 1),
        "lines_by_character":   dict(sorted(lines_by_character.items(),
                                            key=lambda x: -x[1])),
        "chars_by_voice":       dict(sorted(chars_by_voice.items(),
                                            key=lambda x: -x[1])),
    }


def _ms_to_tc(ms: int) -> str:
    if not ms:
        return "00:00.000"
    total_s, millis = divmod(ms, 1000)
    minutes, seconds = divmod(total_s, 60)
    return f"{int(minutes):02d}:{int(seconds):02d}.{int(millis):03d}"


# ---------------------------------------------------------------------------
# Main parse
# ---------------------------------------------------------------------------

def parse_chapter(chapter: dict, voice_map: dict, episode: str = None) -> dict:
    content = chapter.get("content", {})
    raw_blocks = content.get("blocks", [])

    blocks = [
        normalize_block(b, i, voice_map)
        for i, b in enumerate(raw_blocks)
    ]

    result = {
        "chapter": normalize_chapter_meta(chapter),
        "blocks":  blocks,
        "stats":   build_stats(blocks),
    }

    if episode:
        result["episode"] = episode

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse ElevenLabs chapter content JSON into normalized dialog blocks"
    )
    parser.add_argument("input",
                        help="Path to chapter content JSON (XHR capture)")
    parser.add_argument("-p", "--project",
                        help="Path to project JSON (from parse_xi_project or raw XHR) "
                             "for character name resolution",
                        default=None)
    parser.add_argument("--episode",
                        help="Episode tag to embed in output (e.g. S02E03)",
                        default=None)
    parser.add_argument("-o", "--output",
                        help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        sys.exit(1)

    with src.open() as f:
        chapter = json.load(f)

    voice_map = load_voice_map(args.project)
    result = parse_chapter(chapter, voice_map, episode=args.episode)

    out_json = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(out_json)
        print(f"Written to {args.output}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
