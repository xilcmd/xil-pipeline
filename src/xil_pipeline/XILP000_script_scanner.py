# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
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

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.sfx_common import run_banner
from xil_pipeline.XILP001_script_parser import (
    SECTION_MAP,
    _display_to_key,
    _parse_direction_hint,
    extract_cast_from_script,
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

logger = get_logger(__name__)

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

        # Stage directions — never mine for speaker names, regardless of content.
        if is_stage_direction(line):
            continue

        # Try speaker match first — handles both multi-line format (bare "ADAM") and
        # single-line format ("ADAM (direction) dialogue text"). try_match_speaker
        # matches on the leading all-caps speaker prefix regardless of what follows.
        match = try_match_speaker(line, known_speakers, speaker_keys)
        if match:
            speaker_key, _direction, _spoken = match
            # Use the exact speaker prefix that matched, not the full line
            _ks = known_speakers if known_speakers is not None else []
            display = next((s for s in _ks if line.startswith(s)), line.split("(")[0].strip())
            if speaker_key not in speakers:
                speakers[speaker_key] = {
                    "display": display,
                    "count": 0,
                    "lines": [],
                }
            speakers[speaker_key]["count"] += 1
            speakers[speaker_key]["lines"].append(lineno)
            continue

        # For section headers and unrecognized candidates, the line must be
        # all-caps (no trailing lowercase dialogue text).
        if not is_all_caps_candidate(line):
            continue

        # Try section header
        if is_section_header(line):
            sections.append({
                "text": line,
                "slug": SECTION_MAP[line.strip()],
                "line": lineno,
            })
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
# Extended validations
# ---------------------------------------------------------------------------

def scan_direction_texts(
    lines: list[str],
    sfx_effects: dict,
) -> dict:
    """Audit direction texts against an existing SFX config.

    Classifies each unique direction text as:
    - ``matched``: key exists in sfx_effects with a ``source`` field
    - ``hinted``: key not in sfx_effects but script has a pipe-hint (new source)
    - ``new``: key not in sfx_effects and no hint (will need generation prompt)

    Args:
        lines: Normalized script lines.
        sfx_effects: The ``effects`` dict from ``sfx_<TAG>.json``.

    Returns a dict with keys ``matched``, ``hinted``, ``new`` — each a list of
    ``{"text": str, "hint": str | None, "lines": [int, ...]}`` dicts.
    """
    seen: dict[str, dict] = {}
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not is_stage_direction(line):
            continue
        inner = line[1:-1].strip()
        if not inner:
            continue
        # Only SFX/MUSIC/AMBIENCE directions are config-keyed; skip BEAT/VINTAGE FILTER
        direction_type = None
        for dt in ("SFX:", "MUSIC:", "AMBIENCE:"):
            if inner.startswith(dt):
                direction_type = dt
                break
        if direction_type is None:
            continue
        clean, hint = _parse_direction_hint(inner)
        if clean not in seen:
            seen[clean] = {"text": clean, "hint": hint, "lines": []}
        seen[clean]["lines"].append(i + 1)
        if seen[clean]["hint"] is None and hint:
            seen[clean]["hint"] = hint

    matched, hinted, new = [], [], []
    for info in seen.values():
        text = info["text"]
        if text in sfx_effects and sfx_effects[text].get("source"):
            matched.append(info)
        elif info["hint"]:
            hinted.append(info)
        else:
            new.append(info)

    return {"matched": matched, "hinted": hinted, "new": new}


def scan_vintage_filter_pairing(lines: list[str]) -> list[dict]:
    """Check that every VINTAGE FILTER ENGAGES has a matching DISENGAGES.

    Returns a list of unpaired marker dicts:
    ``{"text": str, "line": int, "type": "ENGAGES" | "DISENGAGES"}``
    """
    stack: list[dict] = []
    unpaired: list[dict] = []
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not is_stage_direction(line):
            continue
        inner = line[1:-1].strip()
        if inner == "VINTAGE FILTER ENGAGES":
            stack.append({"text": inner, "line": i + 1, "type": "ENGAGES"})
        elif inner == "VINTAGE FILTER DISENGAGES":
            if stack:
                stack.pop()
            else:
                unpaired.append({"text": inner, "line": i + 1, "type": "DISENGAGES"})
    # Any unclosed ENGAGES still on the stack
    unpaired.extend(stack)
    return unpaired


def scan_preamble_postamble(sections: list[dict]) -> dict:
    """Check whether PREAMBLE and POSTAMBLE sections are present.

    Args:
        sections: The ``sections`` list from :func:`scan_script`.

    Returns a dict ``{"preamble": bool, "postamble": bool}``.
    """
    slugs = {s["slug"] for s in sections}
    return {
        "preamble": "preamble" in slugs,
        "postamble": "postamble" in slugs,
    }


def scan_ambience_coverage(lines: list[str]) -> list[dict]:
    """Check that every looping AMBIENCE direction has a stop marker.

    A stop marker is either ``[AMBIENCE: STOP]`` or a direction ending in
    ``FADES OUT``.  Returns a list of unclosed ambience dicts:
    ``{"text": str, "line": int}``
    """
    # Stack of open (text, lineno) pairs — one slot since only one loop active
    open_stack: list[dict] = []
    unclosed: list[dict] = []
    for i, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not is_stage_direction(line):
            continue
        inner = line[1:-1].strip()
        if not inner.startswith("AMBIENCE:"):
            continue
        body = inner[len("AMBIENCE:"):].strip()
        if body == "STOP" or body.upper().endswith("FADES OUT"):
            if open_stack:
                open_stack.pop()
            # ignore spurious STOP with nothing open
        else:
            # New loop — if one was already open without a stop, flag it
            if open_stack:
                unclosed.append(open_stack.pop())
            open_stack.append({"text": inner, "line": i + 1})
    unclosed.extend(open_stack)
    return unclosed


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

    # PREAMBLE / POSTAMBLE
    pp = scan.get("preamble_postamble")
    if pp is not None:
        lines.append("")
        lines.append("PREAMBLE / POSTAMBLE")
        for key in ("preamble", "postamble"):
            mark = "✓" if pp[key] else "⚠"
            status = "present" if pp[key] else "MISSING — add before broadcast production"
            lines.append(f"  {mark}  {key:<12} {status}")

    # VINTAGE FILTER pairing
    vf_unpaired = scan.get("vintage_filter_unpaired", [])
    lines.append("")
    lines.append("VINTAGE FILTER PAIRING")
    if vf_unpaired:
        for item in vf_unpaired:
            lines.append(f"  ⚠  {item['type']:<12} unpaired  line {item['line']}")
    else:
        lines.append("  ✓  all markers paired (or none present)")

    # Ambience coverage
    amb_unclosed = scan.get("ambience_unclosed", [])
    lines.append("")
    lines.append("AMBIENCE LOOP COVERAGE")
    if amb_unclosed:
        for item in amb_unclosed:
            lines.append(f"  ⚠  no stop marker for [{item['text']}]  line {item['line']}")
    else:
        lines.append("  ✓  all ambience loops have stop markers (or none present)")

    # Direction text audit
    dt = scan.get("direction_texts")
    if dt is not None:
        n_matched = len(dt["matched"])
        n_hinted = len(dt["hinted"])
        n_new = len(dt["new"])
        lines.append("")
        lines.append(
            f"DIRECTION TEXT AUDIT  "
            f"({n_matched} matched / {n_hinted} hinted / {n_new} new)"
        )
        for info in dt["matched"]:
            lines.append(f"  ✓  [reuse]  {info['text']}")
        for info in dt["hinted"]:
            fname = os.path.basename(info["hint"]) if info["hint"] else "?"
            lines.append(f"  +  [hydrate] {info['text']}  → {fname}")
        for info in dt["new"]:
            lines.append(f"  ·  [new]    {info['text']}")
        if n_hinted:
            lines.append(
                f"\n  ℹ  {n_hinted} hinted — run `xil sfx-hydrate` to write source fields"
            )
        if n_new:
            lines.append(
                f"  ℹ  {n_new} new — add prompts to sfx config before producing"
            )

    lines.append("")
    # Final verdict
    fatal = bool(scan["unrecognized"]) or bool(vf_unpaired)
    if fatal:
        lines.append("❌  Errors found — resolve before running XILP001.")
    else:
        lines.append("✅  All sections and speakers recognized — safe to run XILP001.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def _resolve_speakers_path(args_speakers, args_show) -> str | None:
    """Resolve the speakers.json path from CLI args (shared by all modes)."""
    speakers_path = args_speakers
    if speakers_path is None and args_show:
        from xil_pipeline.models import show_slug
        slug = show_slug(args_show)
        candidate = os.path.join("configs", slug, "speakers.json")
        if os.path.exists(candidate):
            speakers_path = candidate
    return speakers_path


def harvest_cast(scripts_dir: str, speakers_path: str | None, apply: bool = False) -> None:
    """Scan all scripts for CAST: entries and optionally add new ones to speakers.json.

    Reports every character declared in any CAST: block that is absent from
    the current speakers.json.  With *apply=True*, appends those entries.
    """
    import glob as _glob

    all_scripts = sorted(_glob.glob(os.path.join(scripts_dir, "*.md")))
    if not all_scripts:
        logger.info("No .md scripts found in %s", scripts_dir)
        return

    # Collect all CAST-declared speakers across all scripts
    all_cast: dict[str, dict] = {}  # key → {display, key, scripts: []}
    for script_path in all_scripts:
        script_lines = load_and_normalize(script_path)
        for entry in extract_cast_from_script(script_lines):
            key = entry["key"]
            if key not in all_cast:
                all_cast[key] = {"display": entry["display"], "key": key, "scripts": []}
            all_cast[key]["scripts"].append(os.path.basename(script_path))

    if not all_cast:
        logger.info(
            "No CAST: blocks found in %d script(s).  "
            "Run --backfill-cast to add them.", len(all_scripts)
        )
        return

    # Compare with existing speakers.json
    existing_data: list[dict] = []
    existing_keys: set[str] = set()
    if speakers_path and os.path.exists(speakers_path):
        with open(speakers_path, encoding="utf-8") as f:
            existing_data = json.load(f)
        existing_keys = {e["key"] for e in existing_data}

    new_entries = [v for k, v in sorted(all_cast.items()) if k not in existing_keys]

    logger.info(
        "CAST harvest — %d script(s) scanned, %d unique speaker(s) found, %d new",
        len(all_scripts), len(all_cast), len(new_entries),
    )
    for e in new_entries:
        logger.info("  +  %-30s  key: %-22s  in: %s",
                    e["display"], e["key"], ", ".join(e["scripts"]))

    if not new_entries:
        logger.info("✅  All CAST-declared speakers already in speakers.json.")
        return

    if not apply:
        logger.info("(dry-run) Re-run with --yes to add these to speakers.json.")
        return

    # Determine write path when speakers.json doesn't exist yet
    write_path = speakers_path
    if not write_path:
        from xil_pipeline.models import get_workspace_root, load_project_config
        from xil_pipeline.models import show_slug as _show_slug
        try:
            cfg = load_project_config()
            slug = _show_slug(cfg.show)
            write_path = str(get_workspace_root() / "configs" / slug / "speakers.json")
        except Exception:
            from xil_pipeline.models import get_workspace_root
            write_path = str(get_workspace_root() / "speakers.json")

    os.makedirs(os.path.dirname(write_path), exist_ok=True)
    for e in new_entries:
        existing_data.append({"display": e["display"], "key": e["key"]})
    with open(write_path, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, indent=2)
    logger.info("✅  Added %d new speaker(s) to %s", len(new_entries), write_path)


def backfill_cast(
    scripts_dir: str,
    speakers_path: str | None,
    parsed_dir: str | None = None,
    dry_run: bool = True,
) -> None:
    """Add CAST: blocks to scripts that don't have one.

    Speaker lists are inferred from existing parsed JSON (most reliable) or
    by body-scanning the raw script against the known speakers.json list.
    """
    import glob as _glob

    from xil_pipeline.models import get_workspace_root

    root = str(get_workspace_root())
    all_scripts = sorted(_glob.glob(os.path.join(scripts_dir, "*.md")))
    if not all_scripts:
        logger.info("No .md scripts found in %s", scripts_dir)
        return

    # Load registry for role enrichment in the CAST block
    registry: dict[str, dict] = {}
    if speakers_path and os.path.exists(speakers_path):
        with open(speakers_path, encoding="utf-8") as f:
            for e in json.load(f):
                registry[e["key"]] = e

    known_speakers, speaker_keys = load_speakers(speakers_path)

    if parsed_dir is None:
        parsed_dir = os.path.join(root, "parsed")

    modified = 0
    for script_path in all_scripts:
        script_lines = load_and_normalize(script_path)

        # Already has a CAST block
        if any(ln.strip() == "CAST:" for ln in script_lines):
            logger.info("  SKIP  %s  (CAST: block already present)", os.path.basename(script_path))
            continue

        # Infer speakers: try matching parsed JSON by source_file first
        cast_display: list[str] = []
        script_basename = os.path.basename(script_path)
        if os.path.isdir(parsed_dir):
            for parsed_path in sorted(_glob.glob(
                os.path.join(parsed_dir, "**", "parsed_*.json"), recursive=True
            )):
                try:
                    with open(parsed_path, encoding="utf-8") as f:
                        parsed = json.load(f)
                    if script_basename not in (parsed.get("source_file") or ""):
                        continue
                    seen_keys: list[str] = []
                    seen_set: set[str] = set()
                    for entry in parsed.get("entries", []):
                        if entry.get("type") == "dialogue":
                            k = entry.get("speaker", "")
                            if k and k not in seen_set:
                                seen_keys.append(k)
                                seen_set.add(k)
                    for k in seen_keys:
                        if k in registry:
                            cast_display.append(registry[k]["display"])
                        else:
                            cast_display.append(k.replace("_", " ").upper())
                    break
                except Exception:
                    continue

        # Fall back to body scan against known speakers
        if not cast_display:
            body_scan = scan_script(script_lines, known_speakers, speaker_keys)
            cast_display = [info["display"] for info in body_scan["speakers"].values()]

        if not cast_display:
            logger.info("  SKIP  %s  (no speakers found)", script_basename)
            continue

        # Build CAST block
        cast_block: list[str] = ["CAST:"]
        for display in cast_display:
            role = registry.get(_display_to_key(display), {}).get("role") or ""
            entry_line = f"* {display}"
            if role and role not in ("TBD", ""):
                entry_line += f" — {role}"
            cast_block.append(entry_line)
        cast_block.append("")

        if dry_run:
            logger.info("  ADD   %s:", script_basename)
            for bl in cast_block:
                logger.info("        %s", bl)
            continue

        # Write CAST block before the first === divider in the original file
        with open(script_path, encoding="utf-8") as f:
            original_lines = f.readlines()

        insert_at = None
        for i, raw_line in enumerate(original_lines):
            if raw_line.strip() in ("===", "---"):
                insert_at = i
                break

        if insert_at is None:
            logger.warning("  WARN  %s  (no === divider found, skipping)", script_basename)
            continue

        new_lines = (
            original_lines[:insert_at]
            + [bl + "\n" for bl in cast_block]
            + original_lines[insert_at:]
        )
        with open(script_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        logger.info("  WROTE %s  (%d speaker(s))", script_basename, len(cast_display))
        modified += 1

    if dry_run:
        logger.info("(dry-run) Re-run without --dry-run to write changes.")
    else:
        logger.info("✅  Backfill complete — %d script(s) updated.", modified)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-scan",
        description="Pre-flight scanner: check a production script for unknown speakers/sections.",
    )
    parser.add_argument(
        "path", nargs="?", default=None,
        help="Path to the markdown production script (required unless --harvest-cast or --backfill-cast)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output machine-readable JSON instead of the human report",
    )
    parser.add_argument("--speakers", default=None,
                        help="Path to speakers.json (default: auto-detect from CWD, then built-in)")
    parser.add_argument("--show", default=None, metavar="NAME",
                        help="Show name override — selects configs/{slug}/speakers.json "
                             "(default: from project.json)")
    parser.add_argument(
        "--sfx", default=None, metavar="PATH",
        help="Path to sfx_<TAG>.json — enables direction-text audit against existing config",
    )
    parser.add_argument(
        "--episode", default=None, metavar="TAG",
        help="Episode tag (e.g. S04E04) — auto-discovers sfx config when --sfx is omitted",
    )
    parser.add_argument(
        "--harvest-cast", action="store_true",
        help="Scan all scripts in scripts/ for CAST: blocks and report speakers missing from speakers.json",
    )
    parser.add_argument(
        "--backfill-cast", action="store_true",
        help="Add CAST: blocks to scripts that don't have one, inferring speakers from parsed JSON or body scan",
    )
    parser.add_argument(
        "--scripts-dir", default=None, metavar="DIR",
        help="Scripts directory for --harvest-cast / --backfill-cast (default: scripts/ under workspace root)",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Apply changes without confirmation (for --harvest-cast and --backfill-cast)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview backfill changes without writing files (implied when --yes is absent for --backfill-cast)",
    )
    return parser


def main():
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        speakers_path = _resolve_speakers_path(args.speakers, args.show)

        # ── Migration modes ───────────────────────────────────────────────────
        from xil_pipeline.models import get_workspace_root
        scripts_dir = args.scripts_dir or os.path.join(str(get_workspace_root()), "scripts")

        if args.harvest_cast:
            harvest_cast(scripts_dir, speakers_path, apply=args.yes)
            return

        if args.backfill_cast:
            dry_run = not args.yes
            backfill_cast(scripts_dir, speakers_path, dry_run=dry_run)
            return

        # ── Normal single-script scan ─────────────────────────────────────────
        if not args.path:
            logger.error("path argument required (or use --harvest-cast / --backfill-cast)")
            sys.exit(1)

        if not os.path.exists(args.path):
            logger.error("File not found: %s", args.path)
            sys.exit(1)

        lines = load_and_normalize(args.path)

        # Extract CAST block and merge into speaker recognition
        cast_entries = extract_cast_from_script(lines)
        known_speakers, speaker_keys = load_speakers(speakers_path, cast_entries=cast_entries)

        # Extract header for display
        header = {}
        for line in lines[:10]:
            if line.strip():
                result = parse_script_header(line)
                if result:
                    show, season, episode, title, _season_title = result
                    header = {"show": show, "season": season, "episode": episode, "title": title}
                break

        scan = scan_script(lines, known_speakers, speaker_keys)

        # --- Extended validations ---

        # PREAMBLE / POSTAMBLE presence
        scan["preamble_postamble"] = scan_preamble_postamble(scan["sections"])

        # VINTAGE FILTER pairing
        scan["vintage_filter_unpaired"] = scan_vintage_filter_pairing(lines)

        # Ambience loop coverage
        scan["ambience_unclosed"] = scan_ambience_coverage(lines)

        # Direction text audit (only when SFX config is available)
        sfx_path = args.sfx
        if sfx_path is None and args.episode:
            from xil_pipeline.models import derive_paths, resolve_slug
            slug = resolve_slug(args.show)
            sfx_path = derive_paths(slug, args.episode).get("sfx")

        if sfx_path and os.path.exists(sfx_path):
            with open(sfx_path, encoding="utf-8") as f:
                sfx_data = json.load(f)
            scan["direction_texts"] = scan_direction_texts(
                lines, sfx_data.get("effects", {})
            )

        if args.json:
            print(json.dumps(scan, indent=2))
        else:
            logger.info(format_report(scan, header))

        fatal = bool(scan["unrecognized"]) or bool(scan["vintage_filter_unpaired"])
        if fatal:
            sys.exit(1)


if __name__ == "__main__":
    main()
