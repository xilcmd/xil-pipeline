# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Gradio web dashboard for xil-pipeline.

A browser-based GUI that supplements the CLI for visual oversight,
audio preview, and sharing episode review with collaborators.

**Usage:**

```bash
xil-gui                              # opens http://localhost:7860
xil-gui --port 8080                  # custom port
xil-gui --share                      # generate public URL for partner access (72h tunnel)
xil-gui --output session.log         # append timestamped activity log to file
```

Install the optional [gui] extra first:
    pip install 'xil-pipeline[gui]'
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import Request as _FastAPIRequest

from xil_pipeline.models import get_workspace_root
from xil_pipeline.sfx_common import read_sfx_grade as _read_sfx_grade
from xil_pipeline.sfx_common import write_sfx_grade as _write_sfx_grade

# ── Optional activity log (set by --output in main()) ─────────────────────────

_activity_log: io.TextIOWrapper | None = None


def _log_activity(msg: str) -> None:
    """Write a timestamped line to the activity log. No-op when --output not set."""
    if _activity_log is not None:
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _activity_log.write(f"[{ts}] {msg}\n")
        _activity_log.flush()

# ── Episode detection ──────────────────────────────────────────────────────

# Matches both legacy root cast files and the new configs/{slug}/cast_{tag}.json layout
_TAG_RE = re.compile(r"^cast_(.+?)_([A-Z0-9]+)\.json$")
_NEW_CAST_RE = re.compile(r"^cast_([A-Z0-9]+)\.json$")

RUNNABLE_STAGES = [
    "1) scan",
    "2) parse",
    "3) produce",
    "4) assemble",
    "5) daw",
    "6) master",
]
DRY_RUN_STAGES = {"produce", "daw", "master"}

_STAGE_MODULES = {
    "scan":     "xil_pipeline.XILP000_script_scanner",
    "parse":    "xil_pipeline.XILP001_script_parser",
    "produce":  "xil_pipeline.XILP002_producer",
    "assemble": "xil_pipeline.XILP003_audio_assembly",
    "daw":      "xil_pipeline.XILP005_daw_export",
    "master":   "xil_pipeline.XILP011_master_export",
}

def _stage_key(choice: str) -> str:
    """'3) produce' → 'produce'"""
    return re.sub(r"^\d+\)\s*", "", choice.strip())


def _find_episodes() -> list[tuple[str, str]]:
    """Return [(slug, tag), ...] sorted by slug then tag.

    Shows with a configs/{slug}/project.json but no cast config yet are returned
    as (slug, "") — show-level stubs so they appear in the dropdown before any
    episode has been parsed.
    """
    seen: set[tuple[str, str]] = set()
    results = []
    root = str(get_workspace_root())

    # Legacy root layout: cast_{slug}_{tag}.json
    for path in glob.glob(os.path.join(root, "cast_*.json")):
        m = _TAG_RE.match(os.path.basename(path))
        if m:
            pair = (m.group(1), m.group(2))
            if pair not in seen:
                seen.add(pair)
                results.append(pair)

    # Normalized layout: configs/{slug}/cast_{tag}.json
    for path in glob.glob(os.path.join(root, "configs", "*", "cast_*.json")):
        slug = os.path.basename(os.path.dirname(path))
        m = _NEW_CAST_RE.match(os.path.basename(path))
        if m:
            pair = (slug, m.group(1))
            if pair not in seen:
                seen.add(pair)
                results.append(pair)

    # Show stubs: configs/{slug}/project.json with no cast config yet
    slugs_with_episodes = {s for s, _ in results}
    configs_dir = os.path.join(root, "configs")
    if os.path.isdir(configs_dir):
        for entry in sorted(os.listdir(configs_dir)):
            if entry in slugs_with_episodes:
                continue
            pj = os.path.join(configs_dir, entry, "project.json")
            if os.path.exists(pj):
                results.append((entry, ""))

    results.sort(key=lambda x: (x[0], x[1]))
    return results


def _ep_meta(slug: str, tag: str) -> tuple[str, str]:
    """Return (title, season_title) from the cast config, or ('', '') if not found."""
    root = str(get_workspace_root())
    for path in [
        os.path.join(root, "configs", slug, f"cast_{tag}.json"),
        os.path.join(root, f"cast_{slug}_{tag}.json"),
    ]:
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("title", ""), data.get("season_title", "")
            except Exception:
                pass
    return "", ""


def _ep_choice(slug: str, tag: str) -> str:
    """Format a (slug, tag) pair as a dropdown label string."""
    if not tag:
        # Show stub — no episodes parsed yet; read show name from project.json
        pj = os.path.join(str(get_workspace_root()), "configs", slug, "project.json")
        try:
            with open(pj, encoding="utf-8") as _f:
                show_name = json.load(_f).get("show", slug)
        except Exception:
            show_name = slug
        return f"{slug}  [show]  —  {show_name}"
    title, season_title = _ep_meta(slug, tag)
    label = f"{slug}  {tag}"
    if season_title:
        label += f"  [{season_title}]"
    if title:
        label += f"  —  {title}"
    return label


def _episode_choices() -> list[str]:
    """Return all episode dropdown labels."""
    return [_ep_choice(slug, tag) for slug, tag in _find_episodes()]


def _script_choices() -> list[str]:
    """Return relative paths to all scripts .md files, hierarchical then flat fallback."""
    root = get_workspace_root()
    per_show = sorted(root.glob("scripts/*/*.md"))
    if per_show:
        return [str(p.relative_to(root)) for p in per_show]
    return sorted(glob.glob(os.path.join(str(root), "scripts", "*.md")))


def _default_chatterbox_python() -> str:
    """Return the first existing venv-chatterbox python3, or an empty string."""
    from pathlib import Path
    candidates = [
        get_workspace_root() / "venv-chatterbox" / "bin" / "python3",
        Path(sys.executable).parent.parent.parent / "venv-chatterbox" / "bin" / "python3",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


def _default_audioldm2_python() -> str:
    """Return the venv-audioldm2 python path if it exists, or an empty string."""
    from pathlib import Path
    candidates = [
        get_workspace_root() / "venv-audioldm2" / "bin" / "python",
        Path(sys.executable).parent.parent.parent / "venv-audioldm2" / "bin" / "python",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return ""


def _find_speakers_configs() -> list[str]:
    """Return relative paths to all speakers.json files, sorted by slug."""
    paths: list[str] = []
    root = str(get_workspace_root())
    for p in sorted(glob.glob(os.path.join(root, "configs", "*", "speakers.json"))):
        paths.append(p)
    legacy = os.path.join(root, "speakers.json")
    if os.path.exists(legacy):
        paths.append(legacy)
    return paths


def _find_cast_configs() -> list[str]:
    """Return relative paths to all cast JSON configs, sorted by slug then tag."""
    paths: list[str] = []
    root = str(get_workspace_root())
    # Normalized layout: configs/{slug}/cast_{tag}.json
    for p in sorted(glob.glob(os.path.join(root, "configs", "*", "cast_*.json"))):
        paths.append(p)
    # Legacy root layout: cast_{slug}_{tag}.json
    for p in sorted(glob.glob(os.path.join(root, "cast_*.json"))):
        if _TAG_RE.match(os.path.basename(p)):
            paths.append(p)
    return paths


_LEGACY_SFX_RE = re.compile(r"^sfx_(.+?)_([A-Z0-9]+)\.json$")


def _find_sfx_configs() -> list[str]:
    """Return relative paths to all SFX JSON configs, sorted by slug then tag."""
    paths: list[str] = []
    root = str(get_workspace_root())
    # Normalized layout: configs/{slug}/sfx_{tag}.json
    for p in sorted(glob.glob(os.path.join(root, "configs", "*", "sfx_*.json"))):
        paths.append(p)
    # Legacy root layout: sfx_{slug}_{tag}.json
    for p in sorted(glob.glob(os.path.join(root, "sfx_*.json"))):
        if _LEGACY_SFX_RE.match(os.path.basename(p)):
            paths.append(p)
    return paths


def _analyze_script_header(
    text: str,
) -> tuple[str, str, str, str, str, str]:
    """Parse first non-blank line; return (show, season, episode, title, arc, filename)."""
    from xil_pipeline.models import show_slug
    from xil_pipeline.XILP001_script_parser import parse_script_header

    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not first_line:
        return "", "", "", "", "", ""

    _log_activity("ANALYZE header")
    result = parse_script_header(first_line)
    if result is None:
        return "", "", "", "", "", '⚠️ Header not recognized — expected: SHOW Season N: Episode N: "Title"'

    show, season, episode, title, season_title = result
    slug = show_slug(show)
    safe_title = re.sub(r"[^A-Za-z0-9]+", "_", title).strip("_")
    season_str = f"{season:02d}" if season is not None else "XX"
    filename = f"S{season_str}E{episode:02d}_{slug}_{safe_title}_v1.md"

    return show, str(season) if season is not None else "", str(episode), title, season_title or "", filename


def _save_script_file(text: str, filename: str) -> str:
    """Write script to {workspace_root}/scripts/{slug}/{filename}. Refuses to overwrite."""
    if not text.strip():
        return "⚠️ No script content to save."
    filename = filename.strip()
    if not filename:
        return "⚠️ Filename is empty — run Analyze Header first."
    if not filename.endswith(".md"):
        filename += ".md"

    root = get_workspace_root()
    # Derive slug from filename pattern: S##E##_{slug}_...md or {slug}_...md
    import re as _re
    _slug_match = _re.match(r"^(?:[A-Z]\d+[A-Z]\d+_)?([a-z0-9]+)_", filename)
    _slug = _slug_match.group(1) if _slug_match else ""
    if _slug and (root / "scripts" / _slug).is_dir():
        scripts_dir = root / "scripts" / _slug
        rel = f"scripts/{_slug}/{filename}"
    else:
        scripts_dir = root / "scripts"
        rel = f"scripts/{filename}"

    scripts_dir.mkdir(parents=True, exist_ok=True)
    dest = scripts_dir / filename

    if dest.exists():
        return f"⚠️ Already exists: {rel} — edit the filename above to save a new version."

    dest.write_text(text, encoding="utf-8")
    _log_activity(f"SAVE script → {rel}")
    return f"✅ Saved: {rel}"


_SLUG_TAG_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _is_safe_slug_or_tag(value: str) -> bool:
    """True iff *value* is a bare filesystem-safe path component.

    Show slugs (:func:`models.show_slug`) and episode tags are always
    alphanumeric-with-separators — never a path separator, ``..``, or a
    null byte. Slug/tag strings arriving from GUI callbacks or the
    ``/xil/*`` HTTP routes flow into :func:`derive_paths`, so this
    allowlist must run *before* any path is built — containment checks
    after the fact (:func:`_check_workspace_path`) are defence-in-depth,
    not the primary control.
    """
    return bool(value) and bool(_SLUG_TAG_RE.match(value))


def _parse_choice(choice: str) -> tuple[str, str]:
    """'the413  S03E03  [title]' → ('the413', 'S03E03')
    'mypodcast  [show]  —  My Podcast' → ('mypodcast', '')

    A slug that is not a safe path component (see :func:`_is_safe_slug_or_tag`)
    yields ``("", "")`` — every caller feeds the result into
    :func:`derive_paths`, and callers already handle the empty case.
    """
    parts = choice.strip().split()
    slug = parts[0] if parts else ""
    if slug and not _is_safe_slug_or_tag(slug):
        return "", ""
    if len(parts) >= 2 and re.match(r"^[A-Z][A-Z0-9]+$", parts[1]):
        return slug, parts[1]
    return slug, ""


def _stage_status(slug: str, tag: str) -> dict[str, str]:
    """Return parse/produce/daw/master freshness indicators for an episode.

    Reuses the ``xil status`` engine (:func:`evaluate_episode`) so each cell
    reflects *staleness*, not just file existence:
    ``✓`` up to date · ``⚠`` stale (an input changed since the stage last ran) ·
    ``○`` not built. The ``overall`` key is the worst of the four shown stages.
    """
    from xil_pipeline.XILU019_episode_status import (
        _DEFAULT_GDOC_DIR,
        _MISSING,
        _OK,
        _STALE,
        evaluate_episode,
    )

    glyph = {_OK: "✓", _STALE: "⚠", _MISSING: "○"}
    stages = {s.name: s for s in evaluate_episode(
        slug, tag, Path(_DEFAULT_GDOC_DIR), include_source=False
    )}

    def g(name: str) -> str:
        s = stages.get(name)
        return glyph.get(s.status, "○") if s else "○"

    # Stems: keep the file count alongside the freshness glyph.
    stems = stages.get("stems")
    produce = (
        f"{glyph.get(stems.status, '○')} {stems.output_count}"
        if stems and stems.status != _MISSING
        else "○"
    )

    # Overall = worst across the four displayed stages (MISSING > STALE > OK);
    # source/script are excluded so it never disagrees with a column not shown.
    shown = [stages[n].status for n in ("parsed", "stems", "daw", "master") if n in stages]
    if _MISSING in shown:
        overall = "○ missing"
    elif _STALE in shown:
        overall = "⚠ stale"
    else:
        overall = "✓ OK"

    return {
        "parse":   g("parsed"),
        "produce": produce,
        "daw":     g("daw"),
        "master":  g("master"),
        "overall": overall,
    }


# Rows memoised per workspace root: demo.load fires _refresh_episodes on every
# browser connect, and the staleness scan stats every stem/daw file per episode
# — prohibitive when XIL_PROJECTROOT is a NAS mount. The ⟳ button forces.
_EPISODES_CACHE: dict[str, tuple[float, list[list[str]]]] = {}
_EPISODES_TTL_S = 300.0


def _refresh_episodes(force: bool = False) -> list[list[str]]:
    """Build the Episodes tab table rows from current workspace state."""
    root = str(get_workspace_root())
    if not force:
        hit = _EPISODES_CACHE.get(root)
        if hit is not None and time.monotonic() - hit[0] < _EPISODES_TTL_S:
            return hit[1]

    episodes = list(_find_episodes())

    def _eval_one(slug_tag: tuple[str, str]) -> list[str]:
        slug, tag = slug_tag
        st = _stage_status(slug, tag)
        title, season_title = _ep_meta(slug, tag)
        desc = title
        if season_title:
            desc = f"[{season_title}]  —  {title}" if title else f"[{season_title}]"
        return [tag, slug, desc, st["parse"], st["produce"], st["daw"], st["master"], st["overall"]]

    rows: list[list[str]] = []
    if episodes:
        workers = min(8, len(episodes))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(_eval_one, episodes))
    _EPISODES_CACHE[root] = (time.monotonic(), rows)
    return rows


# ── Stem discovery ─────────────────────────────────────────────────────────

def _load_stems(slug: str, tag: str, filter_type: str = "all") -> list[tuple[str, str]]:
    """Return [(display_label, filepath), ...] sorted by seq."""
    from xil_pipeline.models import derive_paths
    p = derive_paths(slug, tag)
    stems_dir = p["stems"]
    if not os.path.isdir(stems_dir):
        return []

    parsed_path = p["parsed"]
    seq_index: dict[int, dict] = {}
    if os.path.exists(parsed_path):
        with open(parsed_path, encoding="utf-8") as f:
            data = json.load(f)
        for entry in data.get("entries", []):
            seq_index[entry.get("seq", -99999)] = entry

    stems = sorted(glob.glob(os.path.join(stems_dir, "*.mp3")))
    choices = []
    for path in stems:
        basename = os.path.splitext(os.path.basename(path))[0]
        seq_m = re.match(r"^n?(-?\d+)_", basename)
        seq_num = int(seq_m.group(1)) if seq_m else -99999

        entry = seq_index.get(seq_num, {})
        entry_type = entry.get("type", "")
        direction_type = entry.get("direction_type", "")

        if filter_type == "dialogue" and entry_type != "dialogue":
            continue
        if filter_type == "sfx" and direction_type not in ("SFX", "BEAT"):
            continue
        if filter_type == "music" and direction_type != "MUSIC":
            continue
        if filter_type == "ambience" and direction_type != "AMBIENCE":
            continue

        if entry:
            speaker = entry.get("speaker") or direction_type or "?"
            text = (entry.get("text") or "")[:52]
            section = (entry.get("section") or "")[:14]
            label = f"{seq_num:4d}  {speaker:<12}  {section:<14}  {text}"
        else:
            label = basename

        choices.append((label, path))

    return choices


# ── Local audio cache (NAS workspaces) ──────────────────────────────────────
#
# gr.Audio streams whatever path a handler returns; when XIL_PROJECTROOT is a
# NAS mount that means silently-slow network reads. _cached_audio_path copies
# the file into a bounded local cache (chunked, with progress for gr.Progress)
# and returns the local path — keys carry (size, mtime) so an updated source
# is a new entry and nothing ever needs explicit invalidation.

_AUDIO_CACHE_MAX_BYTES = 2 * 1024**3
_AUDIO_COPY_CHUNK = 4 * 1024 * 1024


def _audio_cache_dir() -> str:
    """Local cache dir for workspace audio ($XDG_CACHE_HOME or ~/.cache)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    d = os.path.join(base, "xil-gui", "audio")
    os.makedirs(d, exist_ok=True)
    return d


def _evict_audio_cache(cache_dir: str, keep: str = "") -> None:
    """Delete oldest-mtime cache files until under _AUDIO_CACHE_MAX_BYTES."""
    try:
        entries = []
        with os.scandir(cache_dir) as it:
            for e in it:
                if e.is_file() and e.path != keep:
                    st = e.stat()
                    entries.append((st.st_mtime, st.st_size, e.path))
        total = sum(size for _, size, _ in entries)
        if keep and os.path.exists(keep):
            total += os.path.getsize(keep)
        for _, size, path in sorted(entries):
            if total <= _AUDIO_CACHE_MAX_BYTES:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass
    except OSError:
        pass


def _cached_audio_path(src: str, progress_cb=None) -> str:
    """Copy src into the local audio cache and return the local path.

    progress_cb(done_bytes, total_bytes) fires per copied chunk (a plain
    callable so this module stays gradio-free). Cache hits bump the file's
    mtime (LRU-ish for eviction) and skip the copy. On any OSError the
    original path is returned unchanged — playback degrades to streaming
    straight off the workspace rather than breaking.
    """
    try:
        st = os.stat(src)
        cache_dir = _audio_cache_dir()
        key = hashlib.sha1(
            f"{os.path.abspath(src)}|{st.st_size}|{st.st_mtime_ns}".encode()
        ).hexdigest()
        dest = os.path.join(cache_dir, key + os.path.splitext(src)[1])
        if os.path.exists(dest):
            os.utime(dest)
            return dest
        part = dest + ".part"
        done = 0
        with open(src, "rb") as fin, open(part, "wb") as fout:
            while True:
                chunk = fin.read(_AUDIO_COPY_CHUNK)
                if not chunk:
                    break
                fout.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, st.st_size)
        os.replace(part, dest)
        _evict_audio_cache(cache_dir, keep=dest)
        return dest
    except OSError:
        return src


def _concatenate_stems(ep_choice: str, filter_type: str, progress_cb=None) -> str | None:
    """Concatenate all stems of filter_type for ep_choice into a cached MP3. Returns path.

    The output is keyed by the ordered (path, size, mtime) signature of the
    input stems, so repeat clicks return the existing file without
    re-decoding and a re-produced stem naturally rolls the key over.
    progress_cb(stem_index, stem_count) fires per decoded stem.
    """
    if not ep_choice:
        return None
    _log_activity(f"PLAY {filter_type} → {ep_choice}")
    slug, tag = _parse_choice(ep_choice)
    stems = _load_stems(slug, tag, filter_type=filter_type)
    if not stems:
        return None
    try:
        sig_parts = [filter_type]
        for _, path in stems:
            st = os.stat(path)
            sig_parts.append(f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}")
        key = hashlib.sha1("\n".join(sig_parts).encode()).hexdigest()
        out = os.path.join(_audio_cache_dir(), f"concat_{key}.mp3")
        if os.path.exists(out):
            os.utime(out)
            return out

        from pydub import AudioSegment
        combined = AudioSegment.empty()
        for i, (_, path) in enumerate(stems):
            if progress_cb:
                progress_cb(i, len(stems))
            combined += AudioSegment.from_mp3(_cached_audio_path(path))
        part = out + ".part"
        combined.export(part, format="mp3")
        os.replace(part, out)
        _evict_audio_cache(_audio_cache_dir(), keep=out)
        return out
    except Exception:
        return None


# ── SFX library grading ──────────────────────────────────────────────────────
#
# Grades live in each mp3's ID3 tag (TXXX:XIL_GRADE = "accurate" | "rejected";
# absent = ungraded) — read/written by sfx_common.read_sfx_grade /
# write_sfx_grade so the pipeline (which omits rejected pool files) and this GUI
# share one implementation. _sfx_grade_cache holds {path: grade} so list
# filtering/relabeling is instant; it is (re)built from disk only on demand
# (Load/Refresh), never at app construction — 842 ID3 reads must stay off the
# startup path.

_GRADES = ("accurate", "rejected")
_GRADE_GLYPH = {"accurate": "✓", "rejected": "✗", "": "•"}
_sfx_grade_cache: dict[str, str] = {}


def _sfx_dir(slug: str = "") -> str:
    """Absolute path to the SFX library directory, scoped to slug when the subdir exists."""
    root = get_workspace_root()
    if slug:
        per_show = root / "SFX" / slug
        if per_show.is_dir():
            return str(per_show)
    return str(root / "SFX")


_GRADE_CACHE_VERSION = 1


def _grade_cache_path() -> str:
    """Path of the persisted grade cache, inside the SFX root."""
    return os.path.join(_sfx_dir(), ".xil_grade_cache.json")


def _load_grade_cache_file() -> dict:
    """Return the persisted {rel_path: {grade, size, mtime_ns}} map.

    Missing, corrupt, or version-mismatched files return {} — the next scan
    simply pays the full ID3 pass once and rewrites a good cache.
    """
    try:
        with open(_grade_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") != _GRADE_CACHE_VERSION:
            return {}
        files = data.get("files", {})
        return files if isinstance(files, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_grade_cache_file(files: dict) -> None:
    """Persist the grade map. Best-effort: the cache is a pure accelerator."""
    try:
        with open(_grade_cache_path(), "w", encoding="utf-8") as f:
            json.dump({"version": _GRADE_CACHE_VERSION, "files": files}, f, indent=2)
            f.write("\n")
    except OSError:
        pass


def _scan_sfx_grades() -> dict[str, str]:
    """Rebuild _sfx_grade_cache from disk for every SFX/*.mp3.

    Recurses into per-show subdirectories (the ``SFX/{slug}/`` hierarchical
    layout) as well as the flat shared pool, so both are found. Returns the
    cache.

    Grades are memoised in SFX/.xil_grade_cache.json keyed by (size, mtime):
    only new/changed files pay an ID3 read, so a rescan is one directory walk
    plus stats instead of ~842 file opens — prohibitive on a NAS workspace.
    Paths in the file are relative to the SFX root so the cache survives
    mount-point moves.
    """
    _sfx_grade_cache.clear()
    sfx_dir = _sfx_dir()
    if not os.path.isdir(sfx_dir):
        return _sfx_grade_cache
    persisted = _load_grade_cache_file()
    fresh: dict[str, dict] = {}
    changed = False
    pattern = os.path.join(sfx_dir, "**", "*.mp3")
    for path in sorted(glob.glob(pattern, recursive=True)):
        try:
            st = os.stat(path)
        except OSError:
            continue
        rel = os.path.relpath(path, sfx_dir)
        rec = persisted.get(rel)
        if rec and rec.get("size") == st.st_size and rec.get("mtime_ns") == st.st_mtime_ns:
            grade = rec.get("grade", "")
        else:
            grade = _read_sfx_grade(path)
            changed = True
        fresh[rel] = {"grade": grade, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        _sfx_grade_cache[path] = grade
    if changed or set(fresh) != set(persisted):
        _save_grade_cache_file(fresh)
    return _sfx_grade_cache


def _update_grade_cache_entry(path: str, grade: str) -> None:
    """Patch one file's record in the persisted grade cache after a grade write.

    The ID3 write changed the mp3's size/mtime; storing the post-write stat
    keeps the next scan read-free. Best-effort: failure is silent and the next
    scan self-heals.
    """
    try:
        st = os.stat(path)
        rel = os.path.relpath(path, _sfx_dir())
        files = _load_grade_cache_file()
        files[rel] = {
            "grade": grade if grade in _GRADES else "",
            "size": st.st_size,
            "mtime_ns": st.st_mtime_ns,
        }
        _save_grade_cache_file(files)
    except OSError:
        pass


def _sfx_show_label(path: str, root: str) -> str:
    """Return the per-show subdirectory name for *path* under *root*.

    Returns "" for files directly in *root* (the flat shared pool) and for
    any path that isn't actually nested under *root* (defensive — cache
    entries seeded in tests with synthetic paths must not produce a
    spurious ``[..]`` label).
    """
    try:
        rel_dir = os.path.relpath(os.path.dirname(path), root)
    except ValueError:
        return ""
    if rel_dir in (".", "") or rel_dir.startswith(os.pardir):
        return ""
    return rel_dir.split(os.sep)[0]


def _sfx_choices(grade_filter: str = "all") -> list[tuple[str, str]]:
    """Return [(glyph + filename, path), ...] from the cache, filtered by grade.

    Files from a per-show subdirectory are prefixed with ``[show]`` since
    two shows can legitimately use the same filename (e.g. ``beat.mp3``).
    """
    root = _sfx_dir()
    out: list[tuple[str, str]] = []
    for path, grade in sorted(_sfx_grade_cache.items()):
        if grade_filter == "ungraded" and grade:
            continue
        if grade_filter in _GRADES and grade != grade_filter:
            continue
        show = _sfx_show_label(path, root)
        name = f"[{show}] {os.path.basename(path)}" if show else os.path.basename(path)
        label = f"{_GRADE_GLYPH.get(grade, '•')}  {name}"
        out.append((label, path))
    return out


def _sfx_summary() -> str:
    """One-line count summary of the current grade cache."""
    total = len(_sfx_grade_cache)
    if total == 0:
        return "No SFX files found (click Load to scan SFX/)."
    acc = sum(1 for g in _sfx_grade_cache.values() if g == "accurate")
    rej = sum(1 for g in _sfx_grade_cache.values() if g == "rejected")
    ung = total - acc - rej
    return f"{total} files — {acc} ✓ accurate · {rej} ✗ rejected · {ung} • ungraded"


# ── Stage runner ───────────────────────────────────────────────────────────

# Characters that have special meaning to a Unix shell; reject any token
# containing them so user-supplied extra_flags cannot escape the subprocess
# argument list or chain additional commands.
_SHELL_UNSAFE_RE = re.compile(r'[;|&$`()\[\]<>!\\\n\r]')


def _sanitize_extra_flags(flags: str) -> list[str]:
    """Parse and validate extra CLI flags supplied by the GUI user.

    Uses shlex.split so quoted paths-with-spaces work correctly.
    Raises ValueError if the input contains shell metacharacters or
    unbalanced quotes.
    """
    try:
        tokens = shlex.split(flags.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid flag syntax: {exc}") from exc
    for tok in tokens:
        if _SHELL_UNSAFE_RE.search(tok):
            raise ValueError(f"Unsafe character in flag argument: {tok!r}")
    return tokens


def _check_workspace_path(path: str) -> None:
    """Raise ValueError if *path* resolves outside the workspace root.

    Config-editor save functions receive paths from Gradio dropdowns that are
    populated from the workspace file tree, so this is a defence-in-depth
    check rather than the primary trust boundary.
    """
    workspace = get_workspace_root().resolve()
    try:
        Path(path).resolve().relative_to(workspace)
    except ValueError as exc:
        raise ValueError(f"Path is outside the workspace root: {path!r}") from exc


def _timeline_iframe_html(html_path: str) -> str:
    """Build the iframe embed for a timeline HTML file.

    The file mtime is appended as a ``?v=`` query param so a regenerated
    timeline always gets a fresh URL — without it, the browser serves its
    cached copy of the old file because the path alone never changes.
    """
    abs_path = os.path.abspath(html_path)
    v = int(os.path.getmtime(html_path))
    return (
        f'<iframe src="/gradio_api/file={abs_path}?v={v}" '
        f'style="width:100%;height:600px;border:none;"></iframe>'
    )


def _load_config_file(path: str, label: str) -> str:
    """Read a JSON config for the editor textboxes, workspace-bounded.

    The path arrives from a Gradio dropdown, but callbacks are HTTP
    endpoints — the value is client-suppliable, so the same
    :func:`_check_workspace_path` boundary the save functions enforce
    must apply on the read side too.
    """
    if not path:
        return ""
    try:
        _check_workspace_path(path)
    except ValueError as exc:
        return f"// {exc}"
    _log_activity(f"SELECT {label} → {path}")
    if not os.path.exists(path):
        return f"// File not found: {path}"
    with open(path, encoding="utf-8") as f:
        return f.read()


def load_cast_config(path: str) -> str:
    return _load_config_file(path, "cast")


def load_speakers_config(path: str) -> str:
    return _load_config_file(path, "speakers")


def load_sfx_config(path: str) -> str:
    return _load_config_file(path, "sfx")


def _run_stage(episode_choice: str, stage: str, dry_run: bool, extra_flags: str):
    """Generator: launch a pipeline stage, yield accumulated stdout."""
    if not episode_choice or not stage:
        yield "Select an episode and stage first."
        return

    slug, tag = _parse_choice(episode_choice)
    if not tag:
        yield f"Could not parse episode selection: {episode_choice!r}"
        return

    key = _stage_key(stage)
    module = _STAGE_MODULES.get(key)
    if not module:
        yield f"Unknown stage: {stage!r}"
        return

    if key == "scan":
        # scan takes a positional script path — put it in extra_flags
        if not extra_flags.strip():
            yield "scan requires a script path in Extra flags (e.g. scripts/sample_S01E01.md)"
            return
        cmd = [sys.executable, "-m", module, "--show", slug]
    else:
        cmd = [sys.executable, "-m", module, "--episode", tag]
    if dry_run and key in DRY_RUN_STAGES:
        cmd.append("--dry-run")
    if extra_flags.strip():
        try:
            cmd.extend(_sanitize_extra_flags(extra_flags))
        except ValueError as exc:
            yield f"Error in Extra flags: {exc}"
            return

    header = "$ " + " ".join(cmd) + "\n\n"
    _log_activity("CMD: " + " ".join(cmd))
    yield header

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(get_workspace_root()),
        )
        output = header
        for line in iter(proc.stdout.readline, ""):
            _log_activity(line.rstrip("\n"))
            output += line
            yield output
        proc.wait()
        _log_activity(f"[exit {proc.returncode}]")
        output += f"\n[exit {proc.returncode}]"
        yield output
    except Exception as exc:
        _log_activity(f"[ERROR] {exc}")
        yield f"{header}\nError: {exc}"


def _execute_cmd(cmd: list[str]):
    """Generator: run cmd, yield accumulated stdout to a log box."""
    header = "$ " + " ".join(cmd) + "\n\n"
    _log_activity("CMD: " + " ".join(cmd))
    yield header
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(get_workspace_root()),
        )
        buf = header
        for line in iter(proc.stdout.readline, ""):
            _log_activity(line.rstrip("\n"))
            buf += line
            yield buf
        proc.wait()
        _log_activity(f"[exit {proc.returncode}]")
        buf += f"\n[exit {proc.returncode}]"
        yield buf
    except Exception as exc:
        _log_activity(f"[ERROR] {exc}")
        yield header + f"\n[ERROR] {exc}\n"


def _cmd_scan(slug: str, tag: str, script_path: str | None, speakers: str, as_json: bool) -> list[str]:
    """Build the xil-scan command list."""
    if not script_path or not str(script_path).strip():
        raise ValueError("Scan requires a script — select one from the dropdown.")
    module = _STAGE_MODULES["scan"]
    cmd = [sys.executable, "-m", module, "--show", slug, str(script_path).strip()]
    if speakers.strip():
        cmd += ["--speakers", speakers.strip()]
    if as_json:
        cmd.append("--json")
    return cmd


def _cmd_parse(slug: str, tag: str, script_path: str | None, preview: int | None,
               quiet: bool, debug: bool, stats: bool, speakers: str) -> list[str]:
    """Build the xil-parse command list."""
    module = _STAGE_MODULES["parse"]
    if script_path and str(script_path).strip():
        cmd = [sys.executable, "-m", module, str(script_path).strip(), "--episode", tag]
    else:
        import glob as _glob
        root = get_workspace_root()
        # Prefer per-show subdir when the slug subdir exists
        candidates = sorted(_glob.glob(os.path.join(str(root), "scripts", slug, "*.md"))) if slug else []
        if not candidates:
            candidates = sorted(_glob.glob(os.path.join(str(root), "scripts", "*.md")))
        if not candidates:
            raise ValueError("No script path given and no .md files found in scripts/")
        script = candidates[0]
        cmd = [sys.executable, "-m", module, script, "--episode", tag]
    if preview is not None and preview > 0:
        cmd += ["--preview", str(int(preview))]
    if quiet:
        cmd.append("--quiet")
    if debug:
        cmd.append("--debug")
    if stats:
        cmd.append("--stats")
    if speakers.strip():
        cmd += ["--speakers", speakers.strip()]
    return cmd


def _cmd_produce(slug: str, tag: str, dry_run: bool, backend: str,
                 gen_sfx: bool, gen_music: bool, gen_ambience: bool,
                 local_only: bool, terse: bool,
                 start_from: int | None, stop_at: int | None,
                 exaggeration: float, cb_python: str = "",
                 force: bool = False, cfg_weight: float = 0.5,
                 sfx_backend: str = "elevenlabs", adl2_python: str = "",
                 adl2_guidance: float = 3.5, adl2_steps: int = 200,
                 adl2_neg_prompt: str = "low quality, noise") -> list[str]:
    """Build the xil-produce command list."""
    module = _STAGE_MODULES["produce"]
    cmd = [sys.executable, "-m", module, "--episode", tag]
    if dry_run:
        cmd.append("--dry-run")
    if backend and backend != "elevenlabs":
        cmd += ["--backend", backend]
    if gen_sfx:
        cmd.append("--gen-sfx")
    if gen_music:
        cmd.append("--gen-music")
    if gen_ambience:
        cmd.append("--gen-ambience")
    if local_only:
        cmd.append("--local-only")
    if terse:
        cmd.append("--terse")
    if start_from is not None and start_from > 0:
        cmd += ["--start-from", str(int(start_from))]
    if stop_at is not None and stop_at > 0:
        cmd += ["--stop-at", str(int(stop_at))]
    if backend in ("chatterbox", "chatterbox-turbo"):
        # exaggeration/cfg-weight apply only to classic Chatterbox; Turbo ignores them.
        if backend == "chatterbox":
            if exaggeration != 0.5:
                cmd += ["--exaggeration", f"{exaggeration:.2f}"]
            if cfg_weight != 0.5:
                cmd += ["--cfg-weight", f"{cfg_weight:.2f}"]
        if cb_python and cb_python.strip():
            cmd += ["--chatterbox-python", cb_python.strip()]
    if sfx_backend and sfx_backend != "elevenlabs":
        cmd += ["--sfx-backend", sfx_backend]
    if sfx_backend == "audioldm2":
        if adl2_python and adl2_python.strip():
            cmd += ["--audioldm2-python", adl2_python.strip()]
        if adl2_guidance != 3.5:
            cmd += ["--audioldm2-guidance", f"{adl2_guidance:.1f}"]
        if adl2_steps != 200:
            cmd += ["--audioldm2-steps", str(int(adl2_steps))]
        if adl2_neg_prompt and adl2_neg_prompt.strip() != "low quality, noise":
            cmd += ["--audioldm2-negative-prompt", adl2_neg_prompt.strip()]
    if force:
        cmd.append("--force")
    return cmd


def _cmd_assemble(slug: str, tag: str, gap_ms: int,
                  parsed_path: str, output: str) -> list[str]:
    """Build the xil-assemble command list."""
    module = _STAGE_MODULES["assemble"]
    cmd = [sys.executable, "-m", module, "--episode", tag]
    if gap_ms != 600:
        cmd += ["--gap-ms", str(int(gap_ms))]
    if parsed_path.strip():
        cmd += ["--parsed", parsed_path.strip()]
    if output.strip():
        cmd += ["--output", output.strip()]
    return cmd


def _cmd_daw(slug: str, tag: str, dry_run: bool, gap_ms: int,
             timeline: bool, timeline_html: bool,
             macro: bool, save_aup3: bool, output_dir: str) -> list[str]:
    """Build the xil-daw command list."""
    module = _STAGE_MODULES["daw"]
    cmd = [sys.executable, "-m", module, "--episode", tag]
    if dry_run:
        cmd.append("--dry-run")
    if gap_ms != 600:
        cmd += ["--gap-ms", str(int(gap_ms))]
    if timeline:
        cmd.append("--timeline")
    if timeline_html:
        cmd.append("--timeline-html")
    if macro:
        cmd.append("--macro")
    if save_aup3:
        cmd.append("--save-aup3")
    if output_dir.strip():
        cmd += ["--output-dir", output_dir.strip()]
    return cmd


def _cmd_master(slug: str, tag: str, dry_run: bool,
                output: str, daw_dir: str) -> list[str]:
    """Build the xil-master command list."""
    module = _STAGE_MODULES["master"]
    cmd = [sys.executable, "-m", module, "--episode", tag]
    if dry_run:
        cmd.append("--dry-run")
    if output.strip():
        cmd += ["--output", output.strip()]
    if daw_dir.strip():
        cmd += ["--daw-dir", daw_dir.strip()]
    return cmd


# ── Gradio app ─────────────────────────────────────────────────────────────

def _build_app():
    """Build and return the Gradio dashboard application."""
    try:
        import gradio as gr
    except ImportError:
        raise SystemExit(
            "Gradio is not installed.\nRun: pip install 'xil-pipeline[gui]'"
        )

    workspace = str(get_workspace_root())
    ep_choices = _episode_choices()

    # Pre-load stem list for the first episode at startup
    initial_stems: list[tuple[str, str]] = []
    if ep_choices:
        slug0, tag0 = _parse_choice(ep_choices[0])
        initial_stems = _load_stems(slug0, tag0, "all")
    initial_stem_labels = [lbl for lbl, _ in initial_stems]

    # ── callback helpers ──────────────────────────────────────────────────

    def _copy_progress(progress, label):
        """Adapt gr.Progress to _cached_audio_path's (done, total) callback."""
        def cb(done, total):
            frac = (done / total) if total else None
            progress(frac, desc=f"Copying {label}… {done >> 20} / {total >> 20} MB")
        return cb

    def on_ep_or_filter_change(choice, filter_type):
        if not choice:
            return gr.update(choices=[], value=None), gr.update(value=None)
        _log_activity(f"PREVIEW episode → {choice} [{filter_type}]")
        slug, tag = _parse_choice(choice)
        stems = _load_stems(slug, tag, filter_type)
        labels = [lbl for lbl, _ in stems]
        return (
            gr.update(choices=labels, value=labels[0] if labels else None),
            gr.update(value=None),
        )

    def on_stem_select(episode_choice, stem_label, filter_type, progress=gr.Progress()):
        if not episode_choice or not stem_label:
            return gr.update(value=None)
        _log_activity(f"PREVIEW stem → {stem_label}")
        slug, tag = _parse_choice(episode_choice)
        for lbl, path in _load_stems(slug, tag, filter_type):
            if lbl == stem_label:
                local = _cached_audio_path(
                    path, _copy_progress(progress, os.path.basename(path)))
                return gr.update(value=local)
        return gr.update(value=None)

    def on_timeline_ep_change(choice):
        if not choice:
            return "<p>Select an episode above.</p>"
        from xil_pipeline.models import derive_paths
        slug, tag = _parse_choice(choice)
        p = derive_paths(slug, tag)
        daw_dir = p["daw"]
        html_path = os.path.join(daw_dir, f"{tag}_timeline.html")
        if not os.path.exists(html_path):
            return (
                f"<p>No timeline found for <b>{tag}</b>.<br>"
                f"Generate it first:<br>"
                f"<code>xil daw --episode {tag} --timeline-html</code></p>"
            )
        return _timeline_iframe_html(html_path)

    def refresh_all():
        new_choices = _episode_choices()
        rows = _refresh_episodes(force=True)
        return (
            rows,
            gr.update(choices=new_choices),
            gr.update(choices=new_choices),
            gr.update(choices=new_choices),
        )

    def _list_available_shows() -> list[str]:
        """Return show names found in configs/*/project.json."""
        import json as _json
        configs_dir = os.path.join(workspace, "configs")
        results = []
        if os.path.isdir(configs_dir):
            for entry_name in sorted(os.listdir(configs_dir)):
                entry = os.path.join(configs_dir, entry_name)
                pj = os.path.join(entry, "project.json")
                if os.path.isdir(entry) and os.path.exists(pj):
                    try:
                        with open(pj, encoding="utf-8") as _f:
                            data = _json.load(_f)
                        results.append(data.get("show", entry_name))
                    except Exception:
                        results.append(entry_name)
        return results

    def run_use_show(show_name: str) -> str:
        """Set .active_show to the selected show and return status."""
        if not show_name:
            return "No show selected."
        _log_activity(f"USE show → {show_name}")
        cmd = [sys.executable, "-m", "xil_pipeline.xil_use", show_name]
        try:
            import subprocess as _sp
            result = _sp.run(cmd, capture_output=True, text=True, cwd=workspace)
            out = (result.stdout + result.stderr).strip()
            return out if out else f"Active show set to: {show_name}"
        except Exception as exc:
            return f"Error: {exc}"

    def run_init(show_name: str, content_type: str, season: str, season_title: str):
        if not show_name.strip():
            yield "Show name is required."
            return
        cmd = [sys.executable, "-m", "xil_pipeline.xil_init",
               "--show", show_name.strip(), "--type", content_type, "--flat"]
        # Sample scripts are always named e.g. sample_S01E01.md (SAMPLE_TAG_BY_TYPE),
        # which bakes in "season 1" — default to it here so the generated header's
        # season declaration (or lack of one) doesn't disagree with that filename.
        cmd += ["--season", season.strip() if season.strip() else "1"]
        if season_title.strip():
            cmd += ["--season-title", season_title.strip()]
        _log_activity("CMD: " + " ".join(cmd))
        yield "$ " + " ".join(cmd) + "\n\n"
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=workspace,
            )
            output = "$ " + " ".join(cmd) + "\n\n"
            for line in iter(proc.stdout.readline, ""):
                _log_activity(line.rstrip("\n"))
                output += line
                yield output
            proc.wait()
            _log_activity(f"[exit {proc.returncode}]")
            output += f"\n[exit {proc.returncode}]"
            yield output
        except Exception as exc:
            _log_activity(f"[ERROR] {exc}")
            yield f"Error: {exc}"

    def _get_project_json_path() -> str:
        from xil_pipeline.models import get_active_show as _gas
        slug = _gas()
        if slug:
            candidate = os.path.join(workspace, "configs", slug, "project.json")
            if os.path.exists(candidate):
                return candidate
        return os.path.join(workspace, "project.json")

    def load_project_json() -> tuple[str, str]:
        path = _get_project_json_path()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read(), path
        return json.dumps({"show": "", "season": 1}, indent=2), path

    def save_project_json(text: str) -> str:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON — not saved: {exc}"
        path = _get_project_json_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        _log_activity(f"SAVE project.json → {path}")
        return f"Saved {path}"

    def cast_config_choices() -> list[str]:
        return _find_cast_configs()

    def save_cast_config(path: str, text: str) -> str:
        if not path:
            return "No file selected."
        try:
            _check_workspace_path(path)
        except ValueError as exc:
            return str(exc)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON — not saved: {exc}"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        _log_activity(f"SAVE cast config → {path}")
        return f"Saved {path}"

    def speakers_config_choices() -> list[str]:
        return _find_speakers_configs()

    def save_speakers_config(path: str, text: str) -> str:
        if not path:
            return "No file selected."
        try:
            _check_workspace_path(path)
        except ValueError as exc:
            return str(exc)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON — not saved: {exc}"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        _log_activity(f"SAVE speakers.json → {path}")
        return f"Saved {path}"

    def sfx_config_choices() -> list[str]:
        return _find_sfx_configs()

    def save_sfx_config(path: str, text: str) -> str:
        if not path:
            return "No file selected."
        try:
            _check_workspace_path(path)
        except ValueError as exc:
            return str(exc)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON — not saved: {exc}"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        _log_activity(f"SAVE sfx config → {path}")
        return f"Saved {path}"

    # ── layout ────────────────────────────────────────────────────────────

    with gr.Blocks(title="xil-pipeline", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# xil-pipeline")
        gr.Markdown(f"**Workspace:** `{workspace}`")

        with gr.Row():
            refresh_btn = gr.Button("⟳ Refresh", size="sm", scale=0)

        with gr.Tabs(elem_id="app-root"):

            # ── Tab 0: Setup ─────────────────────────────────────────
            with gr.Tab("Setup"):
                gr.Markdown("### Active show")
                gr.Markdown(
                    "Select which show is active. All pipeline commands will use this show's "
                    "`configs/{slug}/project.json` to resolve the show slug and season defaults."
                )
                with gr.Row():
                    from xil_pipeline.models import get_active_show as _get_active_show
                    from xil_pipeline.models import show_slug as _show_slug
                    _init_shows = _list_available_shows()
                    _active_name = next(
                        (name for name in _init_shows
                         if _show_slug(name) == _get_active_show()),
                        None,
                    )
                    use_show_dd = gr.Dropdown(
                        label="Show",
                        choices=_init_shows,
                        value=_active_name,
                        scale=4,
                    )
                    use_show_btn = gr.Button("▶ Use this show", variant="primary", scale=1)
                use_show_status = gr.Textbox(
                    label="Status", lines=1, interactive=False,
                )
                # use_show_btn click is wired after the Project tab so it can
                # update proj_editor and proj_path_display as well.

                gr.Markdown("---")
                gr.Markdown("### Initialize a new show")
                gr.Markdown(
                    f"Creates `configs/{{slug}}/project.json`, `speakers.json`, a type-specific "
                    f"sample script, and per-show subdirectories inside `{workspace}`."
                )
                with gr.Row():
                    init_show = gr.Textbox(
                        label="Show name *", placeholder='e.g. "Night Owls"', scale=3,
                        elem_id="init-show-name",
                    )
                    init_type = gr.Dropdown(
                        label="Content type",
                        choices=["podcast", "audiobook", "drama", "special"],
                        value="podcast",
                        scale=1,
                    )
                with gr.Row():
                    init_season = gr.Textbox(
                        label="Season number (optional)", placeholder="e.g. 1", scale=1,
                    )
                    init_season_title = gr.Textbox(
                        label="Season title (optional)",
                        placeholder='e.g. "The Holiday Shift"',
                        scale=3,
                    )
                init_btn = gr.Button("▶ Create show", variant="primary", elem_id="init-create-btn")
                init_log = gr.Textbox(
                    label="Output", lines=12, max_lines=12, autoscroll=True, interactive=False,
                    elem_id="init-log",
                )

                def run_init_and_refresh(show_name, content_type, season, season_title):
                    output = ""
                    for chunk in run_init(show_name, content_type, season, season_title):
                        output = chunk
                        yield output, gr.update()
                    new_shows = _list_available_shows()
                    from xil_pipeline.models import get_active_show as _gas
                    from xil_pipeline.models import show_slug as _ss
                    new_active = next((n for n in new_shows if _ss(n) == _gas()), None)
                    yield output, gr.update(choices=new_shows, value=new_active)

                _init_click = init_btn.click(
                    fn=run_init_and_refresh,
                    inputs=[init_show, init_type, init_season, init_season_title],
                    outputs=[init_log, use_show_dd],
                    api_name="setup_create_show",
                )

            # ── Tab 1: Project ───────────────────────────────────────
            with gr.Tab("Project"):
                _init_proj_content, _init_proj_path = load_project_json()
                proj_path_display = gr.Textbox(
                    value=_init_proj_path, label="File", interactive=False, lines=1,
                )
                proj_editor = gr.Code(
                    value=_init_proj_content,
                    language="json",
                    lines=20,
                    label="project.json",
                )
                with gr.Row():
                    proj_reload_btn = gr.Button("↺ Reload", size="sm", scale=0)
                    proj_save_btn = gr.Button("💾 Save", variant="primary", size="sm", scale=0)
                proj_status = gr.Textbox(label="Status", lines=1, interactive=False)
                proj_reload_btn.click(fn=load_project_json, inputs=[], outputs=[proj_editor, proj_path_display])
                proj_save_btn.click(fn=save_project_json, inputs=proj_editor, outputs=proj_status)

            # ── Tab 2: Episodes ─────────────────────────────────────
            with gr.Tab("Episodes"):
                # Populated by demo.load() on first connect — see below. The
                # staleness scan stats files across every episode, so we keep it
                # off the app-construction path and show a placeholder meanwhile.
                ep_table = gr.Dataframe(
                    headers=[
                        "Tag", "Slug", "Title  [Arc]",
                        "Parse", "Stems", "DAW", "Master", "Overall",
                    ],
                    value=[["", "", "⏳ Collecting episode status — please wait…",
                            "", "", "", "", ""]],
                    interactive=False,
                    wrap=True,
                )
                gr.Markdown(
                    "✓ up to date · ⚠ stale (re-run needed) · ○ not built"
                )

            # ── Tab 3: Run Stage (Scripts → Scan → Parse → Produce → Assemble → DAW → Master)
            with gr.Tab("Run Stage"):
                gr.Markdown(
                    "Run pipeline stages against an episode. "
                    "**Dry-run is on by default** — uncheck to write output files."
                )
                with gr.Row():
                    run_ep_dd = gr.Dropdown(label="Episode", choices=ep_choices, scale=3)
                    run_ep_refresh_btn = gr.Button("⟳", size="sm", scale=0)

                with gr.Tabs():
                    # ── Scripts ───────────────────────────────────────
                    with gr.Tab("Scripts"):
                        script_editor = gr.Textbox(
                            lines=28,
                            label="Script Markdown",
                            placeholder="Paste production script here…",
                            show_copy_button=True,
                        )
                        analyze_btn = gr.Button("Analyze Header", variant="secondary")
                        gr.Markdown("**Derived metadata**")
                        with gr.Row():
                            script_show = gr.Textbox(label="Show", interactive=False, scale=2)
                            script_season = gr.Textbox(label="Season", interactive=False, scale=1)
                            script_episode = gr.Textbox(label="Episode", interactive=False, scale=1)
                        with gr.Row():
                            script_title = gr.Textbox(label="Title", interactive=False, scale=3)
                            script_arc = gr.Textbox(label="Arc / Season Title", interactive=False, scale=3)
                        script_filename = gr.Textbox(label="Filename (editable)", interactive=True)
                        script_save_btn = gr.Button("💾 Save to scripts/", variant="primary")
                        script_status = gr.Textbox(label="Status", lines=1, interactive=False)

                        script_editor.change(
                            fn=lambda t: gr.update(variant="primary" if t.strip() else "secondary"),
                            inputs=[script_editor],
                            outputs=[analyze_btn],
                        )
                        analyze_btn.click(
                            fn=_analyze_script_header,
                            inputs=[script_editor],
                            outputs=[script_show, script_season, script_episode, script_title, script_arc, script_filename],
                        )

                    # ── Scan ──────────────────────────────────────────
                    with gr.Tab("Scan"):
                        with gr.Row():
                            scan_script = gr.Dropdown(
                                label="Script *",
                                choices=_script_choices(),
                                allow_custom_value=True,
                                scale=3,
                            )
                            scan_refresh_btn = gr.Button("⟳", size="sm", scale=0)
                            scan_speakers = gr.Textbox(
                                label="--speakers (optional override)",
                                placeholder="configs/the413/speakers.json",
                                scale=2,
                            )
                        scan_json_cb = gr.Checkbox(label="--json  (machine-readable output)")
                        scan_btn = gr.Button("▶ Run Scan", variant="primary")

                    # ── Parse ─────────────────────────────────────────
                    with gr.Tab("Parse"):
                        with gr.Row():
                            parse_ep_dd = gr.Dropdown(
                                label="Episode (select existing or type new tag, e.g. S04E04)",
                                choices=ep_choices,
                                allow_custom_value=True,
                                scale=3,
                                elem_id="parse-episode",
                            )
                            parse_ep_refresh_btn = gr.Button("⟳", size="sm", scale=0)
                        with gr.Row():
                            parse_script = gr.Dropdown(
                                label="Script (blank = auto-detect first .md in scripts/)",
                                choices=_script_choices(),
                                allow_custom_value=True,
                                scale=3,
                            )
                            parse_refresh_btn = gr.Button("⟳", size="sm", scale=0)
                            parse_speakers = gr.Textbox(
                                label="--speakers (optional override)",
                                placeholder="configs/the413/speakers.json",
                                scale=2,
                            )
                        with gr.Row():
                            parse_preview = gr.Number(
                                label="--preview  (show first N entries, 0 = all)",
                                value=0, minimum=0, precision=0,
                            )
                            parse_quiet_cb = gr.Checkbox(label="--quiet  (JSON only, skip summary)")
                            parse_debug_cb = gr.Checkbox(label="--debug  (write diagnostic CSV)", value=True)
                            parse_stats_cb = gr.Checkbox(label="--stats  (per-speaker line/word/char distribution)")
                        parse_btn = gr.Button("▶ Run Parse", variant="primary", elem_id="parse-run-btn")

                    # ── Produce ───────────────────────────────────────
                    with gr.Tab("Produce"):
                        with gr.Row():
                            prod_dry_run_cb = gr.Checkbox(label="--dry-run", value=True)
                            prod_backend_dd = gr.Dropdown(
                                label="--backend  (dialogue voice generator)",
                                choices=["elevenlabs", "gtts", "chatterbox", "chatterbox-turbo"],
                                value="chatterbox",
                            )
                        with gr.Row():
                            prod_gen_sfx_cb    = gr.Checkbox(label="--gen-sfx")
                            prod_gen_music_cb  = gr.Checkbox(label="--gen-music")
                            prod_gen_amb_cb    = gr.Checkbox(label="--gen-ambience")
                            prod_local_only_cb = gr.Checkbox(label="--local-only", value=True)
                            prod_terse_cb      = gr.Checkbox(label="--terse")
                        prod_sfx_backend_dd = gr.Dropdown(
                            label="--sfx-backend  (SFX / music / ambience generator)",
                            choices=["elevenlabs", "audioldm2"],
                            value="elevenlabs",
                        )
                        with gr.Row():
                            prod_start_from = gr.Number(
                                label="--start-from  (seq, 0 = beginning)",
                                value=0, minimum=0, precision=0,
                            )
                            prod_stop_at = gr.Number(
                                label="--stop-at  (seq, 0 = all)",
                                value=0, minimum=0, precision=0,
                            )
                        prod_exaggeration = gr.Slider(
                            label="--exaggeration  (classic Chatterbox only, ignored by Turbo, 0.0–1.0)",
                            minimum=0.0, maximum=1.0, step=0.05, value=0.5,
                        )
                        prod_cfg_weight = gr.Slider(
                            label="--cfg-weight  (classic Chatterbox only, ignored by Turbo, 0.1–1.0)",
                            minimum=0.1, maximum=1.0, step=0.05, value=0.5,
                        )
                        prod_cb_python = gr.Textbox(
                            label="--chatterbox-python  (blank = auto-detect venv-chatterbox/)",
                            placeholder=_default_chatterbox_python(),
                        )
                        prod_adl2_python = gr.Textbox(
                            label="--audioldm2-python  (blank = auto-detect venv-audioldm2/)",
                            placeholder=_default_audioldm2_python(),
                        )
                        with gr.Row():
                            prod_adl2_guidance = gr.Number(
                                label="--audioldm2-guidance  (default: 3.5)",
                                value=3.5, minimum=1.0, maximum=10.0, step=0.5,
                            )
                            prod_adl2_steps = gr.Number(
                                label="--audioldm2-steps  (default: 200)",
                                value=200, minimum=10, maximum=1000, step=10, precision=0,
                            )
                        prod_adl2_neg_prompt = gr.Textbox(
                            label="--audioldm2-negative-prompt",
                            value="low quality, noise",
                        )
                        with gr.Row():
                            prod_force_cb = gr.Checkbox(
                                label="--force  ⚠️ overwrite existing stems (API cost!)",
                                value=False,
                            )
                        prod_btn = gr.Button("▶ Run Produce", variant="primary")

                    # ── Assemble ──────────────────────────────────────
                    with gr.Tab("Assemble"):
                        with gr.Row():
                            asm_gap_ms = gr.Number(
                                label="--gap-ms  (silence between stems, ms)",
                                value=600, minimum=0, precision=0,
                            )
                        with gr.Row():
                            asm_parsed = gr.Textbox(
                                label="--parsed  (override parsed JSON path, blank = auto)",
                                placeholder="parsed/the413/parsed_S01E01.json",
                                scale=2,
                            )
                            asm_output = gr.Textbox(
                                label="--output  (override master MP3 path, blank = auto)",
                                placeholder="masters/S01E01_the413_master.mp3",
                                scale=2,
                            )
                        asm_btn = gr.Button("▶ Run Assemble", variant="primary")

                    # ── DAW ───────────────────────────────────────────
                    with gr.Tab("DAW"):
                        with gr.Row():
                            daw_dry_run_cb = gr.Checkbox(label="--dry-run", value=True)
                            daw_gap_ms = gr.Number(
                                label="--gap-ms  (ms)",
                                value=600, minimum=0, precision=0,
                            )
                        with gr.Row():
                            daw_timeline_cb      = gr.Checkbox(label="--timeline  (ASCII)")
                            daw_timeline_html_cb = gr.Checkbox(label="--timeline-html", value=True)
                            daw_macro_cb         = gr.Checkbox(label="--macro  (Audacity)", value=True)
                        daw_output_dir = gr.Textbox(
                            label="--output-dir  (blank = auto)",
                            placeholder="daw/S01E01/",
                        )
                        daw_btn = gr.Button("▶ Run DAW", variant="primary")

                    # ── Master ────────────────────────────────────────
                    with gr.Tab("Master"):
                        master_dry_run_cb = gr.Checkbox(label="--dry-run", value=True)
                        with gr.Row():
                            master_output = gr.Textbox(
                                label="--output  (blank = auto)",
                                placeholder="masters/S01E01_the413_2026-04-26.mp3",
                                scale=2,
                            )
                            master_daw_dir = gr.Textbox(
                                label="--daw-dir  (blank = auto)",
                                placeholder="daw/S01E01/",
                                scale=2,
                            )
                        master_btn = gr.Button("▶ Run Master", variant="primary")

                log_box = gr.Textbox(
                    label="Output", lines=24, max_lines=24, autoscroll=True, interactive=False,
                    elem_id="run-stage-log",
                )

                # ── Button handlers ───────────────────────────────────
                def run_scan(ep, script, speakers, as_json):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
                    try:
                        cmd = _cmd_scan(slug, tag, script, speakers, as_json)
                    except ValueError as exc:
                        yield str(exc)
                        return
                    yield from _execute_cmd(cmd)

                def run_parse(ep, script, preview, quiet, debug, stats, speakers):
                    ep = (ep or "").strip()
                    if not ep:
                        yield "Select an episode or type a new episode tag (e.g. S04E04)."
                        return
                    slug, tag = _parse_choice(ep)
                    if not tag:
                        if re.match(r"^[A-Z][A-Z0-9]+$", ep):
                            # User typed a raw episode tag (e.g. S01E01) — derive slug
                            # from the active show, falling back to a legacy root
                            # project.json when no show has been activated.
                            from xil_pipeline.models import get_active_show, resolve_slug
                            slug = get_active_show() or resolve_slug(
                                None,
                                os.path.join(str(get_workspace_root()), "project.json"),
                            )
                            tag = ep
                        else:
                            yield (
                                "⚠️ Show selected but no episode tag. "
                                "Type an episode tag in the field above (e.g. S01E01) and try again."
                            )
                            return
                    try:
                        cmd = _cmd_parse(slug, tag, script, preview or None, quiet, debug, stats, speakers)
                    except ValueError as exc:
                        yield str(exc)
                        return
                    yield from _execute_cmd(cmd)

                def run_produce(ep, dry_run, backend, gen_sfx, gen_music, gen_amb,
                                local_only, terse, start_from, stop_at, exaggeration,
                                cfg_weight, cb_python, force, sfx_backend,
                                adl2_python, adl2_guidance, adl2_steps, adl2_neg_prompt):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
                    if not tag:
                        yield "⚠️ No episode tag — run Parse first (Run Stage → Parse tab)."
                        return
                    cmd = _cmd_produce(slug, tag, dry_run, backend, gen_sfx, gen_music, gen_amb,
                                       local_only, terse,
                                       int(start_from) if start_from else None,
                                       int(stop_at) if stop_at else None,
                                       exaggeration, cb_python or "", force=force,
                                       cfg_weight=cfg_weight,
                                       sfx_backend=sfx_backend or "elevenlabs",
                                       adl2_python=adl2_python or "",
                                       adl2_guidance=adl2_guidance or 3.5,
                                       adl2_steps=int(adl2_steps) if adl2_steps else 200,
                                       adl2_neg_prompt=adl2_neg_prompt or "low quality, noise")
                    yield from _execute_cmd(cmd)

                def run_assemble(ep, gap_ms, parsed_path, output):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
                    if not tag:
                        yield "⚠️ No episode tag — run Parse first (Run Stage → Parse tab)."
                        return
                    cmd = _cmd_assemble(slug, tag, int(gap_ms) if gap_ms else 600,
                                        parsed_path, output)
                    yield from _execute_cmd(cmd)

                def run_daw(ep, dry_run, gap_ms, timeline, timeline_html,
                            macro, output_dir):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
                    if not tag:
                        yield "⚠️ No episode tag — run Parse first (Run Stage → Parse tab)."
                        return
                    cmd = _cmd_daw(slug, tag, dry_run, int(gap_ms) if gap_ms else 600,
                                   timeline, timeline_html, macro, False, output_dir)
                    yield from _execute_cmd(cmd)

                def run_master(ep, dry_run, output, daw_dir):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
                    if not tag:
                        yield "⚠️ No episode tag — run Parse first (Run Stage → Parse tab)."
                        return
                    cmd = _cmd_master(slug, tag, dry_run, output, daw_dir)
                    yield from _execute_cmd(cmd)

                script_save_btn.click(
                    fn=_save_script_file,
                    inputs=[script_editor, script_filename],
                    outputs=[script_status],
                ).then(
                    fn=lambda: (
                        gr.update(choices=_script_choices()),
                        gr.update(choices=_script_choices()),
                        gr.update(choices=_episode_choices()),
                        gr.update(choices=_episode_choices()),
                    ),
                    outputs=[scan_script, parse_script, run_ep_dd, parse_ep_dd],
                )
                run_ep_refresh_btn.click(
                    fn=lambda: gr.update(choices=_episode_choices()),
                    outputs=run_ep_dd,
                )
                scan_refresh_btn.click(
                    fn=lambda: gr.update(choices=_script_choices()),
                    outputs=scan_script,
                )
                parse_ep_refresh_btn.click(
                    fn=lambda: gr.update(choices=_episode_choices()),
                    outputs=parse_ep_dd,
                )
                parse_refresh_btn.click(
                    fn=lambda: gr.update(choices=_script_choices()),
                    outputs=parse_script,
                )
                scan_btn.click(
                    fn=run_scan,
                    inputs=[run_ep_dd, scan_script, scan_speakers, scan_json_cb],
                    outputs=log_box,
                )
                parse_btn.click(
                    fn=run_parse,
                    inputs=[parse_ep_dd, parse_script,
                             parse_preview, parse_quiet_cb, parse_debug_cb, parse_stats_cb, parse_speakers],
                    outputs=log_box,
                ).then(
                    fn=lambda: gr.update(choices=_episode_choices()),
                    outputs=run_ep_dd,
                )
                prod_btn.click(
                    fn=run_produce,
                    inputs=[run_ep_dd, prod_dry_run_cb, prod_backend_dd,
                             prod_gen_sfx_cb, prod_gen_music_cb, prod_gen_amb_cb,
                             prod_local_only_cb, prod_terse_cb,
                             prod_start_from, prod_stop_at, prod_exaggeration,
                             prod_cfg_weight, prod_cb_python, prod_force_cb,
                             prod_sfx_backend_dd, prod_adl2_python,
                             prod_adl2_guidance, prod_adl2_steps, prod_adl2_neg_prompt],
                    outputs=log_box,
                )
                asm_btn.click(
                    fn=run_assemble,
                    inputs=[run_ep_dd, asm_gap_ms, asm_parsed, asm_output],
                    outputs=log_box,
                )
                daw_btn.click(
                    fn=run_daw,
                    inputs=[run_ep_dd, daw_dry_run_cb, daw_gap_ms,
                             daw_timeline_cb, daw_timeline_html_cb,
                             daw_macro_cb, daw_output_dir],
                    outputs=log_box,
                )
                master_btn.click(
                    fn=run_master,
                    inputs=[run_ep_dd, master_dry_run_cb, master_output, master_daw_dir],
                    outputs=log_box,
                )

            # ── Tab 4: Speakers ──────────────────────────────────────
            with gr.Tab("Speakers"):
                _initial_spk = speakers_config_choices()
                _initial_spk_val = _initial_spk[0] if _initial_spk else None
                spk_file_dd = gr.Dropdown(
                    label="Speakers file",
                    choices=_initial_spk,
                    value=_initial_spk_val,
                    interactive=True,
                )
                spk_editor = gr.Code(
                    value=load_speakers_config(_initial_spk_val) if _initial_spk_val else "",
                    language="json",
                    lines=20,
                    label="speakers.json",
                )
                with gr.Row():
                    spk_reload_btn = gr.Button("↺ Reload", size="sm", scale=0)
                    spk_save_btn = gr.Button("💾 Save", variant="primary", size="sm", scale=0)
                spk_status = gr.Textbox(label="Status", lines=1, interactive=False)

                spk_file_dd.change(
                    fn=load_speakers_config,
                    inputs=spk_file_dd,
                    outputs=spk_editor,
                )
                spk_reload_btn.click(
                    fn=load_speakers_config,
                    inputs=spk_file_dd,
                    outputs=spk_editor,
                )
                spk_save_btn.click(
                    fn=save_speakers_config,
                    inputs=[spk_file_dd, spk_editor],
                    outputs=spk_status,
                )

            # ── Tab 5: Cast Config ──────────────────────────────────
            with gr.Tab("Cast Config"):
                _initial_casts = cast_config_choices()
                _initial_cast_val = _initial_casts[0] if _initial_casts else None
                cast_file_dd = gr.Dropdown(
                    label="Cast config file",
                    choices=_initial_casts,
                    value=_initial_cast_val,
                    interactive=True,
                )
                cast_editor = gr.Code(
                    value=load_cast_config(_initial_cast_val) if _initial_cast_val else "",
                    language="json",
                    lines=30,
                    label="cast config",
                )
                with gr.Row():
                    cast_reload_btn = gr.Button("↺ Reload", size="sm", scale=0)
                    cast_save_btn = gr.Button("💾 Save", variant="primary", size="sm", scale=0)
                cast_status = gr.Textbox(label="Status", lines=1, interactive=False)

                cast_file_dd.change(
                    fn=load_cast_config,
                    inputs=cast_file_dd,
                    outputs=cast_editor,
                )
                cast_reload_btn.click(
                    fn=load_cast_config,
                    inputs=cast_file_dd,
                    outputs=cast_editor,
                )
                cast_save_btn.click(
                    fn=save_cast_config,
                    inputs=[cast_file_dd, cast_editor],
                    outputs=cast_status,
                )

            # ── Tab 6: SFX Config ────────────────────────────────────
            with gr.Tab("SFX Config"):
                _initial_sfx = sfx_config_choices()
                _initial_sfx_val = _initial_sfx[0] if _initial_sfx else None
                sfx_file_dd = gr.Dropdown(
                    label="SFX config file",
                    choices=_initial_sfx,
                    value=_initial_sfx_val,
                    interactive=True,
                )
                sfx_editor = gr.Code(
                    value=load_sfx_config(_initial_sfx_val) if _initial_sfx_val else "",
                    language="json",
                    lines=30,
                    label="sfx config",
                )
                with gr.Row():
                    sfx_reload_btn = gr.Button("↺ Reload", size="sm", scale=0)
                    sfx_save_btn = gr.Button("💾 Save", variant="primary", size="sm", scale=0)
                sfx_status = gr.Textbox(label="Status", lines=1, interactive=False)

                sfx_file_dd.change(
                    fn=load_sfx_config,
                    inputs=sfx_file_dd,
                    outputs=sfx_editor,
                )
                sfx_reload_btn.click(
                    fn=load_sfx_config,
                    inputs=sfx_file_dd,
                    outputs=sfx_editor,
                )
                sfx_save_btn.click(
                    fn=save_sfx_config,
                    inputs=[sfx_file_dd, sfx_editor],
                    outputs=sfx_status,
                )

            # ── Tab 7: Audio Preview ────────────────────────────────
            with gr.Tab("Audio Preview"):
                with gr.Row():
                    audio_ep_dd = gr.Dropdown(
                        label="Episode",
                        choices=ep_choices,
                        value=ep_choices[0] if ep_choices else None,
                        scale=2,
                    )
                    stem_filter = gr.Radio(
                        ["all", "dialogue", "sfx", "music", "ambience"],
                        label="Filter",
                        value="all",
                        scale=3,
                    )
                stem_dd = gr.Dropdown(
                    label="Stem",
                    choices=initial_stem_labels,
                    value=initial_stem_labels[0] if initial_stem_labels else None,
                    interactive=True,
                )
                audio_player = gr.Audio(label="Playback", type="filepath", autoplay=False)
                with gr.Row():
                    play_all_sfx_btn   = gr.Button("▶ All SFX",      size="sm")
                    play_all_music_btn = gr.Button("▶ All Music",     size="sm")
                    play_all_amb_btn   = gr.Button("▶ All Ambience",  size="sm")

                audio_ep_dd.change(
                    fn=on_ep_or_filter_change,
                    inputs=[audio_ep_dd, stem_filter],
                    outputs=[stem_dd, audio_player],
                )
                stem_filter.change(
                    fn=on_ep_or_filter_change,
                    inputs=[audio_ep_dd, stem_filter],
                    outputs=[stem_dd, audio_player],
                )
                stem_dd.change(
                    fn=on_stem_select,
                    inputs=[audio_ep_dd, stem_dd, stem_filter],
                    outputs=audio_player,
                )
                def _play_all(ep, filter_type, progress):
                    def cb(i, n):
                        progress(i / n if n else None, desc=f"Decoding stems… {i + 1} / {n}")
                    return _concatenate_stems(ep, filter_type, progress_cb=cb)

                def on_play_all_sfx(ep, progress=gr.Progress()):
                    return _play_all(ep, "sfx", progress)

                def on_play_all_music(ep, progress=gr.Progress()):
                    return _play_all(ep, "music", progress)

                def on_play_all_ambience(ep, progress=gr.Progress()):
                    return _play_all(ep, "ambience", progress)

                play_all_sfx_btn.click(
                    fn=on_play_all_sfx,
                    inputs=[audio_ep_dd],
                    outputs=[audio_player],
                )
                play_all_music_btn.click(
                    fn=on_play_all_music,
                    inputs=[audio_ep_dd],
                    outputs=[audio_player],
                )
                play_all_amb_btn.click(
                    fn=on_play_all_ambience,
                    inputs=[audio_ep_dd],
                    outputs=[audio_player],
                )

            # ── Tab 8: Audio Grading ─────────────────────────────────
            with gr.Tab("Audio Grading"):
                grade_refresh_btn = gr.Button("⟳ Load / Refresh SFX library", size="sm")
                grade_summary = gr.Markdown("Click **Load** to scan SFX/.")
                grade_filter = gr.Radio(
                    ["all", "ungraded", "accurate", "rejected"],
                    label="Show", value="all",
                )
                sfx_dd = gr.Dropdown(label="SFX file", choices=[], interactive=True)
                grade_audio = gr.Audio(label="Playback", type="filepath", autoplay=False)
                grade_status = gr.Markdown("")
                with gr.Row():
                    mark_ok_btn = gr.Button("✓ Mark Accurate", variant="primary", size="sm")
                    mark_rej_btn = gr.Button("✗ Mark Rejected", variant="stop", size="sm")
                    clear_grade_btn = gr.Button("Clear grade", size="sm")

                def _grade_status_md(path):
                    if not path:
                        return ""
                    g = _sfx_grade_cache.get(path, "")
                    label = {"accurate": "✓ accurate", "rejected": "✗ rejected"}.get(g, "• ungraded")
                    return f"**{os.path.basename(path)}** — {label}"

                def _path_for_label(label, grade_filter_val):
                    if not label:
                        return None
                    for lbl, path in _sfx_choices(grade_filter_val):
                        if lbl == label:
                            return path
                    return None

                def on_grade_refresh(grade_filter_val):
                    _scan_sfx_grades()
                    labels = [lbl for lbl, _ in _sfx_choices(grade_filter_val)]
                    return (
                        gr.update(choices=labels, value=labels[0] if labels else None),
                        _sfx_summary(),
                    )

                def on_grade_filter_change(grade_filter_val):
                    labels = [lbl for lbl, _ in _sfx_choices(grade_filter_val)]
                    return gr.update(choices=labels, value=labels[0] if labels else None)

                def on_sfx_select(label, grade_filter_val, progress=gr.Progress()):
                    path = _path_for_label(label, grade_filter_val)
                    if not path:
                        return gr.update(value=None), ""
                    _log_activity(f"GRADE preview → {os.path.basename(path)}")
                    local = _cached_audio_path(
                        path, _copy_progress(progress, os.path.basename(path)))
                    return gr.update(value=local), _grade_status_md(path)

                def _apply_grade(label, grade_filter_val, status):
                    path = _path_for_label(label, grade_filter_val)
                    if not path:
                        return gr.update(), gr.update(), "", _sfx_summary()
                    _write_sfx_grade(path, status)
                    _sfx_grade_cache[path] = status if status in _GRADES else ""
                    _update_grade_cache_entry(path, status)
                    _log_activity(f"GRADE {status or 'cleared'} → {os.path.basename(path)}")
                    # Rebuild the (filtered) list; if the graded item dropped out of
                    # the current filter, advance to the next item for fast grading.
                    labels = [lbl for lbl, _ in _sfx_choices(grade_filter_val)]
                    new_label = f"{_GRADE_GLYPH.get(_sfx_grade_cache[path], '•')}  {os.path.basename(path)}"
                    sel = new_label if new_label in labels else (labels[0] if labels else None)
                    sel_path = _path_for_label(sel, grade_filter_val) if sel else None
                    return (
                        gr.update(choices=labels, value=sel),
                        gr.update(value=_cached_audio_path(sel_path) if sel_path else None),
                        _grade_status_md(sel_path),
                        _sfx_summary(),
                    )

                grade_refresh_btn.click(
                    fn=on_grade_refresh, inputs=[grade_filter],
                    outputs=[sfx_dd, grade_summary],
                )
                grade_filter.change(
                    fn=on_grade_filter_change, inputs=[grade_filter], outputs=[sfx_dd],
                )
                sfx_dd.change(
                    fn=on_sfx_select, inputs=[sfx_dd, grade_filter],
                    outputs=[grade_audio, grade_status],
                )
                mark_ok_btn.click(
                    fn=lambda label, f: _apply_grade(label, f, "accurate"),
                    inputs=[sfx_dd, grade_filter],
                    outputs=[sfx_dd, grade_audio, grade_status, grade_summary],
                )
                mark_rej_btn.click(
                    fn=lambda label, f: _apply_grade(label, f, "rejected"),
                    inputs=[sfx_dd, grade_filter],
                    outputs=[sfx_dd, grade_audio, grade_status, grade_summary],
                )
                clear_grade_btn.click(
                    fn=lambda label, f: _apply_grade(label, f, ""),
                    inputs=[sfx_dd, grade_filter],
                    outputs=[sfx_dd, grade_audio, grade_status, grade_summary],
                )

            # ── Tab 9: Timeline ──────────────────────────────────────
            with gr.Tab("Timeline"):
                tl_ep_dd = gr.Dropdown(label="Episode", choices=ep_choices)
                tl_html = gr.HTML("<p>Select an episode above.</p>")
                tl_ep_dd.change(fn=on_timeline_ep_change, inputs=tl_ep_dd, outputs=tl_html)

        def _use_show_and_reload(show_name):
            status = run_use_show(show_name)
            content, path = load_project_json()
            return status, content, path

        use_show_btn.click(
            fn=_use_show_and_reload,
            inputs=[use_show_dd],
            outputs=[use_show_status, proj_editor, proj_path_display],
            api_name="setup_use_show",
        )

        def _refresh_all_with_project():
            rows, aud, run, tl = refresh_all()
            content, path = load_project_json()
            return rows, aud, run, tl, content, path

        refresh_btn.click(
            fn=_refresh_all_with_project,
            outputs=[ep_table, audio_ep_dd, run_ep_dd, tl_ep_dd, proj_editor, proj_path_display],
        )

        # Fill the Episodes table after the page loads rather than during app
        # construction, so xil-gui startup stays snappy even on large workspaces.
        # The placeholder row shows "Collecting episode status…" until this resolves.
        demo.load(fn=_refresh_episodes, outputs=ep_table)

        # After "Create show" completes, refresh all episode dropdowns so the new
        # show stub appears immediately without the user having to press ⟳.
        _init_click.then(
            fn=lambda: (
                gr.update(choices=_episode_choices()),
                gr.update(choices=_episode_choices()),
                gr.update(choices=_episode_choices()),
            ),
            outputs=[run_ep_dd, parse_ep_dd, tl_ep_dd],
        )

    demo.queue()
    return demo


# ── CLI entry point ────────────────────────────────────────────────────────

def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-gui."""
    parser = argparse.ArgumentParser(
        prog="xil-gui",
        description=(
            "Launch the xil-pipeline web dashboard (Gradio). Opens a browser UI "
            "with nine tabs: Setup (initialize a workspace / select the active "
            "show), Project (edit project.json), Episodes (workspace overview "
            "with parse/stems/DAW/master status), Run Stage (launch pipeline "
            "stages with live log streaming; dry-run on by default), Speakers, "
            "Cast Config and SFX Config (edit the respective JSON configs), "
            "Audio Preview (browse and play stems in the browser), and Timeline "
            "(interactive HTML timeline)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Requires the [gui] extra:\n"
            "  pip install 'xil-pipeline[gui]'\n\n"
            "Partner sharing (temporary 72h public URL, open access, no auth —\n"
            "share only with trusted collaborators):\n"
            "  xil-gui --share\n"
        ),
    )
    parser.add_argument(
        "--port", type=int, default=7860,
        help="Port to listen on (default: 7860)",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Host address to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--share", action="store_true",
        help="Generate a public ngrok URL for partner access (open, no auth)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        metavar="FILE",
        help="Append a timestamped session activity log to FILE",
    )
    return parser


def _register_sfx_routes(app) -> None:
    """Register the timeline editor's /xil/* routes on a FastAPI *app*.

    Must be called with the app Gradio creates during ``launch()`` —
    ``launch()`` replaces ``demo.app`` with a new instance, so routes added
    to the pre-launch object are silently discarded.
    """
    from fastapi.responses import JSONResponse as _JSONResponse

    @app.get("/xil/get-sfx")
    async def _api_get_sfx(slug: str, tag: str, key: str):
        if not (_is_safe_slug_or_tag(slug) and _is_safe_slug_or_tag(tag)):
            return _JSONResponse({"error": "invalid slug or tag"}, status_code=400)
        # Already validated above (no separators possible) — basename() is a
        # no-op here, kept only because static analysis specifically
        # recognizes it as neutralizing path-injection taint.
        slug, tag = os.path.basename(slug), os.path.basename(tag)
        from xil_pipeline.models import derive_paths as _dp
        sfx_path = _dp(slug, tag)["sfx"]
        _check_workspace_path(sfx_path)
        if not os.path.exists(sfx_path):
            return _JSONResponse({"error": "sfx config not found"}, status_code=404)
        with open(sfx_path, encoding="utf-8") as f:
            data = json.load(f)
        return _JSONResponse({
            "effect": data.get("effects", {}).get(key, {}),
            "defaults": data.get("defaults", {}),
        })

    @app.post("/xil/update-sfx")
    async def _api_update_sfx(request: _FastAPIRequest):
        body = await request.json()
        slug_b, tag_b, key_b = body["slug"], body["tag"], body["key"]
        if not (_is_safe_slug_or_tag(slug_b) and _is_safe_slug_or_tag(tag_b)):
            return _JSONResponse({"ok": False, "error": "invalid slug or tag"}, status_code=400)
        # Already validated above (no separators possible) — basename() is a
        # no-op here, kept only because static analysis specifically
        # recognizes it as neutralizing path-injection taint.
        slug_b, tag_b = os.path.basename(slug_b), os.path.basename(tag_b)
        from xil_pipeline.models import derive_paths as _dp
        sfx_path = _dp(slug_b, tag_b)["sfx"]
        _check_workspace_path(sfx_path)
        if not os.path.exists(sfx_path):
            return _JSONResponse({"ok": False, "error": "sfx config not found"}, status_code=404)
        with open(sfx_path, encoding="utf-8") as f:
            data = json.load(f)
        effect = data.setdefault("effects", {}).setdefault(key_b, {})
        for field in ("volume_percentage", "ramp_in_seconds", "ramp_out_seconds", "play_duration"):
            val = body.get(field)
            if val is None:
                effect.pop(field, None)
            else:
                effect[field] = val
        with open(sfx_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        # Journal the edit so it survives sfx_{tag}.json being cleared and
        # regenerated (replayed by generate_sfx_config / xil sfx-restore).
        # A journal failure must never fail the save itself.
        try:
            from xil_pipeline.sfx_common import SFX_EDIT_FIELDS, append_sfx_edit
            append_sfx_edit(sfx_path, key_b, {f: body.get(f) for f in SFX_EDIT_FIELDS})
        except Exception as exc:
            _log_activity(f"[WARN] sfx edit journal write failed: {exc}")
        _log_activity(f"SFX edit via timeline: {slug_b}/{tag_b} → {key_b!r}")
        return _JSONResponse({"ok": True, "message": f"Saved {key_b!r} — re-run xil daw to apply."})


def main() -> None:
    """CLI entry point for the Gradio web dashboard."""
    global _activity_log
    args = get_parser().parse_args()
    if args.output:
        _activity_log = open(args.output, "a", encoding="utf-8", buffering=1)
    try:
        demo = _build_app()
        demo.launch(
            server_name=args.host,
            server_port=args.port,
            share=args.share,
            allowed_paths=[str(get_workspace_root())],
            prevent_thread_lock=True,
        )
        # launch() replaced demo.app with the FastAPI app that actually
        # serves requests — only now can the /xil/* routes be attached.
        _register_sfx_routes(demo.app)
        demo.block_thread()
    finally:
        if _activity_log:
            _activity_log.close()


if __name__ == "__main__":
    main()
