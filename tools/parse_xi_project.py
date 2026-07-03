"""
parse_xi_project.py
-------------------
Parse an ElevenLabs Studio project JSON export into a normalized
pipeline-ready structure for xil-pipeline / ruffcut-pipeline.

Usage:
    python parse_xi_project.py <project.json> [-o output.json]

Output schema:
    {
      "project": { meta },
      "voice_map": { voice_id -> character info },
      "chapters": [ { chapter meta } ],
      "assets": [ { normalized asset } ]
    }
"""

import json
import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Heuristic type classification from filename
# ---------------------------------------------------------------------------

# Order matters — first match wins
FILENAME_TYPE_RULES = [
    (r"ambien(ce|t|s)",         "ambiance"),
    (r"music|score|theme|ost|soundtrack|legacy.*(intro|one)|intro.*413", "music"),
    (r"sign[-_]?on|bumper",     "music"),          # station IDs / bumpers
    (r"behind.the.scene|interview|beyond.the.scene", "bonus"),
    (r"wind|rain|storm|crowd|walla|room.tone|noise|static",  "ambiance"),
    (r"footstep|walk|step",     "sfx"),
    (r"door|knock|creak",       "sfx"),
    (r"lamp|light|switch|click","sfx"),
    (r"paper|rustle|rustle",    "sfx"),
    (r"key|keys|rattle|jingle", "sfx"),
    (r"throat|cough|sneeze|breath", "sfx"),
    (r"notif|ding|ping|alert|chime", "sfx"),
    (r"download",               "unknown"),        # generic placeholder filenames
]

def classify_filename(filename: str) -> str:
    name = filename.lower()
    for pattern, label in FILENAME_TYPE_RULES:
        if re.search(pattern, name):
            return label
    return "sfx"   # safe default for unmatched audio


# ---------------------------------------------------------------------------
# ms helpers
# ---------------------------------------------------------------------------

def ms_to_timecode(ms: int) -> str:
    """Convert milliseconds to MM:SS.mmm string."""
    if ms is None:
        return None
    total_s, millis = divmod(ms, 1000)
    minutes, seconds = divmod(total_s, 60)
    return f"{int(minutes):02d}:{int(seconds):02d}.{int(millis):03d}"


# ---------------------------------------------------------------------------
# Voice map builder
# ---------------------------------------------------------------------------

def build_voice_map(project: dict) -> dict:
    """
    Merge voices (project settings) with base_voices (full metadata).
    Returns { voice_id: { character, alias, settings, meta } }
    """
    # Index base_voices by voice_id
    base = {v["voice_id"]: v for v in project.get("base_voices", [])}

    voice_map = {}
    for pv in project.get("voices", []):
        vid = pv["voice_id"]
        bv  = base.get(vid, {})
        voice_map[vid] = {
            "voice_id":   vid,
            "name":       bv.get("name", ""),
            "alias":      pv.get("alias", "") or bv.get("name", ""),
            "category":   bv.get("category", ""),
            "labels":     bv.get("labels", {}),
            "description": bv.get("description", ""),
            "settings": {
                "stability":       pv.get("stability"),
                "similarity_boost": pv.get("similarity_boost"),
                "style":           pv.get("style"),
                "speed":           pv.get("speed"),
                "use_speaker_boost": pv.get("use_speaker_boost"),
                "volume_gain":     pv.get("volume_gain"),
            }
        }
    return voice_map


# ---------------------------------------------------------------------------
# Asset normalizer
# ---------------------------------------------------------------------------

def normalize_asset(asset: dict) -> dict:
    offset   = asset.get("offset_ms", 0) or 0
    duration = asset.get("duration_ms", 0) or 0
    end      = offset + duration

    filename = asset.get("filename", "")
    asset_type = classify_filename(filename)

    return {
        "external_audio_id": asset["external_audio_id"],
        "filename":          filename,
        "type":              asset_type,
        "track_id":          asset.get("track_id"),
        "order":             asset.get("order"),
        # Timing
        "offset_ms":         offset,
        "duration_ms":       duration,
        "end_ms":            end,
        # Human-readable timecodes
        "offset_tc":         ms_to_timecode(offset),
        "end_tc":            ms_to_timecode(end),
        "duration_tc":       ms_to_timecode(duration),
        # Mix
        "volume_gain_db":    asset.get("volume_gain_db", 0.0),
        "muted":             asset.get("muted", False),
        "fade_in_ms":        asset.get("fade_in_ms", 0),
        "fade_out_ms":       asset.get("fade_out_ms", 0),
        # Source clip window (within the source file)
        "source_start_ms":   asset.get("start_time_ms", 0),
        "source_end_ms":     asset.get("end_time_ms", duration),
    }


# ---------------------------------------------------------------------------
# Chapter normalizer
# ---------------------------------------------------------------------------

def normalize_chapter(ch: dict) -> dict:
    return {
        "chapter_id":   ch["chapter_id"],
        "name":         ch.get("name", ""),
        "state":        ch.get("state"),
        "can_download": ch.get("can_be_downloaded", False),
        "has_video":    ch.get("has_video", False),
        "voice_ids":    ch.get("voice_ids", []),
        "last_converted_unix": ch.get("last_conversion_date_unix"),
    }


# ---------------------------------------------------------------------------
# Project meta
# ---------------------------------------------------------------------------

def normalize_project_meta(project: dict) -> dict:
    return {
        "project_id":   project["project_id"],
        "name":         project["name"],
        "created_unix": project.get("create_date_unix"),
        "default_voice_id": project.get("default_paragraph_voice_id"),
        "model_id":     project.get("default_model_id"),
        "quality_preset": project.get("quality_preset"),
        "chapters_enabled": project.get("chapters_enabled", True),
        "source_type":  project.get("source_type"),
    }


# ---------------------------------------------------------------------------
# Main parse
# ---------------------------------------------------------------------------

def parse_project(project: dict) -> dict:
    return {
        "project":   normalize_project_meta(project),
        "voice_map": build_voice_map(project),
        "chapters":  [normalize_chapter(c) for c in project.get("chapters", [])],
        "assets":    sorted(
            [normalize_asset(a) for a in project.get("assets", [])],
            key=lambda a: a["offset_ms"]
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Parse ElevenLabs project JSON")
    parser.add_argument("input", help="Path to project JSON file")
    parser.add_argument("-o", "--output", help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        sys.exit(1)

    with src.open() as f:
        project = json.load(f)

    result = parse_project(project)

    out_json = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(out_json)
        print(f"Written to {args.output}")
    else:
        print(out_json)


if __name__ == "__main__":
    main()
