"""XILU003 — CSV + SFX/Cast JSON annotation utility.

Reads a parsed episode CSV, joins it with the SFX JSON (keyed on direction
text) and the cast JSON (keyed on speaker), then writes an annotated output
CSV with SFX and cast configuration columns appended.

Usage::

    python XILU003_csv_sfx_join.py --episode S02E03
    python XILU003_csv_sfx_join.py --episode S02E03 --output my_review.csv
"""

import argparse
import csv
import json
import os
import sys

from xil_pipeline.models import derive_paths as _derive_paths
from xil_pipeline.models import resolve_slug
from xil_pipeline.sfx_common import run_banner, slugify_effect_key

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

_INPUT_COLS = [
    "md_line_num", "md_raw", "seq", "type", "section", "scene",
    "speaker", "direction", "text", "direction_type",
]

_SFX_COLS = [
    "sfx_type", "sfx_prompt", "sfx_duration_seconds",
    "sfx_prompt_influence", "sfx_loop", "sfx_slug", "sfx_matched",
]

_CAST_COLS = [
    "cast_full_name", "cast_voice_id", "cast_pan",
    "cast_filter", "cast_role", "cast_matched",
]

OUTPUT_COLS = _INPUT_COLS + _SFX_COLS + _CAST_COLS


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def derive_paths(episode: str, show: str | None = None) -> tuple[str, str, str, str]:
    """Derive default file paths from the episode tag (e.g. ``'S02E03'``).

    Returns:
        ``(csv_path, sfx_path, cast_path, out_path)``
    """
    slug = resolve_slug(show)
    p = _derive_paths(slug, episode)
    return p["parsed_csv"], p["sfx"], p["cast"], p["annotated_csv"]


# ---------------------------------------------------------------------------
# Per-row join logic
# ---------------------------------------------------------------------------

def join_sfx(row: dict, effects: dict, default_influence: float | None) -> dict:
    """Return SFX annotation columns for one CSV row.

    Only ``type == "direction"`` rows can match an effects entry.  All other
    row types get blank sfx columns with ``sfx_matched=FALSE``.

    Args:
        row: A single CSV row dict.
        effects: The ``effects`` mapping from the SFX JSON.
        default_influence: Fallback ``prompt_influence`` from ``defaults``.

    Returns:
        Dict of SFX annotation columns ready to merge into the output row.
    """
    blank = {c: "" for c in _SFX_COLS}
    blank["sfx_matched"] = "FALSE"

    if row.get("type") != "direction":
        return blank

    text = row.get("text", "")
    if text not in effects:
        return blank

    entry = effects[text]
    influence = entry.get("prompt_influence")
    if influence is None:
        influence = default_influence

    return {
        "sfx_type": entry.get("type", "sfx"),
        "sfx_prompt": entry.get("prompt") or "",
        "sfx_duration_seconds": entry.get("duration_seconds", ""),
        "sfx_prompt_influence": "" if influence is None else influence,
        "sfx_loop": "TRUE" if entry.get("loop") else "",
        "sfx_slug": slugify_effect_key(text),
        "sfx_matched": "TRUE",
    }


def join_cast(row: dict, cast: dict) -> dict:
    """Return cast annotation columns for one CSV row.

    Only ``type == "dialogue"`` rows can carry a speaker key.  All other
    row types get blank cast columns with ``cast_matched=FALSE``.

    Args:
        row: A single CSV row dict.
        cast: The ``cast`` mapping from the cast JSON.

    Returns:
        Dict of cast annotation columns ready to merge into the output row.
    """
    blank = {c: "" for c in _CAST_COLS}
    blank["cast_matched"] = "FALSE"

    if row.get("type") != "dialogue":
        return blank

    speaker = row.get("speaker", "")
    if not speaker or speaker not in cast:
        return blank

    member = cast[speaker]
    return {
        "cast_full_name": member.get("full_name", ""),
        "cast_voice_id": member.get("voice_id", ""),
        "cast_pan": member.get("pan", ""),
        "cast_filter": "TRUE" if member.get("filter") else "FALSE",
        "cast_role": member.get("role", ""),
        "cast_matched": "TRUE",
    }


# ---------------------------------------------------------------------------
# Core join function
# ---------------------------------------------------------------------------

def annotate_csv(
    csv_path: str,
    sfx_path: str,
    cast_path: str,
    out_path: str,
) -> tuple[int, int, int, int, int]:
    """Join parsed CSV with SFX and cast configs and write annotated output.

    Args:
        csv_path: Path to the input parsed CSV.
        sfx_path: Path to the SFX JSON config.
        cast_path: Path to the cast JSON config.
        out_path: Destination path for the annotated output CSV.

    Returns:
        ``(total_rows, direction_rows, sfx_matched, dialogue_rows, cast_matched)``
    """
    with open(sfx_path, encoding="utf-8") as f:
        sfx_cfg = json.load(f)
    effects: dict = sfx_cfg.get("effects", {})
    default_influence: float | None = sfx_cfg.get("defaults", {}).get("prompt_influence")

    with open(cast_path, encoding="utf-8") as f:
        cast_cfg = json.load(f)
    cast: dict = cast_cfg.get("cast", {})

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    direction_rows = [r for r in rows if r.get("type") == "direction"]
    dialogue_rows = [r for r in rows if r.get("type") == "dialogue"]
    sfx_matched = sum(1 for r in direction_rows if r.get("text", "") in effects)
    cast_matched = sum(1 for r in dialogue_rows if r.get("speaker", "") in cast)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_COLS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            sfx_ann = join_sfx(row, effects, default_influence)
            cast_ann = join_cast(row, cast)
            out_row = {**{c: row.get(c, "") for c in _INPUT_COLS}, **sfx_ann, **cast_ann}
            writer.writerow(out_row)

    return len(rows), len(direction_rows), sfx_matched, len(dialogue_rows), cast_matched


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    with run_banner():
        parser = argparse.ArgumentParser(
            description="Annotate a parsed episode CSV with SFX and cast config data."
        )
        parser.add_argument(
            "--episode", required=True,
            help="Episode tag (e.g. S02E03) — derives default input/output paths",
        )
        parser.add_argument("--show", default=None, help="Show name override (default: from project.json)")
        parser.add_argument("--csv", dest="csv_path", help="Override input CSV path")
        parser.add_argument("--sfx", dest="sfx_path", help="Override SFX JSON path")
        parser.add_argument("--cast", dest="cast_path", help="Override cast JSON path")
        parser.add_argument("--output", dest="out_path", help="Override output CSV path")
        args = parser.parse_args()

        csv_def, sfx_def, cast_def, out_def = derive_paths(args.episode, show=args.show)
        csv_path = args.csv_path or csv_def
        sfx_path = args.sfx_path or sfx_def
        cast_path = args.cast_path or cast_def
        out_path = args.out_path or out_def

        if os.path.abspath(out_path) == os.path.abspath(csv_path):
            print(
                f"ERROR: output path '{out_path}' is the same as input '{csv_path}'",
                file=sys.stderr,
            )
            sys.exit(1)

        for path, label in [
            (csv_path, "CSV"),
            (sfx_path, "SFX JSON"),
            (cast_path, "Cast JSON"),
        ]:
            if not os.path.exists(path):
                print(f"ERROR: {label} file not found: {path}", file=sys.stderr)
                sys.exit(1)

        total, n_dir, sfx_hit, n_dlg, cast_hit = annotate_csv(
            csv_path, sfx_path, cast_path, out_path
        )

        print(f"Rows written: {total}")
        print(f"  SFX matched:  {sfx_hit} / {n_dir} direction rows")
        print(f"  Cast matched: {cast_hit} / {n_dlg} dialogue rows")
        print(f"Output: {out_path}")


if __name__ == "__main__":
    main()
