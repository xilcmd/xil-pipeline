# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Discover and inspect ElevenLabs voices available in this workspace.

Lists all voices returned by the API with enriched metadata drawn from
``labels``, ``sharing``, ``verified_languages``, and other fields exposed
by the SDK.  Useful for finding voice IDs and assessing voice suitability
before configuring cast files.

Usage::

    python XILU001_discover_voices_T2S.py
    python XILU001_discover_voices_T2S.py --category professional
    python XILU001_discover_voices_T2S.py --category cloned generated
    python XILU001_discover_voices_T2S.py --search tina
    python XILU001_discover_voices_T2S.py --verbose
    python XILU001_discover_voices_T2S.py --json
    python XILU001_discover_voices_T2S.py --id WtA85syCrJwasGeHGH2p

Categories returned by the API: ``premade``, ``cloned``, ``generated``,
``professional`` (Professional Voice Clone / PVC).

Permissions note: all workspace voices (including PVCs copied from the
voice library) return ``permission_on_resource = 'admin'`` — no access
barriers exist. PVCs show ``is_owner = False`` but remain fully usable.
"""

import argparse
import datetime
import json as _json
import os

from elevenlabs.client import ElevenLabs

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)

# Human-readable category labels matching the portal display
CATEGORY_LABELS = {
    "premade":      "Premade",
    "cloned":       "Instant Clone",
    "generated":    "Generated",
    "professional": "Professional Clone (PVC)",
}

# Sharing sub-categories (inside sharing.category for library voices)
SHARING_CATEGORY_LABELS = {
    "professional": "Professional Clone",
    "high_quality": "High Quality",
}


def _fmt_unix(ts: int | None) -> str:
    """Format a Unix timestamp as YYYY-MM-DD, or '' if None."""
    if ts is None:
        return ""
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d")


def _fmt_languages(verified_languages: list) -> str:
    """Return a compact language list string, e.g. 'en, de, es (+14)'."""
    if not verified_languages:
        return ""
    langs = list(dict.fromkeys(vl.language for vl in verified_languages))
    if len(langs) <= 3:
        return ", ".join(langs)
    return f"{', '.join(langs[:3])} (+{len(langs) - 3})"


def build_voice_record(v) -> dict:
    """Extract all relevant fields from a Voice SDK object into a plain dict."""
    labels = v.labels or {}
    sharing = v.sharing

    # Library (sharing) metadata — richer description/name for copied voices
    library_name = sharing.name if sharing else None
    library_desc = sharing.description if sharing else None
    sharing_cat = SHARING_CATEGORY_LABELS.get(sharing.category, sharing.category) if sharing else None
    notice_days = sharing.notice_period if sharing else None

    # Prefer local description; fall back to library description
    description = v.description or library_desc or ""

    verified_langs = v.verified_languages or []
    lang_str = _fmt_languages(verified_langs)

    return {
        "voice_id":          v.voice_id,
        "name":              v.name,
        "library_name":      library_name,
        "category":          v.category,
        "sharing_category":  sharing_cat,
        "description":       description,
        "gender":            labels.get("gender", ""),
        "age":               labels.get("age", ""),
        "accent":            labels.get("accent", ""),
        "descriptive":       labels.get("descriptive", ""),
        "use_case":          labels.get("use_case", ""),
        "language":          labels.get("language", ""),
        "verified_languages": lang_str,
        "verified_lang_count": len(set(vl.language for vl in verified_langs)),
        "high_quality_models": v.high_quality_base_model_ids or [],
        "is_owner":          v.is_owner,
        "is_bookmarked":     v.is_bookmarked,
        "permission":        v.permission_on_resource,
        "created_at":        _fmt_unix(v.created_at_unix),
        "notice_days":       notice_days,
    }


def print_verbose(rec: dict) -> None:
    """Print all fields for a single voice record."""
    cat_label = CATEGORY_LABELS.get(rec["category"], rec["category"] or "?")
    if rec["sharing_category"]:
        cat_label = f"{cat_label} / {rec['sharing_category']}"

    logger.info(f"  Name         : {rec['name']}")
    if rec["library_name"] and rec["library_name"] != rec["name"]:
        logger.info(f"  Library name : {rec['library_name']}")
    logger.info(f"  Voice ID     : {rec['voice_id']}")
    logger.info(f"  Category     : {cat_label}")
    if rec["description"]:
        logger.info(f"  Description  : {rec['description']}")
    logger.info(f"  Gender       : {rec['gender'] or '—'}")
    logger.info(f"  Age          : {rec['age'] or '—'}")
    logger.info(f"  Accent       : {rec['accent'] or '—'}")
    logger.info(f"  Tone/style   : {rec['descriptive'] or '—'}")
    logger.info(f"  Use case     : {rec['use_case'] or '—'}")
    logger.info(f"  Language     : {rec['language'] or '—'}")
    if rec["verified_languages"]:
        logger.info(f"  Verified langs: {rec['verified_languages']} ({rec['verified_lang_count']} total)")
    if rec["high_quality_models"]:
        logger.info(f"  HQ models    : {', '.join(rec['high_quality_models'])}")
    logger.info(f"  Owner        : {'Yes' if rec['is_owner'] else 'No (library copy)'}")
    logger.info(f"  Bookmarked   : {'Yes' if rec['is_bookmarked'] else 'No'}")
    logger.info(f"  Permission   : {rec['permission'] or 'none'}")
    logger.info(f"  Created      : {rec['created_at'] or '—'}")
    if rec["notice_days"]:
        logger.info(f"  Notice period: {rec['notice_days']} days")
    logger.info("")


def print_compact(rec: dict) -> None:
    """Print a single compact summary line for a voice."""
    cat = CATEGORY_LABELS.get(rec["category"], rec["category"] or "?")
    gender = rec["gender"] or "?"
    age = rec["age"] or "?"
    accent = rec["accent"] or "?"
    tone = rec["descriptive"] or "?"
    langs = f" | langs: {rec['verified_languages']}" if rec["verified_languages"] else ""
    desc = f" | {rec['description'][:60]}" if rec["description"] else ""
    logger.info(
        f"  {rec['name']:<28} {rec['voice_id']}  [{cat}]"
        f"\n    {gender}, {age}, {accent}, {tone}{langs}{desc}"
    )


def update_cast(cast_path: str, records_by_id: dict, dry_run: bool = False) -> None:
    """Back-fill cast JSON fields from API voice metadata.

    For each cast member whose ``voice_id`` is not ``"TBD"``, looks up
    the voice in *records_by_id* and updates:

    - ``role`` — set to the voice description if currently ``"TBD"``
    - ``language_code`` — set from ``labels.language`` if currently ``null``

    ``full_name`` is intentionally left unchanged (it is the character
    name, not the voice name).

    Args:
        cast_path: Path to the cast JSON file to update.
        records_by_id: Mapping of ``voice_id`` → voice record dict from
            :func:`build_voice_record`.
        dry_run: When ``True``, print the diff but do not write the file.
    """
    with open(cast_path, encoding="utf-8") as f:
        cast_data = _json.load(f)

    changes: list[str] = []

    for key, member in cast_data.get("cast", {}).items():
        vid = member.get("voice_id", "TBD")
        if vid == "TBD":
            logger.info(f"  {key}: voice_id is TBD — skipping")
            continue
        rec = records_by_id.get(vid)
        if rec is None:
            logger.info(f"  {key} ({vid}): not found in workspace voices — skipping")
            continue

        # role: fill if still "TBD"
        if member.get("role") == "TBD" and rec.get("description"):
            old = member["role"]
            member["role"] = rec["description"]
            changes.append(f"  {key}.role: {old!r} → {member['role']!r}")

        # language_code: fill if null/missing
        if not member.get("language_code") and rec.get("language"):
            member["language_code"] = rec["language"]
            changes.append(f"  {key}.language_code: null → {member['language_code']!r}")

    if not changes:
        logger.info("  No updates needed — cast file is already fully populated.")
        return

    logger.info(f"  {'(dry run) ' if dry_run else ''}Changes ({len(changes)}):")
    for c in changes:
        logger.info(c)

    if not dry_run:
        with open(cast_path, "w", encoding="utf-8") as f:
            _json.dump(cast_data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.info(f"  Written: {cast_path}")


def main() -> None:
    """CLI entry point for voice discovery."""
    configure_logging()
    with run_banner():
        parser = argparse.ArgumentParser(
            description="List ElevenLabs voices with enriched metadata"
        )
        parser.add_argument(
            "--category", nargs="+",
            metavar="CAT",
            help="Filter by category: premade cloned generated professional",
        )
        parser.add_argument(
            "--search",
            metavar="TEXT",
            help="Case-insensitive substring filter on name or description",
        )
        parser.add_argument(
            "--id",
            metavar="VOICE_ID",
            help="Show full detail for a single voice ID",
        )
        parser.add_argument(
            "--verbose", "-v", action="store_true",
            help="Print all fields for each voice",
        )
        parser.add_argument(
            "--json", action="store_true",
            help="Output results as JSON array",
        )
        parser.add_argument(
            "--update-cast",
            metavar="CAST_JSON",
            help="Back-fill role and language_code in a cast JSON from API voice metadata",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="With --update-cast: show changes without writing the file",
        )
        args = parser.parse_args()

        client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))
        response = client.voices.get_all()
        voices = response.voices

        records = [build_voice_record(v) for v in voices]
        records_by_id = {r["voice_id"]: r for r in records}

        # --update-cast: enrich a cast JSON from API metadata, then exit
        if args.update_cast:
            logger.info(f"\n--- Updating cast file: {args.update_cast} ---")
            update_cast(args.update_cast, records_by_id, dry_run=args.dry_run)
            return

        # --id: single voice detail
        if args.id:
            matches = [r for r in records if r["voice_id"] == args.id]
            if not matches:
                logger.info(f"Voice ID {args.id!r} not found in your workspace.")
                return
            print_verbose(matches[0])
            return

        # Category filter
        if args.category:
            cats = {c.lower() for c in args.category}
            records = [r for r in records if (r["category"] or "").lower() in cats]

        # Search filter
        if args.search:
            q = args.search.lower()
            records = [
                r for r in records
                if q in (r["name"] or "").lower()
                or q in (r["description"] or "").lower()
                or q in (r["library_name"] or "").lower()
            ]

        # Sort: bookmarked first, then by name
        records.sort(key=lambda r: (not r["is_bookmarked"], r["name"].lower()))

        if args.json:
            print(_json.dumps(records, indent=2))
            return

        # Summary header
        cat_counts: dict[str, int] = {}
        for r in records:
            cat_counts[r["category"] or "unknown"] = cat_counts.get(r["category"] or "unknown", 0) + 1

        logger.info(f"\n--- ElevenLabs Voices ({len(records)} shown) ---")
        for cat, count in sorted(cat_counts.items()):
            logger.info(f"  {CATEGORY_LABELS.get(cat, cat)}: {count}")
        logger.info("")

        if args.verbose:
            for rec in records:
                print_verbose(rec)
        else:
            for rec in records:
                print_compact(rec)
            logger.info("")
            logger.info("  Use --verbose for full details, --json for machine-readable output,")
            logger.info("  --id <VOICE_ID> for a single voice, --category / --search to filter.")


if __name__ == "__main__":
    main()
