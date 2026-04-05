# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XILP006_cues_ingester.py — Cues Sheet Ingester

Parses a sound cues & music prompts markdown file into a structured asset
manifest, audits the shared SFX library, and optionally:

  - Generates NEW assets into ``SFX/`` via ElevenLabs Sound Effects API
  - Enriches the episode SFX config with accurate prompts/durations
    sourced directly from the cues sheet

Pipeline position: after XILP001 (script parse), before XILU002/XILP002
(SFX stem generation).

Usage::

    # Audit only — show library status, what needs generating, write manifest
    python XILP006_cues_ingester.py --episode S02E03

    # Same with explicit cues file path
    python XILP006_cues_ingester.py --episode S02E03 \\
        --cues "cues/Season 2, Episode 3 Sound Cues.md"

    # Generate NEW assets into SFX/ (requires ELEVENLABS_API_KEY)
    python XILP006_cues_ingester.py --episode S02E03 --generate

    # Enrich episode SFX config with cues-sheet prompts/durations
    python XILP006_cues_ingester.py --episode S02E03 --enrich-sfx-config

    # Preview sfx config changes without writing
    python XILP006_cues_ingester.py --episode S02E03 \\
        --enrich-sfx-config --dry-run

    # Full workflow: generate new assets and enrich sfx config
    python XILP006_cues_ingester.py --episode S02E03 \\
        --generate --enrich-sfx-config
"""

import argparse
import glob as _glob
import json
import math
import os
import re
import tempfile

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import derive_paths, resolve_slug
from xil_pipeline.sfx_common import file_nonempty, run_banner

logger = get_logger(__name__)

SFX_DIR = "SFX"
CUES_DIR = "cues"
DEFAULT_SFX_DURATION = 5.0   # seconds when no duration given in cues sheet
API_MAX_DURATION = 30.0       # ElevenLabs Sound Effects API hard cap
CREDITS_PER_SECOND = 40       # approximate ElevenLabs credit cost per second


# ─── Cues markdown parsing ──────────────────────────────────────────────────


def parse_duration(text: str) -> float | None:
    """Parse a human-readable duration string into seconds.

    Returns ``None`` for loop/loopable markers or unrecognised input.

    Examples::

        >>> parse_duration("60 seconds")
        60.0
        >>> parse_duration("2 minutes")
        120.0
        >>> parse_duration("90 seconds")
        90.0
        >>> parse_duration("Loop")
        # returns None
    """
    text = text.strip()
    if not text or re.search(r"\bloop", text, re.I):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(minutes?|min|seconds?|sec|s)\b", text, re.I)
    if not m:
        return None
    value = float(m.group(1))
    return value * 60.0 if m.group(2).lower().startswith("m") else value


def parse_cues_markdown(path: str) -> list[dict]:
    """Parse a cues & music prompts markdown file into a list of asset dicts.

    Handles three cue sheet sections:

    - ``MUSIC CUES``: ``### ASSET-ID (REUSE|NEW)`` heading blocks with
      ``**Prompt:**``, ``**Duration:**``, ``**Used:**`` on a single line
    - ``AMBIENCE``: same heading-block format
    - ``SOUND EFFECTS``: Markdown tables grouped under scene headings

    Each returned dict has keys::

        asset_id         – e.g. "MUS-THEME-MAIN-01"
        category         – "MUSIC", "AMBIENCE", or "SFX"
        reuse            – True if marked (REUSE), False if (NEW)
        prompt           – ElevenLabs generation prompt string
        duration_seconds – float seconds, or None (loop/unspecified)
        loop             – True for ambience and loop-marked assets
        scene            – scene label from SFX section, or None
    """
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    assets: list[dict] = []
    section: str | None = None
    scene: str | None = None
    pending: dict | None = None

    for raw in lines:
        s = raw.strip()

        # ── Top-level section heading (## …) ─────────────────────────────
        if s.startswith("## "):
            heading = re.sub(r"\*+", "", s[3:]).strip().upper()
            if "MUSIC" in heading and "CUE" in heading:
                section = "MUSIC"
            elif heading == "AMBIENCE":
                section = "AMBIENCE"
            elif "SOUND EFFECT" in heading:
                section = "SFX"
            else:
                section = None
            pending = None
            scene = None
            continue

        if section is None:
            continue

        # ── MUSIC / AMBIENCE: asset block heading (### …) ─────────────────
        if section in ("MUSIC", "AMBIENCE") and s.startswith("### "):
            heading = re.sub(r"\*+", "", s[4:]).strip()
            m = re.match(r"([\w][\w-]*(?:-\d+)?)\s*\(?(NEW|REUSE)\)?", heading, re.I)
            if m:
                pending = {
                    "asset_id": m.group(1).upper(),
                    "category": section,
                    "reuse": m.group(2).upper() == "REUSE",
                    "prompt": None,
                    "duration_seconds": None,
                    "loop": section == "AMBIENCE",
                    "scene": None,
                }
            continue

        # Prompt/Duration/Used data line for MUSIC / AMBIENCE
        if section in ("MUSIC", "AMBIENCE") and pending and "**Prompt:**" in s:
            pm = re.search(r"\*{1,2}Prompt:\*{1,2}\s*(.*?)\s*\*{1,2}Duration:", s)
            dm = re.search(r"\*{1,2}Duration:\*{1,2}\s*(.*?)\s*\*{1,2}Used:", s)
            if pm:
                pending["prompt"] = pm.group(1).strip()
            if dm:
                dur_raw = dm.group(1).strip()
                pending["duration_seconds"] = parse_duration(dur_raw)
                if re.search(r"\bloop", dur_raw, re.I):
                    pending["loop"] = True
            assets.append(pending)
            pending = None
            continue

        # ── SFX section: scene headings + table rows ──────────────────────
        if section == "SFX":
            if s.startswith("###"):
                m = re.match(r"###\s+(?:Scene\s+\d+:\s*)?(.*)", s)
                scene = re.sub(r"\*+", "", m.group(1)).strip() if m else s[4:].strip()
                continue

            if s.startswith("|"):
                cols = [c.strip() for c in s.split("|")]
                cols = [c for c in cols if c]
                if len(cols) < 2:
                    continue
                # Skip header and divider rows
                if re.match(r"Asset\s+Name", cols[0], re.I):
                    continue
                if not re.search(r"[A-Z]{2,}", cols[0]):
                    continue
                m = re.match(
                    r"\*{0,2}([\w][\w-]*-\d+)\s*\((NEW|REUSE)\)\*{0,2}",
                    cols[0], re.I,
                )
                if m:
                    assets.append({
                        "asset_id": m.group(1).upper(),
                        "category": "SFX",
                        "reuse": m.group(2).upper() == "REUSE",
                        "prompt": cols[1],
                        "duration_seconds": None,
                        "loop": False,
                        "scene": scene,
                    })

    return assets


# ─── Library helpers ─────────────────────────────────────────────────────────


def asset_library_path(asset_id: str, sfx_dir: str = SFX_DIR) -> str:
    """Return the SFX/ file path for a cues-sheet asset ID.

    ``'MUS-THEME-MAIN-01'`` → ``'SFX/mus-theme-main-01.mp3'``
    """
    return os.path.join(sfx_dir, f"{asset_id.lower()}.mp3")


def asset_status(asset: dict, sfx_dir: str = SFX_DIR) -> str:
    """Return 'EXISTS', ' REUSE', or '   NEW' for an asset.

    - ``EXISTS`` — asset ID file present in ``SFX/``
    - `` REUSE`` — marked REUSE in cues sheet, not yet in library
    - ``   NEW`` — marked NEW, needs API generation
    """
    path = asset_library_path(asset["asset_id"], sfx_dir)
    if file_nonempty(path):
        return "EXISTS"
    return " REUSE" if asset["reuse"] else "   NEW"


def generation_duration(asset: dict) -> float:
    """Return the API request duration, capped at API_MAX_DURATION.

    Falls back to ``DEFAULT_SFX_DURATION`` when ``duration_seconds`` is None,
    zero, or negative.
    """
    d = asset.get("duration_seconds")
    if d is None or d <= 0:
        return DEFAULT_SFX_DURATION
    return min(d, API_MAX_DURATION)


def credits_for_duration(duration: float) -> int:
    """Return the credit cost for a single generation call of *duration* seconds.

    Uses ``math.ceil`` so fractional-second requests are never underestimated.
    ElevenLabs bills per API call, so each call's cost must be rounded up
    independently before summing.
    """
    return math.ceil(duration * CREDITS_PER_SECOND)


# ─── Dry-run report ──────────────────────────────────────────────────────────


def dry_run_report(assets: list[dict], sfx_dir: str = SFX_DIR) -> None:
    """Print a formatted audit of cues sheet assets vs. the SFX library."""
    new_assets = [
        a for a in assets
        if not a["reuse"] and asset_status(a, sfx_dir).strip() == "NEW"
    ]
    total_new_dur = sum(generation_duration(a) for a in new_assets)
    total_credits = sum(credits_for_duration(generation_duration(a)) for a in new_assets)
    capped = [a for a in new_assets if (a.get("duration_seconds") or 0) > API_MAX_DURATION]
    exists_count = sum(1 for a in assets if asset_status(a, sfx_dir) == "EXISTS")
    reuse_missing = sum(
        1 for a in assets if a["reuse"] and asset_status(a, sfx_dir).strip() == "REUSE"
    )

    logger.info(f"\n{'='*72}")
    logger.info(f"CUES SHEET AUDIT — {len(assets)} assets total")
    logger.info(
        f"  {exists_count} in library  |  "
        f"{len(new_assets)} new to generate  |  "
        f"{reuse_missing} REUSE not yet in library"
    )
    logger.info(f"{'='*72}\n")

    by_cat: dict[str, list[dict]] = {}
    for a in assets:
        by_cat.setdefault(a["category"], []).append(a)

    for cat in ("MUSIC", "AMBIENCE", "SFX"):
        cat_assets = by_cat.get(cat, [])
        if not cat_assets:
            continue
        logger.info(f"  ── {cat} ──")
        for a in cat_assets:
            st = asset_status(a, sfx_dir)
            dur = a.get("duration_seconds")
            api_dur = generation_duration(a)
            dur_str = f"{dur:.0f}s" if dur else f"~{api_dur:.0f}s"
            cap_note = f" [CAPPED→{API_MAX_DURATION:.0f}s]" if dur and dur > API_MAX_DURATION else ""
            credits_note = f"  ~{credits_for_duration(api_dur)} cr" if st.strip() == "NEW" else ""
            loop_note = " [loop]" if a.get("loop") else ""
            logger.info(
                f"    [{st}] {a['asset_id']:<32} {dur_str:>8}"
                f"{cap_note}{credits_note}{loop_note}"
            )
            if st.strip() == "NEW":
                truncated = a["prompt"][:72] + ("…" if len(a["prompt"]) > 72 else "")
                logger.info(f"            prompt: {truncated}")
        logger.info("")

    if capped:
        logger.info(
            f"  NOTE: {len(capped)} asset(s) exceed the {API_MAX_DURATION:.0f}s API cap "
            "and will be generated at 30s."
        )
        logger.info("")

    logger.info(f"{'='*72}")
    logger.info(f"  New generation: {total_new_dur:.1f}s total, ~{total_credits} credits")
    logger.info(f"{'='*72}\n")


# ─── Asset generation ────────────────────────────────────────────────────────


def generate_new_assets(
    assets: list[dict],
    sfx_dir: str = SFX_DIR,
    client=None,
) -> None:
    """Generate NEW assets via ElevenLabs Sound Effects API into SFX/.

    Skips assets that already exist in the library.  Assets marked REUSE
    are never generated here — they must be sourced from the master library.
    """
    os.makedirs(sfx_dir, exist_ok=True)
    to_generate = [
        a for a in assets
        if not a["reuse"] and asset_status(a, sfx_dir).strip() == "NEW"
    ]
    if not to_generate:
        logger.info("All NEW assets already exist in library — nothing to generate.")
        return

    logger.info(f"Generating {len(to_generate)} new asset(s) into {sfx_dir}/…")
    for asset in to_generate:
        aid = asset["asset_id"]
        path = asset_library_path(aid, sfx_dir)
        dur = generation_duration(asset)
        orig = asset.get("duration_seconds")
        if orig and orig > API_MAX_DURATION:
            logger.warning(f"{aid}: {orig:.0f}s capped to {API_MAX_DURATION:.0f}s for API")
        logger.info(f"  Generating {aid} ({dur:.1f}s)…")
        stream = client.text_to_sound_effects.convert(
            text=asset["prompt"],
            duration_seconds=dur,
            prompt_influence=0.3,
        )
        tmp_fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
        os.close(tmp_fd)
        try:
            with open(tmp, "wb") as f:
                for chunk in stream:
                    if chunk:
                        f.write(chunk)
            os.rename(tmp, path)
            tmp = None
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        logger.info(f"    → {path}")

    logger.info(f"Done. {len(to_generate)} asset(s) generated.")


# ─── SFX config enrichment ───────────────────────────────────────────────────


def find_sfx_config_matches(asset_id: str, sfx_effects: dict) -> list[str]:
    """Return all sfx config keys containing the given asset ID as a substring.

    Example — ``'MUS-THEME-MAIN-01'`` matches both::

        'MUSIC: MUS-THEME-MAIN-01 — EERIE INDIE FOLK, FADES UNDER'
        'MUSIC: MUS-THEME-MAIN-01 — UP BRIEFLY, THEN OUT'
    """
    return [key for key in sfx_effects if asset_id.upper() in key.upper()]


def enrich_sfx_config(
    assets: list[dict],
    sfx_config_path: str,
    dry_run: bool = False,
) -> None:
    """Update sfx config entries with prompts/durations from the cues sheet.

    Matches assets to sfx config keys by asset ID substring.  For each
    matched entry, updates:

    - ``prompt`` — replaced with the richer cues sheet description
    - ``duration_seconds`` — set to cues sheet value, capped at 30s
    - ``loop`` — set to True for ambience assets

    In dry-run mode, prints a diff of what would change without writing.
    """
    if not os.path.exists(sfx_config_path):
        raise FileNotFoundError(
            f"SFX config not found: {sfx_config_path}\n"
            "Run XILP001 first or check your --episode flag."
        )
    with open(sfx_config_path, encoding="utf-8") as f:
        config = json.load(f)
    effects = config.get("effects", {})
    update_count = 0

    for asset in assets:
        matched_keys = find_sfx_config_matches(asset["asset_id"], effects)
        if not matched_keys:
            continue
        new_prompt = asset.get("prompt") or ""
        new_duration = generation_duration(asset)

        for key in matched_keys:
            entry = effects[key]
            old_prompt = entry.get("prompt", "")
            old_duration = entry.get("duration_seconds", 0.0)
            prompt_changed = new_prompt and (new_prompt != old_prompt)
            dur_changed = abs(new_duration - old_duration) >= 0.5
            if not prompt_changed and not dur_changed:
                continue
            update_count += 1
            if dry_run:
                logger.info(f"  WOULD UPDATE: {key}")
                if prompt_changed:
                    logger.info(f"    prompt: {old_prompt!r}")
                    logger.info(f"         → {new_prompt!r}")
                if dur_changed:
                    logger.info(f"    duration: {old_duration}s → {new_duration}s")
            else:
                if prompt_changed:
                    entry["prompt"] = new_prompt
                entry["duration_seconds"] = new_duration
                if asset.get("loop"):
                    entry["loop"] = True

    if not dry_run and update_count > 0:
        with open(sfx_config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        logger.info(
            f"Updated {update_count} entr{'y' if update_count == 1 else 'ies'} "
            f"in {sfx_config_path}"
        )
    elif update_count == 0:
        logger.info("No sfx config entries matched cues sheet assets — nothing to update.")
    else:
        logger.info(
            f"\n{update_count} entr{'y' if update_count == 1 else 'ies'} would be updated "
            "(pass --enrich-sfx-config without --dry-run to apply)."
        )


# ─── Manifest I/O ────────────────────────────────────────────────────────────


def write_manifest(assets: list[dict], episode_tag: str, cues_path: str) -> str:
    """Write a structured JSON manifest of the parsed cues sheet assets.

    Output path: ``cues/cues_manifest_<TAG>.json``

    Returns the output path.
    """
    manifest = {
        "episode": episode_tag,
        "source": os.path.basename(cues_path),
        "total_assets": len(assets),
        "new_count": sum(1 for a in assets if not a["reuse"]),
        "reuse_count": sum(1 for a in assets if a["reuse"]),
        "assets": assets,
    }
    os.makedirs(CUES_DIR, exist_ok=True)
    out_path = os.path.join(CUES_DIR, f"cues_manifest_{episode_tag}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    logger.info(f"Manifest written: {out_path}")
    return out_path


def find_cues_file(episode: str, slug: str | None = None, cues_dir: str = CUES_DIR) -> str | None:
    """Auto-detect a cues markdown file for the given episode.

    Checks for ``cues/cues_<slug>_<TAG>.md`` first, then falls back to
    the sole ``.md`` file in ``cues/`` when exactly one exists.
    """
    if not os.path.isdir(cues_dir):
        return None
    s = slug or resolve_slug()
    p = derive_paths(s, episode)
    canonical = p["cues"]
    if os.path.exists(canonical):
        return canonical
    candidates = _glob.glob(os.path.join(cues_dir, "*.md"))
    return candidates[0] if len(candidates) == 1 else None


# ─── CLI ─────────────────────────────────────────────────────────────────────


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-cues",
        description=(
            "Parse a sound cues & music prompts markdown file into an asset "
            "manifest, audit the SFX library, and optionally generate new "
            "assets or enrich the episode sfx config."
        ),
    )
    parser.add_argument(
        "--episode", required=True,
        help="Episode tag (e.g. S02E03) — derives sfx config path",
    )
    parser.add_argument(
        "--show", default=None,
        help="Show name override (default: from project.json)",
    )
    parser.add_argument(
        "--cues", default=None,
        help="Path to cues markdown file (auto-detected from cues/ if omitted)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help=(
            "Show audit report and enrichment diff without API calls or "
            "sfx config writes (manifest is always written)"
        ),
    )
    parser.add_argument(
        "--generate", action="store_true",
        help="Generate NEW assets via ElevenLabs API into SFX/",
    )
    parser.add_argument(
        "--enrich-sfx-config", action="store_true",
        help="Update episode SFX config with cues-sheet prompts/durations",
    )
    return parser


def main() -> None:
    """CLI entry point for the cues sheet ingester."""
    configure_logging()
    with run_banner():
        parser = get_parser()
        args = parser.parse_args()

        # Resolve cues file
        slug = resolve_slug(args.show)
        p = derive_paths(slug, args.episode)
        cues_path = args.cues or find_cues_file(args.episode, slug=slug)
        if cues_path is None:
            parser.error(
                f"No cues file found for {args.episode}. "
                f"Pass --cues PATH or name your file {p['cues']}"
            )

        sfx_config_path = p["sfx"]

        # Parse
        logger.info(f"Parsing: {cues_path}")
        assets = parse_cues_markdown(cues_path)
        new_count = sum(1 for a in assets if not a["reuse"])
        reuse_count = sum(1 for a in assets if a["reuse"])
        logger.info(f"Found {len(assets)} assets ({new_count} new, {reuse_count} reuse)")

        # Always write manifest and show audit report
        write_manifest(assets, args.episode, cues_path)
        dry_run_report(assets, SFX_DIR)

        # Generate new assets
        if args.generate:
            if args.dry_run:
                logger.info("--dry-run active: skipping API generation.")
            else:
                api_key = os.environ.get("ELEVENLABS_API_KEY")
                if not api_key:
                    logger.error("ELEVENLABS_API_KEY not set. Cannot generate assets.")
                else:
                    from elevenlabs.client import ElevenLabs
                    client = ElevenLabs(api_key=api_key)
                    generate_new_assets(assets, SFX_DIR, client=client)

        # Enrich sfx config
        if args.enrich_sfx_config:
            if not os.path.exists(sfx_config_path):
                logger.warning(
                    f"{sfx_config_path} not found — "
                    "skipping sfx config enrichment."
                )
            else:
                logger.info(f"\nEnriching {sfx_config_path}…")
                enrich_sfx_config(assets, sfx_config_path, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
