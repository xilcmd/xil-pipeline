# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

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

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import (
    ParsedScript,
    ScriptStats,
    derive_paths,
    episode_tag,
    resolve_project_type,
    resolve_season,
    resolve_season_title,
    resolve_slug,
    show_slug,
)
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

# Built-in speaker definitions — used as fallback when no speakers.json exists
_BUILTIN_KNOWN_SPEAKERS = [
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

_BUILTIN_SPEAKER_KEYS = {
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

# Module-level aliases — set to built-in defaults, updated by load_speakers()
KNOWN_SPEAKERS = list(_BUILTIN_KNOWN_SPEAKERS)
SPEAKER_KEYS = dict(_BUILTIN_SPEAKER_KEYS)


def load_speakers(
    path: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Load speaker definitions from a JSON file or fall back to built-in defaults.

    Resolution order:

    1. Explicit *path* (from ``--speakers`` CLI flag)
    2. ``configs/{slug}/speakers.json`` (normalized layout)
    3. ``speakers.json`` in the current working directory (legacy fallback)
    4. Built-in ``_BUILTIN_KNOWN_SPEAKERS`` / ``_BUILTIN_SPEAKER_KEYS``

    The JSON file is an array of objects with ``display`` and ``key`` fields::

        [
            {"display": "ADAM", "key": "adam"},
            {"display": "MR. PATTERSON", "key": "mr_patterson"}
        ]

    The returned list is automatically sorted longest-first so compound names
    match before short ones.

    Args:
        path: Explicit path to a speakers JSON file.  ``None`` triggers
            auto-detection.

    Returns:
        A tuple of ``(known_speakers_list, speaker_keys_dict)``.
    """
    # Determine which file to load
    speakers_file = path
    if speakers_file is None:
        # 2a. configs/{slug}/speakers.json (normalized layout)
        try:
            from xil_pipeline.models import load_project_config, show_slug
            cfg = load_project_config()
            slug = show_slug(cfg.show)
            from xil_pipeline.models import get_workspace_root
            normalized = str(get_workspace_root() / "configs" / slug / "speakers.json")
            if os.path.exists(normalized):
                speakers_file = normalized
        except Exception:
            pass
    if speakers_file is None:
        # 2b. speakers.json at workspace root (legacy fallback)
        from xil_pipeline.models import get_workspace_root
        cwd_file = str(get_workspace_root() / "speakers.json")
        if os.path.exists(cwd_file):
            speakers_file = cwd_file

    if speakers_file is None:
        return list(_BUILTIN_KNOWN_SPEAKERS), dict(_BUILTIN_SPEAKER_KEYS)

    with open(speakers_file, encoding="utf-8") as f:
        data = json.load(f)

    known: list[str] = []
    keys: dict[str, str] = {}
    for entry in data:
        display = entry["display"]
        key = entry["key"]
        known.append(display)
        keys[display] = key

    # Sort longest-first for correct compound-name matching
    known.sort(key=len, reverse=True)

    return known, keys

# Section detection
SECTION_MAP = {
    "COLD OPEN": "cold-open",
    "OPENING CREDITS": "opening-credits",
    "CHAPTER ONE": "chapter1",
    "CHAPTER 1": "chapter1",                    # numeral variant
    "CHAPTER TWO": "chapter2",
    "CHAPTER 2": "chapter2",                    # numeral variant
    "CHAPTER THREE": "chapter3",
    "CHAPTER 3": "chapter3",                    # numeral variant
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

# ---------------------------------------------------------------------------
# Per-type section maps
# ---------------------------------------------------------------------------

PODCAST_SECTIONS: dict[str, str] = {
    "COLD OPEN": "cold-open",
    "OPENING CREDITS": "opening-credits",
    "ACT ONE": "act1",
    "ACT 1": "act1",
    "ACT TWO": "act2",
    "ACT 2": "act2",
    "ACT THREE": "act3",
    "ACT 3": "act3",
    "ACT FOUR": "act4",
    "ACT 4": "act4",
    "MID-EPISODE BREAK": "mid-break",
    "CLOSING": "closing",
    "POST-CREDITS SCENE": "post-credits",
    "INTRO": "intro",
    "OUTRO": "outro",
}

_AUDIOBOOK_CHAPTERS: dict[str, str] = {
    f"CHAPTER {word.upper()}": f"chapter{num}"
    for num, word in enumerate([
        "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT",
        "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN",
        "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN", "TWENTY",
        "TWENTY-ONE", "TWENTY-TWO", "TWENTY-THREE", "TWENTY-FOUR", "TWENTY-FIVE",
        "TWENTY-SIX", "TWENTY-SEVEN", "TWENTY-EIGHT", "TWENTY-NINE", "THIRTY",
    ], start=1)
}
_AUDIOBOOK_CHAPTERS.update({f"CHAPTER {n}": f"chapter{n}" for n in range(1, 31)})

AUDIOBOOK_SECTIONS: dict[str, str] = {
    "PROLOGUE": "prologue",
    "EPILOGUE": "epilogue",
    "AUTHOR'S NOTE": "authors-note",
    "AUTHOR\u2019S NOTE": "authors-note",
    **_AUDIOBOOK_CHAPTERS,
}

DRAMA_SECTIONS: dict[str, str] = {
    "PROLOGUE": "prologue",
    "EPILOGUE": "epilogue",
    "INTERMISSION": "intermission",
    "ACT ONE": "act1",
    "ACT 1": "act1",
    "ACT TWO": "act2",
    "ACT 2": "act2",
    "ACT THREE": "act3",
    "ACT 3": "act3",
    "ACT FOUR": "act4",
    "ACT 4": "act4",
    "COLD OPEN": "cold-open",
    "CLOSING": "closing",
    "POST-CREDITS SCENE": "post-credits",
}

SPECIAL_SECTIONS: dict[str, str] = {
    **PODCAST_SECTIONS,
    **AUDIOBOOK_SECTIONS,
    **DRAMA_SECTIONS,
    **{f"SEGMENT {n}": f"segment{n}" for n in range(1, 16)},
}


def get_section_map(project_type: str = "podcast") -> dict[str, str]:
    """Return the section-header-to-slug map for the given content type.

    Falls back to the legacy :data:`SECTION_MAP` entries not covered by the
    type-specific map so that existing show-specific section names continue
    to parse correctly.

    Args:
        project_type: One of ``"podcast"``, ``"audiobook"``, ``"drama"``,
            ``"special"``.  Unknown values fall back to the full legacy map.

    Returns:
        Combined section map for the parser to use.
    """
    type_maps: dict[str, dict[str, str]] = {
        "podcast": PODCAST_SECTIONS,
        "audiobook": AUDIOBOOK_SECTIONS,
        "drama": DRAMA_SECTIONS,
        "special": SPECIAL_SECTIONS,
    }
    base = type_maps.get(project_type, SECTION_MAP)
    # Merge legacy entries not already in the type map (show-specific variants)
    merged = dict(SECTION_MAP)
    merged.update(base)
    return merged


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


def try_match_speaker(
    line: str,
    known_speakers: list[str] | None = None,
    speaker_keys: dict[str, str] | None = None,
) -> tuple[str, str | None, str] | None:
    """Match a known speaker name at the start of a line.

    Args:
        line: A stripped line from the script.
        known_speakers: Ordered list of speaker display names (longest-first).
            Defaults to the module-level ``KNOWN_SPEAKERS``.
        speaker_keys: Mapping from display names to normalized keys.
            Defaults to the module-level ``SPEAKER_KEYS``.

    Returns:
        A tuple of ``(speaker_key, direction, spoken_text)`` if a known
        speaker is found, or ``None`` if no speaker matches.
    """
    if known_speakers is None:
        known_speakers = KNOWN_SPEAKERS
    if speaker_keys is None:
        speaker_keys = SPEAKER_KEYS

    for speaker in known_speakers:
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
        return speaker_keys[speaker], direction, spoken_text

    return None


def is_stage_direction(line: str) -> bool:
    """Check if a line is a stage direction like ``[SFX: ...]`` or ``[BEAT]``.

    Args:
        line: A stripped line from the script.

    Returns:
        ``True`` if the line starts with ``[`` and contains ``]``.
    """
    return line.startswith("[") and "]" in line


def _parse_direction_hint(raw: str) -> tuple[str, str | None]:
    """Strip a scriptwriter SFX-source hint from a direction text.

    Scriptwriters may annotate directions with a filename hint separated
    by a pipe, e.g.::

        SFX: RADIO STATIC — BRIEF TUNING | sfx_radio-static-tuning-transition.mp3

    Returns the clean direction text and the SFX source path (``"SFX/<filename>"``),
    or ``None`` if no hint is present.
    """
    if " | " in raw:
        clean, hint = raw.split(" | ", 1)
        hint = hint.strip()
        if hint.endswith(".mp3") or hint.endswith(".wav"):
            return clean.strip(), f"SFX/{hint}"
    return raw.strip(), None


def is_section_header(line: str, section_map: dict[str, str] | None = None) -> bool:
    """Check if a line matches a known section header.

    Args:
        line: A stripped line from the script.
        section_map: Section map to check against.  Defaults to :data:`SECTION_MAP`.

    Returns:
        ``True`` if the line matches a key in the section map.
    """
    return line.strip() in (section_map if section_map is not None else SECTION_MAP)


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


def parse_script_header(line: str) -> tuple[str, int | None, int, str, str | None] | None:
    """Extract show, season, episode, title, and season_title from the script header line.

    Parses the first line of a production script, which follows the format::

        SHOW [Season N:] Episode N: "Episode Title" [Arc: "Arc Title"] ...

    Season is optional — scripts without a season declaration return ``None`` for
    the season element.  Title is the first double-quoted string after
    ``Episode N:``.  Arc title (season title) is the quoted string after ``Arc:``;
    it is ``None`` when no ``Arc:`` declaration is present.  Falls back to bare
    text after ``Episode N:`` when no quoted strings are present.

    Args:
        line: The first non-empty line of the production script, after
            markdown escapes have been removed.

    Returns:
        A tuple of ``(show, season, episode, title, season_title)`` where
        ``season`` and ``season_title`` are ``None`` when not declared, or
        ``None`` if the line does not match the expected header format.
    """
    # Must contain "Episode N" to be a valid header
    ep_match = re.search(r"Episode\s+(\d+)", line)
    if not ep_match:
        return None

    # Show: text before the first Season or Episode keyword
    show_match = re.match(r"^(.+?)\s+(?:Season\s+\d+|Episode\s+\d+)", line)
    show = show_match.group(1).strip() if show_match else "Unknown Show"

    # Season: optional
    season_match = re.search(r"Season\s+(\d+)", line)
    season = int(season_match.group(1)) if season_match else None

    episode = int(ep_match.group(1))

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

    # Season/arc title: quoted string after "Arc:" (e.g. Arc: "The Holiday Shift")
    arc_match = re.search(r'\bArc:\s*"([^"]+)"', line)
    season_title = arc_match.group(1) if arc_match else None

    return show, season, episode, title, season_title


def parse_script(
    filepath: str,
    debug_output: str | None = None,
    speakers_path: str | None = None,
    project_type: str | None = None,
) -> dict:
    """Parse a markdown production script into structured entries.

    Reads a markdown file and extracts dialogue lines, stage directions,
    section headers, and scene headers into a sequence-numbered list of
    entries.

    Args:
        filepath: Path to the markdown production script file.
        debug_output: If provided, write a diagnostic CSV to this path
            mapping each markdown source line to its parsed entry.
            Text fields are truncated at 200 characters. Defaults to
            ``None`` (no CSV written).
        speakers_path: Path to a ``speakers.json`` file.  ``None`` uses
            the default resolution order (see :func:`load_speakers`).
        project_type: Content type from ``project.json`` (``"podcast"``,
            ``"audiobook"``, ``"drama"``, ``"special"``).  ``None`` reads
            from ``project.json`` in the current directory, defaulting to
            ``"podcast"`` when the file is absent.

    Returns:
        Dictionary with keys ``show``, ``season``, ``episode``, ``title``,
        ``source_file``, ``entries`` (list of entry dicts), and
        ``stats`` (aggregate statistics dict). Validates against
        the ``ParsedScript`` model.

    Raises:
        FileNotFoundError: If the script file does not exist.
    """
    if project_type is None:
        project_type = resolve_project_type()
    active_section_map = get_section_map(project_type)
    known_speakers, speaker_keys = load_speakers(speakers_path)

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
    first_line = lines[0].strip() if lines else ""
    header = parse_script_header(first_line) if first_line else None
    if header and header[2] is not None:
        show, season, episode, title, season_title = header
        start = 1
    else:
        show, season, episode, title, season_title = "Unknown Show", None, 1, "", None

    # Apply project.json fallbacks when the script header omits Season/Arc declarations
    season = resolve_season(season)
    season_title = resolve_season_title(season_title)

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
        if is_section_header(line, active_section_map):
            current_section = active_section_map[line.strip()]
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
                clean_text, sfx_source = _parse_direction_hint(bracket_text.strip())
                direction_type = classify_direction(clean_text)
                if direction_type is None:
                    # Acting note in square brackets (e.g. [drawn out]) — not a technical cue
                    logger.debug(f"  Skipping unrecognized embedded direction: [{clean_text}]")
                    continue
                seq += 1
                entry = {
                    "seq": seq,
                    "type": "direction",
                    "section": current_section,
                    "scene": current_scene,
                    "speaker": None,
                    "direction": None,
                    "text": clean_text,
                    "direction_type": direction_type,
                }
                if sfx_source:
                    entry["sfx_source"] = sfx_source
                entries.append(entry)
                debug_line_map.append((i + 1, lines[i], len(entries) - 1))

            last_dialogue_idx = None
            continue

        # Stage directions: [SFX: ...], [MUSIC: ...], [BEAT], etc.
        # Handle lines with multiple directions like [MUSIC: ...] [SFX: ...]
        if is_stage_direction(line):
            # Extract all bracketed sections
            brackets = re.findall(r"\[([^\]]+)\]", line)
            for bracket_text in brackets:
                clean_text, sfx_source = _parse_direction_hint(bracket_text.strip())
                direction_type = classify_direction(clean_text)
                if direction_type is None:
                    # Acting note in square brackets (e.g. [drawn out]) — not a technical cue
                    logger.debug(f"  Skipping unrecognized direction: [{clean_text}]")
                    continue
                seq += 1
                entry = {
                    "seq": seq,
                    "type": "direction",
                    "section": current_section,
                    "scene": current_scene,
                    "speaker": None,
                    "direction": None,
                    "text": clean_text,
                    "direction_type": direction_type,
                }
                if sfx_source:
                    entry["sfx_source"] = sfx_source
                entries.append(entry)
                debug_line_map.append((i + 1, lines[i], len(entries) - 1))
            last_dialogue_idx = None
            continue

        # Dialogue lines: SPEAKER (direction) text
        match = try_match_speaker(line, known_speakers, speaker_keys)
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
        season_title=season_title,
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
        logger.info("  No dialogue entries found.")
        return

    total_lines = sum(r["lines"] for r in rows)
    total_words = sum(r["words"] for r in rows)
    total_chars = sum(r["chars"] for r in rows)

    logger.info(f"\n{'Speaker':<15} {'Lines':>6} {'%':>6} {'Words':>7} {'%':>6} {'Chars':>8} {'%':>6}")
    logger.info(f"{'-'*15} {'-'*6} {'-'*6} {'-'*7} {'-'*6} {'-'*8} {'-'*6}")
    for r in rows:
        logger.info(f"{r['speaker']:<15} {r['lines']:>6} {r['pct_lines']:>5.1f}%"
              f" {r['words']:>7,} {r['pct_words']:>5.1f}%"
              f" {r['chars']:>8,} {r['pct_chars']:>5.1f}%")
    logger.info(f"{'-'*15} {'-'*6} {'-'*6} {'-'*7} {'-'*6} {'-'*8} {'-'*6}")
    logger.info(f"{'TOTAL':<15} {total_lines:>6}        {total_words:>7,}        {total_chars:>8,}")
    logger.info("")


def print_summary(parsed: dict) -> None:
    """Print a human-readable summary of the parsed script.

    Displays show metadata, entry counts, TTS character budget,
    and a per-speaker breakdown of lines, words, and characters.

    Args:
        parsed: Output dictionary from ``parse_script()``.
    """
    stats = parsed["stats"]
    tag = episode_tag(parsed.get("season"), parsed["episode"])
    logger.info(f"\n{'='*60}")
    logger.info(f"PARSED: {parsed['show']} {tag} — {parsed['title']}")
    logger.info(f"Source: {parsed['source_file']}")
    logger.info(f"{'='*60}")
    logger.info(f"  Total entries:      {stats['total_entries']}")
    logger.info(f"  Dialogue lines:     {stats['dialogue_lines']}")
    logger.info(f"  Stage directions:   {stats['direction_lines']}")
    logger.info(f"  TTS characters:     {stats['characters_for_tts']:,}")
    logger.info(f"  Speakers:           {', '.join(stats['speakers'])}")
    logger.info(f"  Sections:           {', '.join(stats['sections'])}")
    logger.info(f"{'='*60}")

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

    logger.info(f"\n--- Dialogue Preview ({len(dialogue_entries)} lines) ---\n")
    for e in dialogue_entries:
        scene_label = e["scene"] or e["section"] or "?"
        direction_label = f" ({e['direction']})" if e["direction"] else ""
        text_preview = e["text"][:80] + "..." if len(e["text"]) > 80 else e["text"]
        logger.info(f"  {e['seq']:03d} | {scene_label:<16} | {e['speaker']:<14}{direction_label}")
        logger.info(f"       {text_preview}")
        logger.info("")


def generate_cast_config(parsed: dict, cast_path: str, tag_override: str | None = None) -> None:
    """Generate a skeleton cast config JSON from parsed script data.

    Creates a cast config with all speakers found in the parsed script,
    using ``voice_id="TBD"`` and sensible defaults for pan, filter, and role.
    The user must fill in voice IDs via ``XILU001_discover_voices_T2S.py``.

    Args:
        parsed: Parsed script dict from :func:`parse_script`.
        cast_path: Output path for the cast config JSON.
        tag_override: Raw non-episodic tag (e.g. ``"V01C03"``); when set,
            ``season``/``episode`` are written as ``null`` and ``tag_override``
            is added to the config.
    """
    # Build reverse mapping: speaker_key -> display name (first entry per key wins)
    key_to_display: dict[str, str] = {}
    for display, key in SPEAKER_KEYS.items():
        if key not in key_to_display:
            key_to_display[key] = display

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
        "show": parsed.get("show", "Unknown Show"),
        "season": None if tag_override else parsed.get("season"),
        "episode": None if tag_override else parsed.get("episode", 1),
        "title": parsed.get("title", ""),
        "season_title": parsed.get("season_title"),
        "cast": cast,
    }
    if tag_override:
        config["tag_override"] = tag_override

    os.makedirs(os.path.dirname(cast_path) or ".", exist_ok=True)
    with open(cast_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    logger.info(f"Created {cast_path} with {len(speakers)} speakers "
          f"(voice_id=TBD — run XILU001 to assign)")


def generate_sfx_config(parsed: dict, sfx_path: str, tag_override: str | None = None) -> None:
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

        sfx_source = entry.get("sfx_source")

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
        elif sfx_source:
            # Scriptwriter provided a source file hint — use it instead of a stub prompt.
            dur = 30.0 if text.startswith("AMBIENCE:") else (15.0 if text.startswith("MUSIC:") else 5.0)
            effect: dict = {"source": sfx_source, "duration_seconds": dur}
            if text.startswith("AMBIENCE:"):
                effect["loop"] = True
            effects[text] = effect
            sfx_count += 1
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
        "show": parsed.get("show", "Unknown Show"),
        "season": None if tag_override else parsed.get("season"),
        "episode": None if tag_override else parsed.get("episode", 1),
        "defaults": {"prompt_influence": 0.3},
        "effects": effects,
    }
    if tag_override:
        config["tag_override"] = tag_override

    os.makedirs(os.path.dirname(sfx_path) or ".", exist_ok=True)
    with open(sfx_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

    total = silence_count + sfx_count
    logger.info(f"Created {sfx_path} with {total} effects "
          f"({silence_count} silence, {sfx_count} sfx "
          f"— review prompts before generation)")


def backfill_sfx_sources(parsed: dict, sfx_path: str) -> None:
    """Add missing ``source`` fields to an existing SFX config from parsed hints.

    When a script is re-parsed and the SFX config already exists, any direction
    entries that carry an ``sfx_source`` hint are used to update the sfx config
    in three ways:

    1. **Clean key already exists, no source** — adds ``source`` field, removes
       stub ``prompt`` if it matched the key text.
    2. **Stale piped key exists** (``"KEY | file.mp3"`` from a pre-fix parse) —
       renames it to the clean key and adds ``source``.
    3. **Key absent entirely** — adds a new entry with ``source`` and sensible
       defaults (``loop: True`` for AMBIENCE, appropriate ``duration_seconds``).

    Entries that already have a ``source`` field are never touched.

    Args:
        parsed: Parsed script dict (after hint stripping).
        sfx_path: Path to the existing SFX config JSON to update in-place.
    """
    with open(sfx_path, encoding="utf-8") as f:
        sfx_data = json.load(f)

    effects = sfx_data.setdefault("effects", {})

    # Build a lookup from piped-key → clean-key for stale entries already in config
    stale_key_map: dict[str, str] = {}
    for existing_key in list(effects.keys()):
        clean, hint = _parse_direction_hint(existing_key)
        if hint and clean != existing_key:
            stale_key_map[existing_key] = clean

    updated = 0
    seen_clean: set[str] = set()
    for entry in parsed["entries"]:
        if entry["type"] != "direction":
            continue
        sfx_source = entry.get("sfx_source")
        if not sfx_source:
            continue
        text = entry["text"]  # clean key
        if text in seen_clean:
            continue
        seen_clean.add(text)

        if text in effects:
            # Case 1: clean key present — add source if missing
            if "source" not in effects[text]:
                effects[text]["source"] = sfx_source
                if effects[text].get("prompt") == text:
                    del effects[text]["prompt"]
                updated += 1
        else:
            # Case 2: stale piped key exists — rename + add source
            stale_key = next((k for k, v in stale_key_map.items() if v == text), None)
            if stale_key and stale_key in effects:
                old_entry = effects.pop(stale_key)
                old_entry["source"] = sfx_source
                old_entry.pop("prompt", None)
                effects[text] = old_entry
                updated += 1
            else:
                # Case 3: key absent entirely — create it
                dur = 30.0 if text.startswith("AMBIENCE:") else (15.0 if text.startswith("MUSIC:") else 5.0)
                new_entry: dict = {"source": sfx_source, "duration_seconds": dur}
                if text.startswith("AMBIENCE:"):
                    new_entry["loop"] = True
                effects[text] = new_entry
                updated += 1

    if updated:
        with open(sfx_path, "w", encoding="utf-8") as f:
            json.dump(sfx_data, f, indent=2, ensure_ascii=False)
        logger.info("Backfilled %d source hint(s) in %s", updated, sfx_path)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-parse",
        description="Parse production script markdown into structured JSON",
    )
    parser.add_argument("script", help="Path to the production script markdown file")
    tag_group = parser.add_mutually_exclusive_group()
    tag_group.add_argument(
        "--episode", default=None,
        help="Episode tag (e.g. S01E01) — validates header and auto-generates absent cast/sfx configs",
    )
    tag_group.add_argument(
        "--tag", default=None,
        help="Raw tag for non-episodic content (e.g. V01C03, D01, CH003) — skips season/episode header validation",
    )
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
    parser.add_argument("--speakers", default=None,
                        help="Path to speakers.json (default: auto-detect from CWD, then built-in)")
    return parser


def main() -> None:
    """CLI entry point for script parsing."""
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        # Load speakers (updates module-level KNOWN_SPEAKERS/SPEAKER_KEYS for downstream use)
        loaded_speakers, loaded_keys = load_speakers(args.speakers)
        global KNOWN_SPEAKERS, SPEAKER_KEYS
        KNOWN_SPEAKERS = loaded_speakers
        SPEAKER_KEYS = loaded_keys

        # Parse first so we can derive the output path from metadata
        parsed = parse_script(args.script, speakers_path=args.speakers)

        if args.tag:
            # Non-episodic mode: use the raw tag string, skip header validation
            tag = args.tag
        else:
            # Episodic mode: derive tag from parsed header
            tag = episode_tag(parsed.get("season"), parsed["episode"])
            if args.episode is not None and args.episode != tag:
                logger.error(f"Script header indicates {tag} but "
                      f"--episode {args.episode} was specified")
                sys.exit(1)

        # Derive default output path from parsed season/episode
        # --show overrides the show name extracted from the script header
        if args.show:
            parsed["show"] = args.show
        slug = show_slug(parsed.get("show", "")) or resolve_slug(args.show)
        paths = derive_paths(slug, tag)
        if args.output is None:
            args.output = paths["parsed"]

        # Ensure output directory exists before any file writes (including debug CSV)
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

        # Write debug CSV if requested (must happen after output path is resolved)
        if args.debug:
            debug_csv_path = os.path.splitext(args.output)[0] + ".csv"
            # Re-parse with debug output enabled
            parsed = parse_script(args.script, debug_output=debug_csv_path)
            if args.show:
                parsed["show"] = args.show

        # Write JSON output (create parent dirs for new layout e.g. parsed/{slug}/)
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(parsed, f, indent=2, ensure_ascii=False)

        if not args.quiet:
            print_summary(parsed)
            print_dialogue_preview(parsed, limit=args.preview)
            logger.info(f"JSON written to: {args.output}")
            if args.debug:
                logger.info(f"Debug CSV written to: {os.path.splitext(args.output)[0]}.csv")

        if args.stats and args.quiet:
            # --stats with --quiet: show only the speaker table
            print_speaker_stats(parsed)

        # Auto-generate cast/sfx configs if --episode or --tag provided and files absent
        trigger_tag = args.tag or args.episode
        if not trigger_tag:
            logger.warning(
                "No --episode tag given — cast and SFX skeleton configs were NOT created. "
                "Re-run with --episode %s to generate them.",
                tag,
            )
        if trigger_tag:
            cast_path = paths["cast"]
            sfx_path = paths["sfx"]
            if not os.path.exists(cast_path):
                generate_cast_config(parsed, cast_path, tag_override=args.tag)
            if not os.path.exists(sfx_path):
                generate_sfx_config(parsed, sfx_path, tag_override=args.tag)
            else:
                backfill_sfx_sources(parsed, sfx_path)


if __name__ == "__main__":
    main()
