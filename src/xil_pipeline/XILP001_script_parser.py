"""Parse markdown production scripts into structured JSON.

Converts podcast scripts from markdown format into
sequence-numbered entries suitable for voice generation.

Module Attributes:
    KNOWN_SPEAKERS: Ordered list of speaker names (longest-first for matching).
    SPEAKER_KEYS: Mapping from display names to normalized keys.
    SECTION_MAP: Mapping from section header text to URL-safe slugs.
    DIRECTION_TYPES: Recognized direction subtypes for stage directions.
"""

import argparse
import csv
import json
import os
import re
import sys

from xil_pipeline.models import (
    ParsedScript,
    ScriptStats,
    derive_paths,
    episode_tag,
    resolve_slug,
    show_slug,
)
from xil_pipeline.sfx_common import run_banner

# Known speakers — ordered longest-first so compound names match before short ones
KNOWN_SPEAKERS = [
    "FILM AUDIO (MARGARET'S VOICE)",
    "STRANGER (MALE VOICE, FLAT)",
    "MARGARET (V.O.)",
    "MR. PATTERSON",
    "FILM AUDIO",
    "STRANGER",
    "MARGARET",
    "MARTHA",
    "GERALD",
    "KAREN",
    "SARAH",
    "ELENA",
    "CLERK",
    "ADAM",
    "DEZ",
    "MAYA",
    "AVA",
    "RÍAN",   # S01 spelling
    "RÍÁN",   # S02 spelling
    "FRANK",
    "TINA",
]

# Map display names to normalized keys for cast_config lookup
SPEAKER_KEYS = {
    "FILM AUDIO (MARGARET'S VOICE)": "film_audio",
    "STRANGER (MALE VOICE, FLAT)": "stranger",
    "MR. PATTERSON": "mr_patterson",
    "FILM AUDIO": "film_audio",
    "STRANGER": "stranger",
    "MARGARET (V.O.)": "margaret",
    "MARGARET": "margaret",
    "MARTHA": "martha",
    "GERALD": "gerald",
    "CLERK": "clerk",
    "KAREN": "karen",
    "SARAH": "sarah",
    "ELENA": "elena",
    "ADAM": "adam",
    "DEZ": "dez",
    "MAYA": "maya",
    "AVA": "ava",
    "RÍAN": "rian",
    "RÍÁN": "rian",
    "FRANK": "frank",
    "TINA": "tina",
}

# Section detection
SECTION_MAP = {
    "COLD OPEN": "cold-open",
    "OPENING CREDITS": "opening-credits",
    "ACT ONE": "act1",
    "ACT 1": "act1",                            # numeral variant
    "ACT TWO": "act2",
    "ACT 2": "act2",                            # numeral variant
    "ACT THREE": "act3",
    "ACT 3": "act3",                            # S02E02 three-act structure
    "ACT FOUR": "act4",
    "ACT 4": "act4",
    "MID-EPISODE BREAK": "mid-break",
    "CLOSING": "closing",
    "CLOSING — RADIO STATION": "closing",       # S02E01 variant
    "CLOSING — ADAM'S SIGN-OFF": "closing",     # S02E02 variant straight apostrophe
    "CLOSING \u2014 ADAM\u2019S SIGN-OFF": "closing",  # S02E02 variant curly apostrophe
    "POST-INTERVIEW": "post-interview",
    "POST-INTERVIEW: ADAM & TINA": "post-interview",  # S02E02 variant
    "POST-CREDITS SCENE": "post-credits",       # S01E03
    "DEZ'S CLOSING NARRATION": "dez-closing",       # S02E03 straight apostrophe
    "DEZ\u2019S CLOSING NARRATION": "dez-closing",  # S02E03 curly apostrophe
    "PRODUCTION NOTES": "production-notes",     # S02E03 preamble
}

# Direction subtypes
DIRECTION_TYPES = ["SFX", "MUSIC", "AMBIENCE", "BEAT"]


def strip_markdown_escapes(text: str) -> str:
    """Remove markdown backslash escapes from the script.

    Args:
        text: Raw text possibly containing backslash-escaped markdown characters.

    Returns:
        Text with all backslash escapes removed.
    """
    text = text.replace("\\[", "[")
    text = text.replace("\\]", "]")
    text = text.replace("\\===", "===")
    text = text.replace("\\=", "=")
    # Remove all remaining backslash escapes (e.g., \. \~ \* \& \!)
    text = re.sub(r"\\(.)", r"\1", text)
    return text


def strip_markdown_formatting(text: str) -> str:
    """Remove markdown formatting syntax (bold, headings, trailing breaks).

    Intended to run AFTER ``strip_markdown_escapes()`` so that backslash
    escapes are already resolved.  Operates per-line to correctly strip
    ``#`` heading prefixes while leaving other content intact.

    Args:
        text: Text with markdown formatting (``**``, ``##``, etc.).

    Returns:
        Text with formatting removed.  Plain-text input passes through
        unchanged.
    """
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        # Remove heading prefixes (# through ######)
        line = re.sub(r"^#{1,6}\s*", "", line)
        # Remove bold markers
        line = line.replace("**", "")
        # Strip trailing double-space (markdown line break) and whitespace
        line = line.rstrip()
        cleaned.append(line)
    return "\n".join(cleaned)


def classify_direction(text: str) -> str | None:
    """Classify a stage direction into a sound category.

    Args:
        text: Bracket-interior text (e.g., ``"SFX: DOOR OPENS"``).

    Returns:
        One of ``"SFX"``, ``"MUSIC"``, ``"AMBIENCE"``, ``"BEAT"``, or ``None``
        if the direction doesn't match a known category.
    """
    for dt in DIRECTION_TYPES:
        if text.strip().startswith(dt):
            return dt
    if text.strip() == "BEAT" or text.strip() == "LONG BEAT":
        return "BEAT"
    return None


def try_match_speaker(line: str) -> tuple[str, str | None, str] | None:
    """Match a known speaker name at the start of a line.

    Args:
        line: A stripped line from the script.

    Returns:
        A tuple of ``(speaker_key, direction, spoken_text)`` if a known
        speaker is found, or ``None`` if no speaker matches.
    """
    for speaker in KNOWN_SPEAKERS:
        if not line.startswith(speaker):
            continue
        rest = line[len(speaker):]
        # Must be followed by space, '(' or end of string
        if rest and rest[0] not in (" ", "("):
            continue

        rest = rest.lstrip()
        direction = None
        # Check for parenthetical direction
        if rest.startswith("("):
            paren_end = rest.find(")")
            if paren_end != -1:
                direction = rest[1:paren_end].strip()
                rest = rest[paren_end + 1:].strip()

        spoken_text = rest
        return SPEAKER_KEYS[speaker], direction, spoken_text

    return None


def is_stage_direction(line: str) -> bool:
    """Check if a line is a stage direction like ``[SFX: ...]`` or ``[BEAT]``.

    Args:
        line: A stripped line from the script.

    Returns:
        ``True`` if the line starts with ``[`` and contains ``]``.
    """
    return line.startswith("[") and "]" in line


def is_section_header(line: str) -> bool:
    """Check if a line matches a known section header.

    Args:
        line: A stripped line from the script.

    Returns:
        ``True`` if the line matches a key in ``SECTION_MAP``.
    """
    return line.strip() in SECTION_MAP


def is_scene_header(line: str) -> bool:
    """Check if a line is a scene header (``SCENE N: ...``).

    Args:
        line: A stripped line from the script.

    Returns:
        ``True`` if the line matches the ``SCENE \\d+:`` pattern.
    """
    return bool(re.match(r"^SCENE \d+:", line))


def is_divider(line: str) -> bool:
    """Check if a line is a section divider (``===`` or ``---``).

    Args:
        line: A stripped line from the script.

    Returns:
        ``True`` if the stripped line equals ``"==="`` or ``"---"``.
    """
    return bool(re.match(r"^={3,}$|^-{3,}$", line.strip()))


def is_metadata_section(line: str) -> bool:
    """Check if a line begins a post-script metadata section.

    Args:
        line: A stripped line from the script.

    Returns:
        ``True`` if the line matches a known metadata header
        (e.g., ``"PRODUCTION NOTES:"``).
    """
    return line.strip() in (
        "PRODUCTION NOTES:",
        "SOCIAL MEDIA PROMPT:",
        "KEY CHANGES FROM ORIGINAL:",
        "ACCESSIBILITY NOTES:",
        "VOICES NEEDED THIS EPISODE:",
        "KEY SOUND EFFECTS:",
        "MUSIC CUES:",
    )


def parse_scene_header(line: str) -> tuple[int | None, str | None]:
    """Extract scene number and name from a scene header line.

    Args:
        line: A line matching the ``SCENE N: ...`` pattern.

    Returns:
        A tuple of ``(scene_number, scene_name)``, or ``(None, None)``
        if the line doesn't match.
    """
    m = re.match(r"^SCENE (\d+):\s*(.+)", line)
    if m:
        return int(m.group(1)), m.group(2).strip()
    return None, None


_DEBUG_TRUNCATE = 200


def write_debug_csv(
    output_path: str,
    debug_line_map: list[tuple[int, str, int]],
    entries: list[dict],
) -> None:
    """Write a diagnostic CSV mapping markdown source lines to parsed entries.

    Each row represents one parsed entry, showing the originating markdown
    line alongside all fields from the parsed JSON output. Text fields are
    truncated at 200 characters to prevent unpredictable CSV cell sizes.

    Args:
        output_path: Filesystem path for the output CSV file.
        debug_line_map: List of ``(1-based line number, raw line text, entry index)``
            tuples collected during parsing.
        entries: The fully-parsed entries list (after all continuation merges).
    """
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "md_line_num", "md_raw", "seq", "type", "section", "scene",
            "speaker", "direction", "direction_type", "text",
        ])
        for line_num, raw_line, entry_idx in debug_line_map:
            entry = entries[entry_idx]
            writer.writerow([
                line_num,
                raw_line[:_DEBUG_TRUNCATE],
                entry["seq"],
                entry["type"],
                entry.get("section") or "",
                entry.get("scene") or "",
                entry.get("speaker") or "",
                entry.get("direction") or "",
                entry.get("direction_type") or "",
                (entry.get("text") or "")[:_DEBUG_TRUNCATE],
            ])


def parse_script_header(line: str) -> tuple[str, int | None, int, str]:
    """Extract show, season, episode, and title from the script header line.

    Parses the first line of a production script, which follows the format::

        SHOW [Season N:] Episode N: ["Arc Title" Arc:] "Episode Title" ...

    Season is optional — scripts without a season declaration return ``None``.
    Title is the first double-quoted string after ``Episode N:``, which is the
    episode title (not the arc title that may follow).  Falls back to bare text
    after ``Episode N:`` when no quoted strings are present.

    Args:
        line: The first non-empty line of the production script, after
            markdown escapes have been removed.

    Returns:
        A tuple of ``(show, season, episode, title)`` where ``season``
        is ``None`` if not declared in the header.
    """
    # Show: text before the first Season or Episode keyword
    show_match = re.match(r"^(.+?)\s+(?:Season\s+\d+|Episode\s+\d+)", line)
    show = show_match.group(1).strip() if show_match else "THE 413"

    # Season: optional
    season_match = re.search(r"Season\s+(\d+)", line)
    season = int(season_match.group(1)) if season_match else None

    # Episode
    ep_match = re.search(r"Episode\s+(\d+)", line)
    episode = int(ep_match.group(1)) if ep_match else 1

    # Title: first double-quoted string after "Episode N:", or bare text
    ep_rest = re.search(r"Episode\s+\d+[:\s]+(.*)", line)
    if ep_rest:
        rest_text = ep_rest.group(1)
        quoted_after_ep = re.search(r'"([^"]+)"', rest_text)
        if quoted_after_ep:
            title = quoted_after_ep.group(1)
        else:
            title = rest_text.strip()
    else:
        title = ""

    return show, season, episode, title


def parse_script(filepath: str, debug_output: str | None = None) -> dict:
    """Parse a markdown production script into structured entries.

    Reads a markdown file following THE 413 script format, extracts
    dialogue lines, stage directions, section headers, and scene headers
    into a sequence-numbered list of entries.

    Args:
        filepath: Path to the markdown production script file.
        debug_output: If provided, write a diagnostic CSV to this path
            mapping each markdown source line to its parsed entry.
            Text fields are truncated at 200 characters. Defaults to
            ``None`` (no CSV written).

    Returns:
        Dictionary with keys ``show``, ``season``, ``episode``, ``title``,
        ``source_file``, ``entries`` (list of entry dicts), and
        ``stats`` (aggregate statistics dict). Validates against
        the ``ParsedScript`` model.

    Raises:
        FileNotFoundError: If the script file does not exist.
    """
    with open(filepath, encoding="utf-8") as f:
        raw = f.read()

    raw = strip_markdown_escapes(raw)
    raw = strip_markdown_formatting(raw)
    lines = raw.split("\n")
    # debug_line_map: (1-based line number, raw line text, entry index)
    debug_line_map: list[tuple[int, str, int]] = []

    entries = []
    seq = 0
    current_section = None
    current_scene = None
    in_metadata = False
    last_dialogue_idx = None  # Index into entries for continuation handling
    pending_speaker = None  # (speaker_key, direction_or_None) for multi-line dialogue

    # Parse metadata from the header line, then skip it
    start = 0
    if lines and lines[0].startswith("THE 413"):
        show, season, episode, title = parse_script_header(lines[0])
        start = 1
    else:
        show, season, episode, title = "THE 413", None, 1, ""

    # Skip CAST section
    in_cast = False
    for i in range(start, len(lines)):
        line = lines[i].strip()
        if line == "CAST:":
            in_cast = True
            continue
        if in_cast:
            if line == "===" or (line and not line.startswith("*")):
                in_cast = False
                start = i
                break
            continue

    for i in range(start, len(lines)):
        line = lines[i].strip()

        if not line:
            continue

        # Handle multi-line dialogue: pending speaker awaiting direction/text
        if pending_speaker is not None:
            p_speaker_key, p_direction = pending_speaker
            # Standalone parenthetical → direction line
            if line.startswith("(") and line.endswith(")"):
                p_direction = line[1:-1].strip()
                pending_speaker = (p_speaker_key, p_direction)
                continue
            # Otherwise this line is the spoken text
            seq += 1
            entries.append({
                "seq": seq,
                "type": "dialogue",
                "section": current_section,
                "scene": current_scene,
                "speaker": p_speaker_key,
                "direction": p_direction,
                "text": line,
                "direction_type": None,
            })
            debug_line_map.append((i + 1, lines[i], len(entries) - 1))
            last_dialogue_idx = len(entries) - 1
            pending_speaker = None
            continue

        if is_divider(line):
            continue

        if is_metadata_section(line):
            in_metadata = True
            continue

        if in_metadata:
            # Check if we've left metadata (shouldn't happen, metadata is at the end)
            continue

        # Also stop at END OF EPISODE / END OF PRODUCTION SCRIPT
        if line.startswith("END OF EPISODE") or line.startswith("END OF PRODUCTION"):
            break

        # Section headers
        if is_section_header(line):
            current_section = SECTION_MAP[line.strip()]
            current_scene = None
            seq += 1
            entries.append({
                "seq": seq,
                "type": "section_header",
                "section": current_section,
                "scene": None,
                "speaker": None,
                "direction": None,
                "text": line.strip(),
                "direction_type": None,
            })
            debug_line_map.append((i + 1, lines[i], len(entries) - 1))
            last_dialogue_idx = None
            continue

        # Scene headers
        if is_scene_header(line):
            scene_num, scene_name = parse_scene_header(line)
            if scene_num is not None:
                current_scene = f"scene-{scene_num}"

            # Strip any bracketed directions from the scene header text
            clean_text = re.sub(r"\s*\[[^\]]+\]", "", line.strip()).strip()

            seq += 1
            entries.append({
                "seq": seq,
                "type": "scene_header",
                "section": current_section,
                "scene": current_scene,
                "speaker": None,
                "direction": None,
                "text": clean_text,
                "direction_type": None,
            })
            debug_line_map.append((i + 1, lines[i], len(entries) - 1))

            # Extract embedded bracketed directions (e.g. [AMBIENCE: ...])
            brackets = re.findall(r"\[([^\]]+)\]", line)
            for bracket_text in brackets:
                seq += 1
                entries.append({
                    "seq": seq,
                    "type": "direction",
                    "section": current_section,
                    "scene": current_scene,
                    "speaker": None,
                    "direction": None,
                    "text": bracket_text.strip(),
                    "direction_type": classify_direction(bracket_text),
                })
                debug_line_map.append((i + 1, lines[i], len(entries) - 1))

            last_dialogue_idx = None
            continue

        # Stage directions: [SFX: ...], [MUSIC: ...], [BEAT], etc.
        # Handle lines with multiple directions like [MUSIC: ...] [SFX: ...]
        if is_stage_direction(line):
            # Extract all bracketed sections
            brackets = re.findall(r"\[([^\]]+)\]", line)
            for bracket_text in brackets:
                seq += 1
                entries.append({
                    "seq": seq,
                    "type": "direction",
                    "section": current_section,
                    "scene": current_scene,
                    "speaker": None,
                    "direction": None,
                    "text": bracket_text.strip(),
                    "direction_type": classify_direction(bracket_text),
                })
                debug_line_map.append((i + 1, lines[i], len(entries) - 1))
            last_dialogue_idx = None
            continue

        # Dialogue lines: SPEAKER (direction) text
        match = try_match_speaker(line)
        if match:
            speaker_key, direction, spoken_text = match
            # Skip lines that are just stage directions disguised as speaker turns
            # (e.g., "[EVERYONE TURNS]" on its own line that starts with no speaker)
            if spoken_text:
                seq += 1
                entries.append({
                    "seq": seq,
                    "type": "dialogue",
                    "section": current_section,
                    "scene": current_scene,
                    "speaker": speaker_key,
                    "direction": direction,
                    "text": spoken_text,
                    "direction_type": None,
                })
                debug_line_map.append((i + 1, lines[i], len(entries) - 1))
                last_dialogue_idx = len(entries) - 1
            else:
                # Multi-line format: speaker name only, direction/text on next lines
                pending_speaker = (speaker_key, direction)
                last_dialogue_idx = None
            continue

        # Continuation text (no speaker prefix, no brackets)
        # Append to previous dialogue entry
        if last_dialogue_idx is not None and entries[last_dialogue_idx]["type"] == "dialogue":
            # Filter standalone parentheticals — acting notes, not spoken text
            if line.startswith("(") and line.endswith(")"):
                continue
            entries[last_dialogue_idx]["text"] += " " + line
            continue

        # Lines we can't classify — skip silently
        # (e.g., "[EVERYONE TURNS]" without brackets after stripping, stray markdown)

    # Compute stats
    dialogue_entries = [e for e in entries if e["type"] == "dialogue"]
    total_tts_chars = sum(len(e["text"]) for e in dialogue_entries)
    speakers_used = set(e["speaker"] for e in dialogue_entries)

    stats = ScriptStats(
        total_entries=len(entries),
        dialogue_lines=len(dialogue_entries),
        direction_lines=sum(1 for e in entries if e["type"] == "direction"),
        characters_for_tts=total_tts_chars,
        speakers=sorted(speakers_used),
        sections=sorted(set(e["section"] for e in entries if e["section"])),
    )

    parsed = ParsedScript(
        show=show,
        season=season,
        episode=episode,
        title=title,
        source_file=os.path.basename(filepath),
        entries=entries,
        stats=stats,
    )

    if debug_output:
        write_debug_csv(debug_output, debug_line_map, entries)

    return parsed.model_dump()


def compute_speaker_stats(parsed: dict) -> list[dict]:
    """Compute per-speaker dialogue distribution.

    Args:
        parsed: Output dictionary from ``parse_script()``.

    Returns:
        List of dicts sorted by lines descending, each with keys:
        ``speaker``, ``lines``, ``words``, ``chars``, ``pct_lines``,
        ``pct_words``, ``pct_chars``.
    """
    dialogue_entries = [e for e in parsed["entries"] if e["type"] == "dialogue"]
    accum: dict[str, dict] = {}
    for e in dialogue_entries:
        sp = e["speaker"]
        if sp not in accum:
            accum[sp] = {"lines": 0, "words": 0, "chars": 0}
        accum[sp]["lines"] += 1
        accum[sp]["words"] += len(e["text"].split())
        accum[sp]["chars"] += len(e["text"])

    total_lines = sum(s["lines"] for s in accum.values()) or 1
    total_words = sum(s["words"] for s in accum.values()) or 1
    total_chars = sum(s["chars"] for s in accum.values()) or 1

    result = []
    for sp, s in accum.items():
        result.append({
            "speaker": sp,
            "lines": s["lines"],
            "words": s["words"],
            "chars": s["chars"],
            "pct_lines": round(s["lines"] / total_lines * 100, 1),
            "pct_words": round(s["words"] / total_words * 100, 1),
            "pct_chars": round(s["chars"] / total_chars * 100, 1),
        })
    result.sort(key=lambda x: x["lines"], reverse=True)
    return result


def print_speaker_stats(parsed: dict) -> None:
    """Print per-speaker dialogue distribution table.

    Shows lines, words, characters, and percentage share for each speaker,
    sorted by number of lines descending.

    Args:
        parsed: Output dictionary from ``parse_script()``.
    """
    rows = compute_speaker_stats(parsed)
    if not rows:
        print("  No dialogue entries found.")
        return

    total_lines = sum(r["lines"] for r in rows)
    total_words = sum(r["words"] for r in rows)
    total_chars = sum(r["chars"] for r in rows)

    print(f"\n{'Speaker':<15} {'Lines':>6} {'%':>6} {'Words':>7} {'%':>6} {'Chars':>8} {'%':>6}")
    print(f"{'-'*15} {'-'*6} {'-'*6} {'-'*7} {'-'*6} {'-'*8} {'-'*6}")
    for r in rows:
        print(f"{r['speaker']:<15} {r['lines']:>6} {r['pct_lines']:>5.1f}%"
              f" {r['words']:>7,} {r['pct_words']:>5.1f}%"
              f" {r['chars']:>8,} {r['pct_chars']:>5.1f}%")
    print(f"{'-'*15} {'-'*6} {'-'*6} {'-'*7} {'-'*6} {'-'*8} {'-'*6}")
    print(f"{'TOTAL':<15} {total_lines:>6}        {total_words:>7,}        {total_chars:>8,}")
    print()


def print_summary(parsed: dict) -> None:
    """Print a human-readable summary of the parsed script.

    Displays show metadata, entry counts, TTS character budget,
    and a per-speaker breakdown of lines, words, and characters.

    Args:
        parsed: Output dictionary from ``parse_script()``.
    """
    stats = parsed["stats"]
    tag = episode_tag(parsed.get("season"), parsed["episode"])
    print(f"\n{'='*60}")
    print(f"PARSED: {parsed['show']} {tag} — {parsed['title']}")
    print(f"Source: {parsed['source_file']}")
    print(f"{'='*60}")
    print(f"  Total entries:      {stats['total_entries']}")
    print(f"  Dialogue lines:     {stats['dialogue_lines']}")
    print(f"  Stage directions:   {stats['direction_lines']}")
    print(f"  TTS characters:     {stats['characters_for_tts']:,}")
    print(f"  Speakers:           {', '.join(stats['speakers'])}")
    print(f"  Sections:           {', '.join(stats['sections'])}")
    print(f"{'='*60}")

    print_speaker_stats(parsed)


def print_dialogue_preview(parsed: dict, limit: int | None = None) -> None:
    """Print dialogue lines for review.

    Args:
        parsed: Output dictionary from ``parse_script()``.
        limit: Maximum number of dialogue lines to display.
            ``None`` shows all lines.
    """
    dialogue_entries = [e for e in parsed["entries"] if e["type"] == "dialogue"]
    if limit:
        dialogue_entries = dialogue_entries[:limit]

    print(f"\n--- Dialogue Preview ({len(dialogue_entries)} lines) ---\n")
    for e in dialogue_entries:
        scene_label = e["scene"] or e["section"] or "?"
        direction_label = f" ({e['direction']})" if e["direction"] else ""
        text_preview = e["text"][:80] + "..." if len(e["text"]) > 80 else e["text"]
        print(f"  {e['seq']:03d} | {scene_label:<16} | {e['speaker']:<14}{direction_label}")
        print(f"       {text_preview}")
        print()


def generate_cast_config(parsed: dict, cast_path: str) -> None:
    """Generate a skeleton cast config JSON from parsed script data.

    Creates a cast config with all speakers found in the parsed script,
    using ``voice_id="TBD"`` and sensible defaults for pan, filter, and role.
    The user must fill in voice IDs via ``XILU001_discover_voices_T2S.py``.

    Args:
        parsed: Parsed script dict from :func:`parse_script`.
        cast_path: Output path for the cast config JSON.
    """
    # Build reverse mapping: speaker_key -> display name
    key_to_display = {v: k for k, v in SPEAKER_KEYS.items()}

    speakers = parsed["stats"]["speakers"]
    cast = {}
    for speaker_key in speakers:
        display = key_to_display.get(speaker_key, speaker_key)
        full_name = display.replace("_", " ").title()
        cast[speaker_key] = {
            "full_name": full_name,
            "voice_id": "TBD",
            "pan": 0.0,
            "filter": False,
            "role": "TBD",
        }

    config = {
        "show": parsed.get("show", "THE 413"),
        "season": parsed.get("season"),
        "episode": parsed.get("episode", 1),
        "title": parsed.get("title", ""),
        "cast": cast,
    }

    with open(cast_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    print(f"Created {cast_path} with {len(speakers)} speakers "
          f"(voice_id=TBD — run XILU001 to assign)")


def generate_sfx_config(parsed: dict, sfx_path: str) -> None:
    """Generate a skeleton SFX config JSON from parsed script data.

    Creates an SFX config with entries for each unique direction found
    in the parsed script.  Defaults are based on direction type:

    - ``BEAT`` / ``LONG BEAT`` → silence (no API call)
    - ``SFX:`` → 5s effect
    - ``MUSIC:`` → 15s effect
    - ``AMBIENCE:`` → 30s looping effect
    - Other → 5s effect

    The user should review and refine prompts before running generation.

    Args:
        parsed: Parsed script dict from :func:`parse_script`.
        sfx_path: Output path for the SFX config JSON.
    """
    effects: dict[str, dict] = {}
    silence_count = 0
    sfx_count = 0

    for entry in parsed["entries"]:
        if entry["type"] != "direction":
            continue
        text = entry["text"]
        if text in effects:
            continue

        if text == "BEAT":
            effects[text] = {"type": "silence", "duration_seconds": 1.0}
            silence_count += 1
        elif text == "LONG BEAT":
            effects[text] = {"type": "silence", "duration_seconds": 2.0}
            silence_count += 1
        elif text.startswith("BEAT"):
            # e.g. "BEAT — 3 SECONDS", "BEAT — LONG, 5 SECONDS", "BEAT — EXTENDED, 8 SECONDS"
            m = re.search(r"(\d+)\s+SECOND", text)
            dur = float(m.group(1)) if m else 1.0
            effects[text] = {"type": "silence", "duration_seconds": dur}
            silence_count += 1
        elif text == "AMBIENCE: STOP" or text.endswith("FADES OUT"):
            effects[text] = {"type": "silence", "duration_seconds": 0.0}
            silence_count += 1
        elif text.startswith("AMBIENCE:"):
            effects[text] = {
                "prompt": text,
                "duration_seconds": 30.0,
                "loop": True,
            }
            sfx_count += 1
        elif text.startswith("MUSIC:"):
            effects[text] = {"prompt": text, "duration_seconds": 15.0}
            sfx_count += 1
        else:
            effects[text] = {"prompt": text, "duration_seconds": 5.0}
            sfx_count += 1

    config = {
        "show": parsed.get("show", "THE 413"),
        "season": parsed.get("season"),
        "episode": parsed.get("episode", 1),
        "defaults": {"prompt_influence": 0.3},
        "effects": effects,
    }

    with open(sfx_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    total = silence_count + sfx_count
    print(f"Created {sfx_path} with {total} effects "
          f"({silence_count} silence, {sfx_count} sfx "
          f"— review prompts before generation)")


def main() -> None:
    """CLI entry point for script parsing."""
    with run_banner():
        parser = argparse.ArgumentParser(description="Parse production script markdown into structured JSON")
        parser.add_argument("script", help="Path to the production script markdown file")
        parser.add_argument("--episode", default=None,
                            help="Episode tag (e.g. S01E01) — validates header and auto-generates absent cast/sfx configs")
        parser.add_argument("--show", default=None,
                            help="Show name override (default: from project.json)")
        parser.add_argument("--output", "-o", default=None,
                            help="Output JSON path (default: parsed/parsed_<slug>_<TAG>.json)")
        parser.add_argument("--preview", type=int, default=None,
                            help="Show first N dialogue lines (default: show all)")
        parser.add_argument("--quiet", action="store_true",
                            help="Only output JSON, skip summary/preview")
        parser.add_argument("--debug", action="store_true",
                            help="Write diagnostic CSV alongside JSON output")
        parser.add_argument("--stats", action="store_true",
                            help="Print per-speaker dialogue distribution (lines, words, chars, %%)")
        args = parser.parse_args()

        # Parse first so we can derive the output path from metadata
        parsed = parse_script(args.script)

        # Derive tag from parsed header
        tag = episode_tag(parsed.get("season"), parsed["episode"])

        # Validate --episode matches script header
        if args.episode is not None and args.episode != tag:
            print(f"ERROR: Script header indicates {tag} but "
                  f"--episode {args.episode} was specified")
            sys.exit(1)

        # Derive default output path from parsed season/episode
        slug = show_slug(parsed.get("show", "")) or resolve_slug(args.show)
        paths = derive_paths(slug, tag)
        if args.output is None:
            args.output = paths["parsed"]

        # Write debug CSV if requested (must happen after output path is resolved)
        if args.debug:
            debug_csv_path = os.path.splitext(args.output)[0] + ".csv"
            # Re-parse with debug output enabled
            parsed = parse_script(args.script, debug_output=debug_csv_path)

        # Write JSON output
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)

        if not args.quiet:
            print_summary(parsed)
            print_dialogue_preview(parsed, limit=args.preview)
            print(f"JSON written to: {args.output}")
            if args.debug:
                print(f"Debug CSV written to: {os.path.splitext(args.output)[0]}.csv")

        if args.stats and args.quiet:
            # --stats with --quiet: show only the speaker table
            print_speaker_stats(parsed)

        # Auto-generate cast/sfx configs if --episode provided and files absent
        if args.episode:
            cast_path = paths["cast"]
            sfx_path = paths["sfx"]
            if not os.path.exists(cast_path):
                generate_cast_config(parsed, cast_path)
            if not os.path.exists(sfx_path):
                generate_sfx_config(parsed, sfx_path)


if __name__ == "__main__":
    main()
