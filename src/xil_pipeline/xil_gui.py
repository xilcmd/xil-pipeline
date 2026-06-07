# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Gradio web dashboard for xil-pipeline.

A browser-based GUI that supplements the CLI for visual oversight,
audio preview, and sharing episode review with collaborators.

**Usage:**

```bash
xil-gui                    # opens http://localhost:7860
xil-gui --port 8080        # custom port
xil-gui --share            # generate public URL for partner access (72h tunnel)
```

Install the optional [gui] extra first:
    pip install 'xil-pipeline[gui]'
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys

from xil_pipeline.models import get_workspace_root

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
    """Return [(slug, tag), ...] sorted newest tag first, checking both layouts."""
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
    title, season_title = _ep_meta(slug, tag)
    label = f"{slug}  {tag}"
    if season_title:
        label += f"  [{season_title}]"
    if title:
        label += f"  —  {title}"
    return label


def _episode_choices() -> list[str]:
    return [_ep_choice(slug, tag) for slug, tag in _find_episodes()]


def _script_choices() -> list[str]:
    """Return relative paths to all scripts/*.md files, sorted."""
    return sorted(glob.glob(os.path.join(str(get_workspace_root()), "scripts", "*.md")))


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
    """Write script to {workspace_root}/scripts/{filename}. Refuses to overwrite."""
    if not text.strip():
        return "⚠️ No script content to save."
    filename = filename.strip()
    if not filename:
        return "⚠️ Filename is empty — run Analyze Header first."
    if not filename.endswith(".md"):
        filename += ".md"

    scripts_dir = get_workspace_root() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    dest = scripts_dir / filename

    if dest.exists():
        return f"⚠️ Already exists: scripts/{filename} — edit the filename above to save a new version."

    dest.write_text(text, encoding="utf-8")
    return f"✅ Saved: scripts/{filename}"


def _parse_choice(choice: str) -> tuple[str, str]:
    """'the413  S03E03' → ('the413', 'S03E03')"""
    parts = choice.strip().split()
    if len(parts) >= 2:
        return parts[0], parts[1]
    return "", ""


def _stage_status(slug: str, tag: str) -> dict[str, str]:
    from xil_pipeline.models import derive_paths
    p = derive_paths(slug, tag)
    stems_dir = p["stems"]
    stem_count = len(glob.glob(os.path.join(stems_dir, "*.mp3"))) if os.path.isdir(stems_dir) else 0
    daw_dir = p["daw"]
    has_daw = os.path.exists(os.path.join(daw_dir, f"{tag}_layer_dialogue.wav"))
    # Master: check new layout, then legacy locations
    root = str(get_workspace_root())
    has_master = (
        os.path.exists(p["master"])
        or bool(glob.glob(os.path.join(root, "masters", f"{tag}_*.mp3")))
        or bool(glob.glob(os.path.join(root, f"{slug}_{tag}_master.mp3")))
    )
    return {
        "parse":    "✓" if os.path.exists(p["parsed"]) else "○",
        "produce":  f"✓ {stem_count}" if stem_count > 0 else "○",
        "assemble": "✓" if has_master else "○",
        "daw":      "✓" if has_daw else "○",
        "master":   "✓" if has_master else "○",
    }


def _refresh_episodes() -> list[list[str]]:
    rows = []
    for slug, tag in _find_episodes():
        st = _stage_status(slug, tag)
        title, season_title = _ep_meta(slug, tag)
        desc = title
        if season_title:
            desc = f"[{season_title}]  —  {title}" if title else f"[{season_title}]"
        rows.append([tag, slug, desc, st["parse"], st["produce"], st["daw"], st["master"]])
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


def _concatenate_stems(ep_choice: str, filter_type: str) -> str | None:
    """Concatenate all stems of filter_type for ep_choice into a temp MP3. Returns path."""
    if not ep_choice:
        return None
    slug, tag = _parse_choice(ep_choice)
    stems = _load_stems(slug, tag, filter_type=filter_type)
    if not stems:
        return None
    try:
        import tempfile

        from pydub import AudioSegment
        combined = AudioSegment.empty()
        for _, path in stems:
            combined += AudioSegment.from_mp3(path)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        combined.export(tmp.name, format="mp3")
        return tmp.name
    except Exception:
        return None


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
            output += line
            yield output
        proc.wait()
        output += f"\n[exit {proc.returncode}]"
        yield output
    except Exception as exc:
        yield f"{header}\nError: {exc}"


def _execute_cmd(cmd: list[str]):
    """Generator: run cmd, yield accumulated stdout to a log box."""
    header = "$ " + " ".join(cmd) + "\n\n"
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
            buf += line
            yield buf
        proc.wait()
        buf += f"\n[exit {proc.returncode}]"
        yield buf
    except Exception as exc:
        yield header + f"\n[ERROR] {exc}\n"


def _cmd_scan(slug: str, tag: str, script_path: str | None, speakers: str, as_json: bool) -> list[str]:
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
    module = _STAGE_MODULES["parse"]
    if script_path and str(script_path).strip():
        cmd = [sys.executable, "-m", module, str(script_path).strip(), "--episode", tag]
    else:
        import glob as _glob
        candidates = sorted(_glob.glob(os.path.join(str(get_workspace_root()), "scripts", "*.md")))
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
                 force: bool = False, cfg_weight: float = 0.5) -> list[str]:
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
    if backend == "chatterbox":
        if exaggeration != 0.5:
            cmd += ["--exaggeration", f"{exaggeration:.2f}"]
        if cfg_weight != 0.5:
            cmd += ["--cfg-weight", f"{cfg_weight:.2f}"]
        if cb_python and cb_python.strip():
            cmd += ["--chatterbox-python", cb_python.strip()]
    if force:
        cmd.append("--force")
    return cmd


def _cmd_assemble(slug: str, tag: str, gap_ms: int,
                  parsed_path: str, output: str) -> list[str]:
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

    def on_ep_or_filter_change(choice, filter_type):
        if not choice:
            return gr.update(choices=[], value=None), gr.update(value=None)
        slug, tag = _parse_choice(choice)
        stems = _load_stems(slug, tag, filter_type)
        labels = [lbl for lbl, _ in stems]
        return (
            gr.update(choices=labels, value=labels[0] if labels else None),
            gr.update(value=None),
        )

    def on_stem_select(episode_choice, stem_label, filter_type):
        if not episode_choice or not stem_label:
            return gr.update(value=None)
        slug, tag = _parse_choice(episode_choice)
        for lbl, path in _load_stems(slug, tag, filter_type):
            if lbl == stem_label:
                return gr.update(value=path)
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
        abs_path = os.path.abspath(html_path)
        return (
            f'<iframe src="/gradio_api/file={abs_path}" '
            f'style="width:100%;height:600px;border:none;"></iframe>'
        )

    def refresh_all():
        new_choices = _episode_choices()
        rows = _refresh_episodes()
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
        if season.strip():
            cmd += ["--season", season.strip()]
        if season_title.strip():
            cmd += ["--season-title", season_title.strip()]
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
                output += line
                yield output
            proc.wait()
            output += f"\n[exit {proc.returncode}]"
            yield output
        except Exception as exc:
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
        return f"Saved {path}"

    def cast_config_choices() -> list[str]:
        return _find_cast_configs()

    def load_cast_config(path: str) -> str:
        if not path:
            return ""
        if not os.path.exists(path):
            return f"// File not found: {path}"
        with open(path, encoding="utf-8") as f:
            return f.read()

    def save_cast_config(path: str, text: str) -> str:
        if not path:
            return "No file selected."
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON — not saved: {exc}"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return f"Saved {path}"

    def speakers_config_choices() -> list[str]:
        return _find_speakers_configs()

    def load_speakers_config(path: str) -> str:
        if not path:
            return ""
        if not os.path.exists(path):
            return f"// File not found: {path}"
        with open(path, encoding="utf-8") as f:
            return f.read()

    def save_speakers_config(path: str, text: str) -> str:
        if not path:
            return "No file selected."
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON — not saved: {exc}"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return f"Saved {path}"

    def sfx_config_choices() -> list[str]:
        return _find_sfx_configs()

    def load_sfx_config(path: str) -> str:
        if not path:
            return ""
        if not os.path.exists(path):
            return f"// File not found: {path}"
        with open(path, encoding="utf-8") as f:
            return f.read()

    def save_sfx_config(path: str, text: str) -> str:
        if not path:
            return "No file selected."
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON — not saved: {exc}"
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return f"Saved {path}"

    # ── layout ────────────────────────────────────────────────────────────

    with gr.Blocks(title="xil-pipeline", theme=gr.themes.Soft()) as demo:
        gr.Markdown("# xil-pipeline")
        gr.Markdown(f"**Workspace:** `{workspace}`")

        with gr.Row():
            refresh_btn = gr.Button("⟳ Refresh", size="sm", scale=0)

        with gr.Tabs():

            # ── Tab 0: Setup ─────────────────────────────────────────
            with gr.Tab("Setup"):
                gr.Markdown("### Active show")
                gr.Markdown(
                    "Select which show is active. All pipeline commands will use this show's "
                    "`configs/{slug}/project.json` to resolve the show slug and season defaults."
                )
                with gr.Row():
                    from xil_pipeline.models import get_active_show as _get_active_show, show_slug as _show_slug
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
                init_btn = gr.Button("▶ Create show", variant="primary")
                init_log = gr.Textbox(
                    label="Output", lines=12, max_lines=12, autoscroll=True, interactive=False,
                )

                def run_init_and_refresh(show_name, content_type, season, season_title):
                    output = ""
                    for chunk in run_init(show_name, content_type, season, season_title):
                        output = chunk
                        yield output, gr.update()
                    new_shows = _list_available_shows()
                    from xil_pipeline.models import get_active_show as _gas, show_slug as _ss
                    new_active = next((n for n in new_shows if _ss(n) == _gas()), None)
                    yield output, gr.update(choices=new_shows, value=new_active)

                init_btn.click(
                    fn=run_init_and_refresh,
                    inputs=[init_show, init_type, init_season, init_season_title],
                    outputs=[init_log, use_show_dd],
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

            # ── Tab 1: Speakers ──────────────────────────────────────
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

            # ── Tab 2: Cast Config ──────────────────────────────────
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

            # ── Tab 3: SFX Config ────────────────────────────────────
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

            # ── Tab 4: Episodes ─────────────────────────────────────
            with gr.Tab("Episodes"):
                ep_table = gr.Dataframe(
                    headers=["Tag", "Slug", "Title  [Arc]", "Parse", "Stems", "DAW", "Master"],
                    value=_refresh_episodes(),
                    interactive=False,
                    wrap=True,
                )

            # ── Tab 5: Audio Preview ────────────────────────────────
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
                play_all_sfx_btn.click(
                    fn=lambda ep: _concatenate_stems(ep, "sfx"),
                    inputs=[audio_ep_dd],
                    outputs=[audio_player],
                )
                play_all_music_btn.click(
                    fn=lambda ep: _concatenate_stems(ep, "music"),
                    inputs=[audio_ep_dd],
                    outputs=[audio_player],
                )
                play_all_amb_btn.click(
                    fn=lambda ep: _concatenate_stems(ep, "ambience"),
                    inputs=[audio_ep_dd],
                    outputs=[audio_player],
                )

            # ── Tab 6: Run Stage (Scripts → Scan → Parse → Produce → Assemble → DAW → Master)
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
                        parse_btn = gr.Button("▶ Run Parse", variant="primary")

                    # ── Produce ───────────────────────────────────────
                    with gr.Tab("Produce"):
                        with gr.Row():
                            prod_dry_run_cb = gr.Checkbox(label="--dry-run", value=True)
                            prod_backend_dd = gr.Dropdown(
                                label="--backend",
                                choices=["elevenlabs", "gtts", "chatterbox"],
                                value="chatterbox",
                            )
                        with gr.Row():
                            prod_gen_sfx_cb    = gr.Checkbox(label="--gen-sfx")
                            prod_gen_music_cb  = gr.Checkbox(label="--gen-music")
                            prod_gen_amb_cb    = gr.Checkbox(label="--gen-ambience")
                            prod_local_only_cb = gr.Checkbox(label="--local-only", value=True)
                            prod_terse_cb      = gr.Checkbox(label="--terse")
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
                            label="--exaggeration  (Chatterbox only, 0.0–1.0)",
                            minimum=0.0, maximum=1.0, step=0.05, value=0.5,
                        )
                        prod_cfg_weight = gr.Slider(
                            label="--cfg-weight  (Chatterbox only, 0.1–1.0)",
                            minimum=0.1, maximum=1.0, step=0.05, value=0.5,
                        )
                        prod_cb_python = gr.Textbox(
                            label="--chatterbox-python  (blank = auto-detect venv-chatterbox/)",
                            placeholder=_default_chatterbox_python(),
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
                    parts = ep.split()
                    if len(parts) >= 2:
                        # Standard dropdown choice: "the413 S03E03 — Episode Title"
                        slug, tag = parts[0], parts[1]
                    else:
                        # Custom-typed tag: resolve slug from project.json
                        from xil_pipeline.models import resolve_slug
                        slug = resolve_slug(
                            None,
                            os.path.join(str(get_workspace_root()), "project.json"),
                        )
                        tag = ep
                    try:
                        cmd = _cmd_parse(slug, tag, script, preview or None, quiet, debug, stats, speakers)
                    except ValueError as exc:
                        yield str(exc)
                        return
                    yield from _execute_cmd(cmd)

                def run_produce(ep, dry_run, backend, gen_sfx, gen_music, gen_amb,
                                local_only, terse, start_from, stop_at, exaggeration,
                                cfg_weight, cb_python, force):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
                    cmd = _cmd_produce(slug, tag, dry_run, backend, gen_sfx, gen_music, gen_amb,
                                       local_only, terse,
                                       int(start_from) if start_from else None,
                                       int(stop_at) if stop_at else None,
                                       exaggeration, cb_python or "", force=force,
                                       cfg_weight=cfg_weight)
                    yield from _execute_cmd(cmd)

                def run_assemble(ep, gap_ms, parsed_path, output):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
                    cmd = _cmd_assemble(slug, tag, int(gap_ms) if gap_ms else 600,
                                        parsed_path, output)
                    yield from _execute_cmd(cmd)

                def run_daw(ep, dry_run, gap_ms, timeline, timeline_html,
                            macro, output_dir):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
                    cmd = _cmd_daw(slug, tag, dry_run, int(gap_ms) if gap_ms else 600,
                                   timeline, timeline_html, macro, False, output_dir)
                    yield from _execute_cmd(cmd)

                def run_master(ep, dry_run, output, daw_dir):
                    if not ep:
                        yield "Select an episode first."
                        return
                    slug, tag = _parse_choice(ep)
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
                    ),
                    outputs=[scan_script, parse_script],
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
                             prod_cfg_weight, prod_cb_python, prod_force_cb],
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

            # ── Tab 8: Timeline ──────────────────────────────────────
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
        )

        def _refresh_all_with_project():
            rows, aud, run, tl = refresh_all()
            content, path = load_project_json()
            return rows, aud, run, tl, content, path

        refresh_btn.click(
            fn=_refresh_all_with_project,
            outputs=[ep_table, audio_ep_dd, run_ep_dd, tl_ep_dd, proj_editor, proj_path_display],
        )

    demo.queue()
    return demo


# ── CLI entry point ────────────────────────────────────────────────────────

def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-gui",
        description="Launch the xil-pipeline web dashboard (Gradio).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Requires the [gui] extra:\n"
            "  pip install 'xil-pipeline[gui]'\n\n"
            "Partner sharing (temporary 72h public URL):\n"
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
    return parser


def main() -> None:
    args = get_parser().parse_args()
    demo = _build_app()
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        allowed_paths=[str(get_workspace_root())],
    )


if __name__ == "__main__":
    main()
