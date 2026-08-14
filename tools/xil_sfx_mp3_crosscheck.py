#!/usr/bin/env python3
"""xil_sfx_mp3_crosscheck.py — cross-check sfx_<tag>.json "source" mp3
references against the actual files under $XIL_PROJECTROOT/SFX/.

Lives in tools/; run from anywhere: python3 tools/xil_sfx_mp3_crosscheck.py

Scans every configs/*/sfx_<tag>.json for effects with an explicit
"source" field pointing at an mp3, and cross-references those paths
against a recursive listing of SFX/*.mp3. Reports, per mp3 file:
  - whether it is referenced by any sfx_<tag>.json (and by which
    show/tag(s), since one file can be reused across episodes),
  - or whether it appears to be unreferenced (a candidate for cleanup).

Only explicit "source" fields are treated as references. Effects with
no "source" (prompt-generated sfx/silence, landing under a
slug-derived filename computed at generation time) are NOT resolved
back to a filename here — see sfx_common.py's shared_sfx_path()/
ensure_shared_sfx() if that reverse-mapping is ever needed. This means
some "unreferenced" files may in fact be in active use via the
slug-derived naming path; treat the unreferenced list as candidates
for review, not a deletion list.

Output:
  - CSV at $XIL_PROJECTROOT/sfx_mp3_crosscheck.csv, one row per SFX/*.mp3
    file, columns: mp3_path, referenced, show, tag, config_path,
    declared_source.  A referenced file that is reused by multiple
    show/tag configs gets one row per (file, show, tag) reference.
  - Control totals to stdout: total mp3 files on disk, total distinct
    files referenced, total unreferenced, total declared sources found
    across all configs, and how many of those sources failed to
    resolve to a file on disk (dangling references).
"""
import csv
import glob
import json
import os
import sys
from pathlib import Path

PROJECTROOT = Path(os.environ.get("XIL_PROJECTROOT") or ".").resolve()
CONFIGS_DIR = PROJECTROOT / "configs"
SFX_DIR = PROJECTROOT / "SFX"
OUTPUT_CSV = PROJECTROOT / "sfx_mp3_crosscheck.csv"


def find_sfx_configs(configs_dir: Path) -> list[Path]:
    """Return all sfx_*.json files anywhere under configs_dir, sorted."""
    pattern = str(configs_dir / "**" / "sfx_*.json")
    return sorted(Path(p) for p in glob.glob(pattern, recursive=True))


def find_mp3_files(sfx_dir: Path) -> list[Path]:
    """Return all .mp3 files anywhere under sfx_dir, sorted."""
    pattern = str(sfx_dir / "**" / "*.mp3")
    return sorted(Path(p) for p in glob.glob(pattern, recursive=True))


def resolve_source(source: str, workspace_root: Path) -> str:
    """Resolve a config's declared source path the way sfx_common.py does:
    relative paths are resolved against the workspace root, not the
    config file's own directory or the CWD.

    Uses purely lexical normalization (no filesystem syscalls) — this
    project's SFX/ tree lives on a slow network/WSL mount where
    os.path.realpath() on every file is prohibitively slow, and no
    symlinks are expected here.
    """
    src = Path(source)
    if not src.is_absolute():
        src = workspace_root / src
    return os.path.normpath(str(src))


def show_and_tag_from_config_path(config_path: Path, configs_dir: Path) -> tuple[str, str]:
    """Derive (show, tag) from a config path like configs/<show>/sfx_<tag>.json."""
    rel = config_path.relative_to(configs_dir)
    show = rel.parts[0] if len(rel.parts) > 1 else ""
    tag = config_path.stem
    if tag.startswith("sfx_"):
        tag = tag[len("sfx_"):]
    return show, tag


def main() -> None:
    if not CONFIGS_DIR.is_dir():
        print(f"error: configs directory not found: {CONFIGS_DIR}", file=sys.stderr)
        raise SystemExit(1)
    if not SFX_DIR.is_dir():
        print(f"error: SFX directory not found: {SFX_DIR}", file=sys.stderr)
        raise SystemExit(1)

    config_paths = find_sfx_configs(CONFIGS_DIR)
    mp3_paths = find_mp3_files(SFX_DIR)
    mp3_normpaths = {os.path.normpath(str(p)) for p in mp3_paths}

    # references[mp3_realpath] -> list of (show, tag, config_path, declared_source)
    references: dict[str, list[tuple[str, str, str, str]]] = {}
    total_sources_declared = 0
    dangling: list[tuple[str, str, str]] = []  # (show, tag, declared_source)
    bad_configs: list[tuple[Path, str]] = []

    for config_path in config_paths:
        try:
            with open(config_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            bad_configs.append((config_path, str(exc)))
            continue

        show, tag = show_and_tag_from_config_path(config_path, CONFIGS_DIR)
        effects = data.get("effects", {}) if isinstance(data, dict) else {}
        for _key, effect in effects.items():
            if not isinstance(effect, dict):
                continue
            source = effect.get("source")
            if not source:
                continue
            if not str(source).lower().endswith(".mp3"):
                continue
            total_sources_declared += 1
            resolved_norm = resolve_source(source, PROJECTROOT)
            rel_config = str(config_path.relative_to(PROJECTROOT))
            if resolved_norm in mp3_normpaths:
                references.setdefault(resolved_norm, []).append(
                    (show, tag, rel_config, source)
                )
            else:
                dangling.append((f"{show}/{tag}", rel_config, source))

    # --- Write CSV ---
    rows = []
    for mp3_path in mp3_paths:
        norm = os.path.normpath(str(mp3_path))
        rel_mp3 = str(mp3_path.relative_to(PROJECTROOT))
        refs = references.get(norm, [])
        if refs:
            for show, tag, config_path, declared_source in refs:
                rows.append({
                    "mp3_path": rel_mp3,
                    "referenced": "yes",
                    "show": show,
                    "tag": tag,
                    "config_path": config_path,
                    "declared_source": declared_source,
                })
        else:
            rows.append({
                "mp3_path": rel_mp3,
                "referenced": "no",
                "show": "",
                "tag": "",
                "config_path": "",
                "declared_source": "",
            })

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["mp3_path", "referenced", "show", "tag", "config_path", "declared_source"],
        )
        writer.writeheader()
        writer.writerows(rows)

    # --- Control totals ---
    total_mp3_files = len(mp3_paths)
    total_referenced_files = len(references)
    total_unreferenced_files = total_mp3_files - total_referenced_files
    total_reference_rows = sum(len(v) for v in references.values())

    print(f"SFX configs scanned      : {len(config_paths)}  ({CONFIGS_DIR})")
    if bad_configs:
        print(f"  configs failed to parse : {len(bad_configs)}")
        for path, err in bad_configs:
            print(f"    - {path.relative_to(PROJECTROOT)}: {err}")
    print(f"MP3 files on disk        : {total_mp3_files}  ({SFX_DIR})")
    print(f"Declared mp3 sources     : {total_sources_declared}")
    print(f"  resolved to a file     : {total_sources_declared - len(dangling)}")
    print(f"  dangling (not found)   : {len(dangling)}")
    if dangling:
        for tag, config_path, declared_source in dangling:
            print(f"    - [{tag}] {config_path}: {declared_source!r}")
    print(f"Distinct files referenced: {total_referenced_files}")
    print(f"Reference rows (w/ reuse): {total_reference_rows}")
    print(f"Distinct files unreferenced: {total_unreferenced_files}")
    print()
    print(f"CSV written to: {OUTPUT_CSV}")
    print()
    print("Note: only explicit \"source\" fields count as references. Prompt-generated")
    print("effects with no \"source\" resolve to a slug-derived filename at generation")
    print("time and are not reverse-mapped here, so some \"unreferenced\" files may")
    print("still be in active use — treat that list as candidates for review.")


if __name__ == "__main__":
    main()
