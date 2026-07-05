"""
build_character_map.py
----------------------
Generate character_map.json from one or more ElevenLabs project XHR JSONs.

The character map is the authoritative crosswalk between:
  - ElevenLabs voice_id  (what the API uses)
  - voice_name           (ElevenLabs label)
  - character_name       (The 413 character — editable, canonical)
  - character_type       (main | recurring | guest | narrator | host | meta)
  - seasons/episodes     (where they appear)

Usage:
    python build_character_map.py <project1.json> [project2.json ...] -o character_map.json

    # Update existing map with new project data (preserves manual edits):
    python build_character_map.py <project.json> --merge character_map.json -o character_map.json
"""

import json
import argparse
import sys
from pathlib import Path


def load_project(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def extract_episode_tag(project: dict, path: str) -> str:
    """Derive episode tag from project name or filename."""
    name = project.get("name", "")
    import re
    # Try to find SxxExx pattern in project name
    m = re.search(r'S(\d+)[-\s]?E(\d+)', name, re.IGNORECASE)
    if m:
        return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
    # Fall back to filename
    stem = Path(path).stem  # e.g. the413_xhr_s01e01
    m = re.search(r's(\d+)e(\d+)', stem, re.IGNORECASE)
    if m:
        return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}"
    return "unknown"


def collect_voices(project_paths: list) -> dict:
    """
    Collect all unique voices across project files.
    Returns { voice_id -> entry dict }
    """
    all_voices = {}

    for path in project_paths:
        project = load_project(path)
        ep = extract_episode_tag(project, path)
        season = ep[:3] if ep != "unknown" else "unknown"  # S01, S02 etc

        proj_settings = {v["voice_id"]: v for v in project.get("voices", [])}

        for bv in project.get("base_voices", []):
            vid = bv["voice_id"]
            if vid not in all_voices:
                all_voices[vid] = {
                    "voice_id":    vid,
                    "voice_name":  bv.get("name", ""),
                    "category":    bv.get("category", ""),
                    "labels":      bv.get("labels", {}),
                    "description": (bv.get("description") or "").strip(),
                    "episodes":    [],
                    "seasons":     [],
                    "voice_settings_by_episode": {}
                }

            entry = all_voices[vid]
            if ep not in entry["episodes"]:
                entry["episodes"].append(ep)
            if season not in entry["seasons"]:
                entry["seasons"].append(season)

            ps = proj_settings.get(vid, {})
            entry["voice_settings_by_episode"][ep] = {
                "stability":        ps.get("stability"),
                "similarity_boost": ps.get("similarity_boost"),
                "style":            ps.get("style"),
                "speed":            ps.get("speed"),
            }

    return all_voices


# ---------------------------------------------------------------------------
# Character inference heuristics
# Tina/Kathy can edit character_name + character_type manually after generation
# ---------------------------------------------------------------------------

# Known aliases: voice_name -> canonical character_name
# Handles cases where a character was re-voiced between seasons
VOICE_NAME_TO_CHARACTER = {
    "Adam 2.0":                    ("Adam",           "host"),
    "Dez 2.0":                     ("Dez",            "main"),
    "Rian":                        ("Rían",           "main"),
    "Maya 2.0":                    ("Maya",           "main"),
    "Ava":                         ("Ava",            "main"),
    "Tina 2.0":                    ("Tina",           "host"),
    "Stephen Red":                 ("Mr. Patterson",  "recurring"),
    "Russel - Old Man Hoarse Voice": ("Frank",        "recurring"),
    "Margaret Ellis":              ("Margaret Ellis", "recurring"),
    # Karen was re-voiced S01->S02 (Karen Ellis -> Karen Speaking Voice)
    "Karen Ellis":                 ("Karen Ellis",    "recurring"),
    "Karen Speaking Voice":        ("Karen Ellis",    "recurring"),
    # Sarah was re-voiced S01->S02 (radio voice -> speaking voice)
    "Sarah Ellis Radio":           ("Sarah Ellis",    "recurring"),
    "Sarah 2.0 Speaking Voice":    ("Sarah Ellis",    "recurring"),
    "Victor Morrison 'Stranger'":  ("Victor Morrison","recurring"),
    "Janet":                       ("Janet",          "guest"),
    "Martha 2.0":                  ("Martha",         "guest"),
    "Gerald 2.0":                  ("Gerald",         "guest"),
    "Elena 2.0":                   ("Elena",          "recurring"),
    "Collin":                      ("Collin",         "guest"),
    "Brandon":                     ("Brandon",        "guest"),
}


def build_character_map(voices: dict, existing: dict = None) -> dict:
    """
    Build the character_map structure.
    If existing is provided, preserve manual edits to character_name/type.
    """
    existing_by_vid = {}
    if existing:
        for entry in existing.get("characters", []):
            existing_by_vid[entry["voice_id"]] = entry

    characters = []
    for vid, v in sorted(voices.items(),
                         key=lambda x: (x[1]["episodes"][0], x[1]["voice_name"])):

        voice_name = v["voice_name"]
        inferred_char, inferred_type = VOICE_NAME_TO_CHARACTER.get(
            voice_name, (voice_name, "guest")
        )

        # Preserve manual edits if merging
        if vid in existing_by_vid:
            ex = existing_by_vid[vid]
            character_name = ex.get("character_name", inferred_char)
            character_type = ex.get("character_type", inferred_type)
            notes = ex.get("notes", "")
        else:
            character_name = inferred_char
            character_type = inferred_type
            notes = ""

        entry = {
            "voice_id":       vid,
            "voice_name":     voice_name,
            "character_name": character_name,
            "character_type": character_type,
            "seasons":        sorted(v["seasons"]),
            "episodes":       sorted(v["episodes"]),
            "category":       v["category"],
            "labels":         v["labels"],
            "description":    v["description"],
            "notes":          notes,
            "voice_settings_by_episode": v["voice_settings_by_episode"],
        }
        characters.append(entry)

    return {
        "_schema": "character_map_v1",
        "_note": "Edit character_name, character_type, and notes fields freely. "
                 "Re-run build_character_map.py with --merge to update without "
                 "losing your edits.",
        "character_types": {
            "host":      "Adam / Tina — the radio show hosts",
            "main":      "Core ensemble cast (Dez, Rian, Maya, Ava)",
            "recurring": "Characters appearing across multiple episodes",
            "guest":     "Characters appearing in one or a few episodes",
        },
        "characters": characters
    }


def main():
    parser = argparse.ArgumentParser(
        description="Build character_map.json from ElevenLabs project XHR files"
    )
    parser.add_argument("inputs", nargs="+",
                        help="One or more project XHR JSON files")
    parser.add_argument("-o", "--output",
                        help="Output path (default: stdout)")
    parser.add_argument("--merge",
                        help="Existing character_map.json to merge/preserve edits from",
                        default=None)
    args = parser.parse_args()

    voices = collect_voices(args.inputs)

    existing = None
    if args.merge:
        mp = Path(args.merge)
        if mp.exists():
            with mp.open() as f:
                existing = json.load(f)
            print(f"Merging with existing {args.merge}", file=sys.stderr)
        else:
            print(f"WARNING: --merge file {args.merge} not found, building fresh",
                  file=sys.stderr)

    result = build_character_map(voices, existing)

    out_json = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(out_json)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(out_json)


if __name__ == "__main__":
    main()
