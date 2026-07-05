"""
parse_xi_timeline.py
--------------------
Build a unified chronological timeline for a fully generated The 413 episode
from an ElevenLabs chapter content XHR JSON.

Requires a FULLY GENERATED chapter (can_be_downloaded: true) — dialog blocks
must have tts_element.duration_ms populated for timeline reconstruction.

Usage:
    python parse_xi_timeline.py <chapter_extended.json> [-o output.json]
                                [-p <project.json>]
                                [-c <character_map.json>]
                                [--episode S01E01]

External audio placement model:
    - Non-zero offset_ms  → absolute timeline position
    - Zero offset_ms      → looped/tiled within track; reconstructed by
                            accumulating durations per track_id in order

Output schema:
    {
      "episode":  "S01E01",
      "chapter":  { meta },
      "stats":    { duration, counts, warnings },
      "timeline": [ { event }, ... ]   # sorted by abs_start_ms
    }

Each event:
    abs_start_ms, abs_end_ms, duration_ms  — millisecond positions
    start_tc, end_tc                        — MM:SS.mmm timecodes
    event_type                              — dialog|sfx|music|ambiance|bonus|unknown
    source                                  — dialog|external_audio
    track_id, track_type                    — tts|external_audio
    placement                               — absolute|tiled  (audio only)
    character, voice_id, text               — dialog fields
    filename, external_audio_id             — audio fields
    index, block_id, order
    muted, volume_gain_db, fade_in_ms, fade_out_ms
"""

import json
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ms_to_tc(ms):
    if ms is None:
        return None
    total_s, millis = divmod(int(ms), 1000)
    minutes, seconds = divmod(total_s, 60)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def classify_audio(filename):
    name = (filename or "").lower().strip()
    rules = [
        (r"ambien(ce|t)|radio station|broadcast|static|hum|buzz|walla|murmur|background", "ambiance"),
        (r"wind|rain|storm|winter|snow|weather",             "ambiance"),
        (r"diner|inn|hallway|room tone|soft background",     "ambiance"),
        (r"music|frequency|contemplation|new england winter|late.night", "music"),
        (r"sign[-_]?on|sign[-_]?off|bumper|intro|legacy",   "music"),
        (r"behind.the.scene|interview|beyond.the.scene",     "bonus"),
        (r"footstep|walk|step|floor|creaking",               "sfx"),
        (r"door|knock|slam|hinge|creak|bell|chime",          "sfx"),
        (r"lamp|light|switch|click",                         "sfx"),
        (r"paper|rustle|cloth|coat|looking through",         "sfx"),
        (r"key|lock|brass|wooden door",                      "sfx"),
        (r"throat|cough|sneeze|breath",                      "sfx"),
        (r"notif|ding|ping|alert|vibrat|phone|cell|mobile",  "sfx"),
        (r"tension|booster|sting",                           "sfx"),
        (r"\.mp3$|\.wav$|\.flac$",                           "sfx"),
    ]
    for pattern, label in rules:
        if re.search(pattern, name):
            return label
    return "sfx"


# ---------------------------------------------------------------------------
# Voice / character resolution
# ---------------------------------------------------------------------------

def load_voice_map(project_path, character_map_path):
    voice_map = {}
    if project_path:
        p = Path(project_path)
        if p.exists():
            proj = json.load(p.open())
            if "voice_map" in proj:
                for vid, v in proj["voice_map"].items():
                    voice_map[vid] = v.get("name", "")
            else:
                for bv in proj.get("base_voices", []):
                    voice_map[bv["voice_id"]] = bv.get("name", "")
    if character_map_path:
        cp = Path(character_map_path)
        if cp.exists():
            cmap = json.load(cp.open())
            for entry in cmap.get("characters", []):
                char = entry.get("character_name") or entry.get("voice_name", "")
                voice_map[entry["voice_id"]] = char
    return voice_map


# ---------------------------------------------------------------------------
# Dialog timeline reconstruction
# offset_ms on each block = gap between end of previous block and start of this one
# ---------------------------------------------------------------------------

def reconstruct_dialog_events(blocks, voice_map):
    events = []
    cursor = 0
    for idx, block in enumerate(blocks):
        children = block.get("children", [])
        if not children:
            continue
        child = children[0]
        offset   = child.get("offset_ms") or 0
        tts      = child.get("tts_element") or {}
        duration = tts.get("duration_ms")
        if duration is None:
            continue   # not yet generated — cannot place on timeline
        cursor    += offset
        abs_start  = cursor
        abs_end    = cursor + duration
        cursor     = abs_end
        voice_id   = (child.get("settings") or {}).get("project_voice_ref_id", "") or ""
        character  = voice_map.get(voice_id, "") if voice_map else ""
        events.append({
            "abs_start_ms":      abs_start,
            "abs_end_ms":        abs_end,
            "duration_ms":       duration,
            "start_tc":          ms_to_tc(abs_start),
            "end_tc":            ms_to_tc(abs_end),
            "event_type":        "dialog",
            "source":            "dialog",
            "placement":         "absolute",
            "track_id":          block.get("track_id", "tts0"),
            "track_type":        "tts",
            "character":         character,
            "voice_id":          voice_id,
            "text":              (child.get("text") or "").strip(),
            "filename":          None,
            "external_audio_id": None,
            "index":             idx,
            "block_id":          block.get("block_id"),
            "order":             block.get("order"),
            "muted":             child.get("muted", False),
            "volume_gain_db":    child.get("volume_gain_db", 0.0),
            "fade_in_ms":        child.get("fade_in_ms", 0),
            "fade_out_ms":       child.get("fade_out_ms", 0),
        })
    return events


# ---------------------------------------------------------------------------
# External audio events — two-pass placement model
# ---------------------------------------------------------------------------

def build_audio_events(external_audios, tracks):
    track_types = {t["track_id"]: t.get("type", "external_audio") for t in tracks}

    # Split into absolute (offset_ms > 0) and tiled (offset_ms == 0)
    absolute = [ea for ea in external_audios if (ea.get("offset_ms") or 0) > 0]
    tiled    = [ea for ea in external_audios if (ea.get("offset_ms") or 0) == 0]

    events = []

    # Pass 1: absolute placements
    for idx, ea in enumerate(absolute):
        offset   = ea.get("offset_ms", 0)
        duration = ea.get("duration_ms", 0) or 0
        filename = (ea.get("filename") or "").strip()
        track_id = ea.get("track_id", "")
        events.append(_make_audio_event(ea, idx, offset, duration, filename,
                                        track_id, track_types, "absolute"))

    # Pass 2: tiled — accumulate per track in document order
    track_cursor = defaultdict(int)
    for idx, ea in enumerate(tiled):
        track_id = ea.get("track_id", "")
        offset   = track_cursor[track_id]
        duration = ea.get("duration_ms", 0) or 0
        track_cursor[track_id] += duration
        filename = (ea.get("filename") or "").strip()
        events.append(_make_audio_event(ea, len(absolute) + idx, offset, duration,
                                        filename, track_id, track_types, "tiled"))

    return events


def _make_audio_event(ea, idx, abs_start, duration, filename, track_id, track_types, placement):
    return {
        "abs_start_ms":      abs_start,
        "abs_end_ms":        abs_start + duration,
        "duration_ms":       duration,
        "start_tc":          ms_to_tc(abs_start),
        "end_tc":            ms_to_tc(abs_start + duration),
        "event_type":        classify_audio(filename),
        "source":            "external_audio",
        "placement":         placement,
        "track_id":          track_id,
        "track_type":        track_types.get(track_id, "external_audio"),
        "character":         None,
        "voice_id":          None,
        "text":              None,
        "filename":          filename,
        "external_audio_id": ea.get("external_audio_id"),
        "index":             idx,
        "block_id":          None,
        "order":             ea.get("order"),
        "muted":             ea.get("muted", False),
        "volume_gain_db":    ea.get("volume_gain_db", 0.0),
        "fade_in_ms":        ea.get("fade_in_ms", 0),
        "fade_out_ms":       ea.get("fade_out_ms", 0),
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def build_stats(events, blocks):
    dialog = [e for e in events if e["source"] == "dialog"]
    audio  = [e for e in events if e["source"] == "external_audio"]
    max_end = max((e["abs_end_ms"] for e in events), default=0)
    type_counts = {}
    for e in events:
        t = e["event_type"]
        type_counts[t] = type_counts.get(t, 0) + 1
    placement_counts = {}
    for e in audio:
        p = e["placement"]
        placement_counts[p] = placement_counts.get(p, 0) + 1
    lines_by_char = {}
    for e in dialog:
        char = e["character"] or (e["voice_id"][:8] if e["voice_id"] else "?")
        lines_by_char[char] = lines_by_char.get(char, 0) + 1
    unplaced = len(blocks) - len(dialog)
    warnings = []
    if unplaced:
        warnings.append(f"{unplaced} dialog blocks skipped (no duration_ms — not yet generated)")
    return {
        "total_events":            len(events),
        "dialog_events":           len(dialog),
        "audio_events":            len(audio),
        "audio_placement_counts":  placement_counts,
        "total_blocks_in_chapter": len(blocks),
        "dialog_blocks_placed":    len(dialog),
        "episode_duration_ms":     max_end,
        "episode_duration_tc":     ms_to_tc(max_end),
        "event_type_counts":       type_counts,
        "lines_by_character":      dict(sorted(lines_by_char.items(), key=lambda x: -x[1])),
        "warnings":                warnings,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_timeline(chapter, voice_map, episode=None):
    content   = chapter.get("content", {})
    blocks    = content.get("blocks", [])
    ext_audio = content.get("external_audios", [])
    tracks    = content.get("tracks", [])

    dialog_events = reconstruct_dialog_events(blocks, voice_map)
    audio_events  = build_audio_events(ext_audio, tracks)

    all_events = sorted(
        dialog_events + audio_events,
        key=lambda e: (e["abs_start_ms"], 0 if e["source"] == "dialog" else 1)
    )

    result = {
        "chapter": {
            "chapter_id":                chapter.get("chapter_id"),
            "name":                      chapter.get("name"),
            "can_be_downloaded":         chapter.get("can_be_downloaded"),
            "last_conversion_date_unix": chapter.get("last_conversion_date_unix"),
        },
        "stats":    build_stats(all_events, blocks),
        "timeline": all_events,
    }
    if episode:
        result["episode"] = episode
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build unified timeline from ElevenLabs chapter content XHR"
    )
    parser.add_argument("input",  help="Chapter content XHR JSON (fully generated)")
    parser.add_argument("-p", "--project",
                        help="Project XHR JSON for voice name resolution", default=None)
    parser.add_argument("-c", "--character-map",
                        help="character_map.json for canonical character names", default=None)
    parser.add_argument("--episode", help="Episode tag e.g. S01E01", default=None)
    parser.add_argument("-o", "--output", help="Output JSON (default: stdout)")
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        sys.exit(1)

    with src.open() as f:
        chapter = json.load(f)

    if not chapter.get("can_be_downloaded"):
        print("WARNING: can_be_downloaded=false — dialog timing will be incomplete",
              file=sys.stderr)

    voice_map = load_voice_map(args.project, args.character_map)
    result    = parse_timeline(chapter, voice_map, episode=args.episode)

    out_json = json.dumps(result, indent=2)
    if args.output:
        Path(args.output).write_text(out_json)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(out_json)


if __name__ == "__main__":
    main()
