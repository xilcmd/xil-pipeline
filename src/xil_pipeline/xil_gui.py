# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Gradio web dashboard for xil-pipeline.

A browser-based GUI that supplements the CLI for visual oversight,
audio preview, and sharing episode review with collaborators.

Usage:
    xil-gui                    # opens http://localhost:7860
    xil-gui --port 8080        # custom port
    xil-gui --share            # generate public URL for partner access (72h tunnel)

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

    # Legacy root layout: cast_{slug}_{tag}.json
    for path in glob.glob("cast_*.json"):
        m = _TAG_RE.match(os.path.basename(path))
        if m:
            pair = (m.group(1), m.group(2))
            if pair not in seen:
                seen.add(pair)
                results.append(pair)

    # Normalized layout: configs/{slug}/cast_{tag}.json
    for path in glob.glob(os.path.join("configs", "*", "cast_*.json")):
        slug = os.path.basename(os.path.dirname(path))
        m = _NEW_CAST_RE.match(os.path.basename(path))
        if m:
            pair = (slug, m.group(1))
            if pair not in seen:
                seen.add(pair)
                results.append(pair)

    results.sort(key=lambda x: (x[0], x[1]))
    return results


def _ep_choice(slug: str, tag: str) -> str:
    return f"{slug}  {tag}"


def _episode_choices() -> list[str]:
    return [_ep_choice(slug, tag) for slug, tag in _find_episodes()]


def _find_speakers_configs() -> list[str]:
    """Return relative paths to all speakers.json files, sorted by slug."""
    paths: list[str] = []
    for p in sorted(glob.glob(os.path.join("configs", "*", "speakers.json"))):
        paths.append(p)
    if os.path.exists("speakers.json"):
        paths.append("speakers.json")
    return paths


def _find_cast_configs() -> list[str]:
    """Return relative paths to all cast JSON configs, sorted by slug then tag."""
    paths: list[str] = []
    # Normalized layout: configs/{slug}/cast_{tag}.json
    for p in sorted(glob.glob(os.path.join("configs", "*", "cast_*.json"))):
        paths.append(p)
    # Legacy root layout: cast_{slug}_{tag}.json
    for p in sorted(glob.glob("cast_*.json")):
        if _TAG_RE.match(os.path.basename(p)):
            paths.append(p)
    return paths


_LEGACY_SFX_RE = re.compile(r"^sfx_(.+?)_([A-Z0-9]+)\.json$")


def _find_sfx_configs() -> list[str]:
    """Return relative paths to all SFX JSON configs, sorted by slug then tag."""
    paths: list[str] = []
    # Normalized layout: configs/{slug}/sfx_{tag}.json
    for p in sorted(glob.glob(os.path.join("configs", "*", "sfx_*.json"))):
        paths.append(p)
    # Legacy root layout: sfx_{slug}_{tag}.json
    for p in sorted(glob.glob("sfx_*.json")):
        if _LEGACY_SFX_RE.match(os.path.basename(p)):
            paths.append(p)
    return paths


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
    has_master = (
        os.path.exists(p["master"])
        or bool(glob.glob(os.path.join("masters", f"*{tag}*master*.mp3")))
        or bool(glob.glob(f"{slug}_{tag}_master.mp3"))
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
        rows.append([tag, slug, st["parse"], st["produce"], st["daw"], st["master"]])
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

    def auto_dir_from_show(show_name: str, current_dir: str) -> str:
        if current_dir.strip():
            return current_dir
        import re
        slug = re.sub(r"[^a-z0-9]+", "-", show_name.strip().lower()).strip("-")
        return slug if slug else ""

    def run_init(show_name: str, content_type: str, directory: str, season: str, season_title: str):
        if not show_name.strip():
            yield "Show name is required."
            return
        import re
        target_dir = directory.strip()
        if not target_dir:
            target_dir = re.sub(r"[^a-z0-9]+", "-", show_name.strip().lower()).strip("-")
        cmd = [sys.executable, "-m", "xil_pipeline.xil_init",
               "--show", show_name.strip(), "--type", content_type]
        if season.strip():
            cmd += ["--season", season.strip()]
        if season_title.strip():
            cmd += ["--season-title", season_title.strip()]
        cmd.append(target_dir)
        yield "$ " + " ".join(cmd) + "\n\n"
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(get_workspace_root()),
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

    _PROJECT_JSON_PATH = os.path.join(workspace, "project.json")

    def load_project_json() -> str:
        if os.path.exists(_PROJECT_JSON_PATH):
            with open(_PROJECT_JSON_PATH, encoding="utf-8") as f:
                return f.read()
        return json.dumps({"show": "", "season": 1}, indent=2)

    def save_project_json(text: str) -> str:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON — not saved: {exc}"
        with open(_PROJECT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        return f"Saved {_PROJECT_JSON_PATH}"

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

            # ── Tab 0: Project ───────────────────────────────────────
            with gr.Tab("Project"):
                gr.Markdown(f"### `{_PROJECT_JSON_PATH}`")
                proj_editor = gr.Code(
                    value=load_project_json(),
                    language="json",
                    lines=20,
                    label="project.json",
                )
                with gr.Row():
                    proj_reload_btn = gr.Button("↺ Reload", size="sm", scale=0)
                    proj_save_btn = gr.Button("💾 Save", variant="primary", size="sm", scale=0)
                proj_status = gr.Textbox(label="Status", lines=1, interactive=False)
                proj_reload_btn.click(fn=load_project_json, inputs=[], outputs=proj_editor)
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
                    headers=["Tag", "Slug", "Parse", "Stems", "DAW", "Master"],
                    value=_refresh_episodes(),
                    interactive=False,
                    wrap=True,
                )

            # ── Tab 5: Audio Preview ─────────────────────────────────
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

            # ── Tab 6: Run Stage ─────────────────────────────────────
            with gr.Tab("Run Stage"):
                gr.Markdown(
                    "Run pipeline stages against an episode. "
                    "**Dry-run is on by default** — uncheck to write output files."
                )
                with gr.Row():
                    run_ep_dd = gr.Dropdown(label="Episode", choices=ep_choices, scale=2)
                    run_stage_dd = gr.Dropdown(
                        label="Stage", choices=RUNNABLE_STAGES, value="4) assemble", scale=2,
                    )
                with gr.Row():
                    dry_run_cb = gr.Checkbox(
                        label="--dry-run (not supported by this stage)",
                        value=False,
                        interactive=False,
                    )
                    extra_flags = gr.Textbox(
                        label="Extra flags (scan: script path required here)",
                        placeholder="e.g. --gap-ms 300  |  scan: scripts/sample_S01E01.md",
                        scale=3,
                    )
                run_btn = gr.Button("▶ Run", variant="primary")

                def on_stage_change(stage):
                    supported = _stage_key(stage) in DRY_RUN_STAGES
                    return gr.update(
                        value=supported,
                        interactive=supported,
                        label="--dry-run" if supported else "--dry-run (not supported by this stage)",
                    )

                run_stage_dd.change(fn=on_stage_change, inputs=run_stage_dd, outputs=dry_run_cb)
                log_box = gr.Textbox(
                    label="Output", lines=24, max_lines=24, autoscroll=True, interactive=False,
                )
                run_btn.click(
                    fn=_run_stage,
                    inputs=[run_ep_dd, run_stage_dd, dry_run_cb, extra_flags],
                    outputs=log_box,
                )

            # ── Tab 7: Setup ─────────────────────────────────────────
            with gr.Tab("Setup"):
                gr.Markdown("### Initialize a new show workspace")
                gr.Markdown(
                    "Creates `project.json`, `speakers.json`, a type-specific sample script, "
                    "and empty subdirectories in the target directory."
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
                    init_dir = gr.Textbox(
                        label="New workspace directory",
                        placeholder="auto-filled from show name",
                        scale=2,
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
                init_btn = gr.Button("▶ Create workspace", variant="primary")
                init_log = gr.Textbox(
                    label="Output", lines=12, max_lines=12, autoscroll=True, interactive=False,
                )
                init_show.change(
                    fn=auto_dir_from_show,
                    inputs=[init_show, init_dir],
                    outputs=init_dir,
                )
                init_btn.click(
                    fn=run_init,
                    inputs=[init_show, init_type, init_dir, init_season, init_season_title],
                    outputs=init_log,
                )

            # ── Tab 8: Timeline ──────────────────────────────────────
            with gr.Tab("Timeline"):
                tl_ep_dd = gr.Dropdown(label="Episode", choices=ep_choices)
                tl_html = gr.HTML("<p>Select an episode above.</p>")
                tl_ep_dd.change(fn=on_timeline_ep_change, inputs=tl_ep_dd, outputs=tl_html)

        refresh_btn.click(
            fn=refresh_all,
            outputs=[ep_table, audio_ep_dd, run_ep_dd, tl_ep_dd],
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
