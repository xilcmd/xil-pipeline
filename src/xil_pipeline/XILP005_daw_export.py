# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Export episode audio as separate DAW layer WAV files.

Reads the parsed script JSON and episode stems to build four isolated,
full-length WAV files — one per audio layer — that can be imported into
Audacity (or any DAW) as pre-aligned tracks:

    daw/{TAG}/{TAG}_layer_dialogue.wav  — spoken dialogue (with effects)
    daw/{TAG}/{TAG}_layer_ambience.wav  — looped environmental background
    daw/{TAG}/{TAG}_layer_music.wav     — music stings and themes
    daw/{TAG}/{TAG}_layer_sfx.wav       — one-shot sound effects/beats

All four WAVs are exactly the same length (full episode duration) so
they align perfectly when imported at t=0.  The producer controls final
level balance and any additional processing inside the DAW.

An Audacity import helper script is also generated at:
    daw/{TAG}/{TAG}_open_in_audacity.py

Run it to print the file paths and manual import instructions; if
Audacity's mod-script-pipe is enabled it will attempt automation.

Usage:
    python XILP005_daw_export.py --episode S01E02 --dry-run
    python XILP005_daw_export.py --episode S01E02
    python XILP005_daw_export.py --episode S01E02 --output-dir exports/

No ElevenLabs API calls are made — this stage is safe to run freely.
"""

import argparse
import datetime
import json
import os
import subprocess
import textwrap

from xil_pipeline.mix_common import (
    apply_phone_filter,
    build_ambience_layer,
    build_dialogue_layer,
    build_foreground,
    build_foreground_timeline_only,
    build_music_layer,
    build_sfx_layer,
    collect_stem_plans,
    compute_ambience_labels,
    compute_dialogue_labels,
    compute_music_labels,
    compute_sfx_labels,
    load_entries_index,
)
from xil_pipeline.models import CastConfiguration, SfxConfiguration, VoiceConfig, derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner, tag_wav
from xil_pipeline.timeline_viz import build_timeline_data, render_html_timeline, render_terminal_timeline

STEMS_DIR = "stems"
DAW_DIR = "daw"
SILENCE_GAP_MS = 600

# Layer definitions: (key, filename_suffix, description)
LAYERS: list[tuple[str, str, str]] = [
    ("dialogue", "layer_dialogue", "Spoken dialogue (phone filter + pan applied)"),
    ("ambience", "layer_ambience", "Looped environmental background (no ducking)"),
    ("music",    "layer_music",    "Music stings and themes (no ducking)"),
    ("sfx",      "layer_sfx",      "One-shot sound effects and beat silences"),
]


def _write_labels(output_dir: str, fname: str, labels: list[tuple[float, float, str]]) -> None:
    """Write an Audacity label file (tab-separated start, end, text)."""
    with open(os.path.join(output_dir, fname), "w", encoding="utf-8") as lf:
        for start_s, end_s, text, *_ in labels:
            lf.write(f"{start_s:.3f}\t{end_s:.3f}\t{text}\n")


def _find_audacity_macros_dir() -> str | None:
    """Return the Audacity Macros directory as a Linux path, or None if not found.

    Works in WSL (queries APPDATA via cmd.exe) and native Windows Python
    (reads os.environ['APPDATA'] directly).
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        # Native Windows Python — APPDATA is already set.
        macros_dir = os.path.join(appdata, "audacity", "Macros")
        return macros_dir if os.path.isdir(macros_dir) else None
    # WSL: ask Windows for APPDATA, then convert to a Linux path.
    try:
        win_appdata = subprocess.check_output(
            ["cmd.exe", "/c", "echo %APPDATA%"], stderr=subprocess.DEVNULL
        ).decode().strip()
        linux_appdata = subprocess.check_output(
            ["wslpath", "-u", win_appdata], stderr=subprocess.DEVNULL
        ).decode().strip()
        macros_dir = os.path.join(linux_appdata, "audacity", "Macros")
        return macros_dir if os.path.isdir(macros_dir) else None
    except Exception:
        return None


def _to_windows_path(linux_path: str) -> str:
    """Convert an absolute Linux (WSL) path to a Windows path string.

    Falls back to returning the original path unchanged if ``wslpath`` is
    unavailable (e.g. native Linux or Windows Python environments).
    """
    try:
        return subprocess.check_output(
            ["wslpath", "-w", linux_path], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return linux_path


def generate_audacity_macro(
    output_dir: str,
    tag: str,
    layer_files: list[tuple[str, str]],
    show: str = "Sample Show",
    season_title: str | None = None,
    episode_title: str | None = None,
    artist: str = "XIL Pipeline",
) -> str | None:
    """Write an Audacity macro file that imports all layer WAVs and sets metadata.

    The macro is written to the Audacity Macros directory
    (``%APPDATA%\\audacity\\Macros\\<SLUG>_<TAG>.txt``) so it appears
    immediately under Tools → Macros without restarting Audacity.

    Args:
        output_dir: Directory containing the exported layer files.
        tag: Episode tag used to name the macro (e.g. ``"S02E03"``).
        layer_files: List of ``(track_name, filename)`` pairs to import.
        show: Show name for Album metadata (default ``"Sample Show"``).
        season_title: Season title for metadata title field.
        episode_title: Episode title for metadata title field.
        artist: Artist/creator credit for metadata.

    Returns:
        Path to the written macro file, or ``None`` if the Audacity Macros
        directory could not be located.
    """
    macros_dir = _find_audacity_macros_dir()
    if macros_dir is None:
        return None

    abs_output = os.path.abspath(output_dir)
    lines = []
    for _, filename in layer_files:
        if not filename.endswith(".wav"):
            continue
        win_path = _to_windows_path(os.path.join(abs_output, filename))
        lines.append(f'Import2: Filename="{win_path}"')

    # Set project metadata — appears in Edit > Metadata and in exported files.
    if season_title and episode_title:
        title = f"{tag}: {season_title} - {episode_title}"
    elif episode_title:
        title = f"{tag}: {episode_title}"
    else:
        title = tag
    year = str(datetime.date.today().year)
    lines.append(f'SetProject: X-Genre="Podcast" X-Album="{show}" '
                 f'X-Artist="{artist}" X-Title="{title}" X-Year="{year}"')

    from xil_pipeline.models import show_slug as _show_slug
    from xil_pipeline.models import DEFAULT_SLUG
    macro_slug = _show_slug(show).upper() if show else DEFAULT_SLUG.upper()
    macro_path = os.path.join(macros_dir, f"{macro_slug}_{tag}.txt")
    with open(macro_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return macro_path


def _make_audacity_script(
    tag: str,
    layer_files: list[tuple[str, str]],
    save_aup3: bool = False,
    show: str | None = None,
) -> str:
    """Generate the content of the Audacity import helper script.

    Args:
        tag: Episode tag (e.g. ``"S01E01"``).
        layer_files: List of ``(track_name, relative_filename)`` pairs.
        save_aup3: When True, include a SaveProject2 command after imports.
        show: Human-readable show title for display in the generated script.

    Returns:
        Python source code for the helper script as a string.
    """
    show_label = show if show else "Episode"
    layers_repr = repr(layer_files)
    script = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        \"\"\"Open {show_label} {tag} DAW layers in Audacity.

        Run this script while Audacity is open.  If mod-script-pipe is
        enabled the four layer WAVs are imported automatically.  Otherwise
        the file paths and manual import instructions are printed below.

        Enable mod-script-pipe in Audacity:
          Edit > Preferences > Modules > mod-script-pipe → Enabled
          (restart Audacity after enabling)
        \"\"\"
        import os
        import sys

        BASE_DIR = os.path.dirname(os.path.abspath(__file__))
        LAYERS = {layers_repr}


        def try_pipe_import(layers):
            \"\"\"Attempt Audacity import via mod-script-pipe named pipe.\"\"\"
            import platform
            import subprocess
            # On WSL, Audacity's pipe is a Windows kernel object unreachable from Linux
            # Python.  Re-invoke this script via Windows python.exe so it uses the
            # Windows pipe paths.
            if platform.system() != "Windows" and "WSL_DISTRO_NAME" in os.environ:
                try:
                    win_script = subprocess.check_output(
                        ["wslpath", "-w", os.path.abspath(__file__)]
                    ).decode().strip()
                    result = subprocess.run(["python.exe", win_script])
                    return result.returncode == 0
                except Exception as exc:
                    print(f"[!] WSL re-invoke failed: {{exc}}")
                    return False
            if platform.system() == "Windows":
                tofile = r"\\\\.\\pipe\\ToSrvPipe"
                fromfile = r"\\\\.\\pipe\\FromSrvPipe"
            else:
                tofile = "/tmp/audacity_script_pipe.to.app"
                fromfile = "/tmp/audacity_script_pipe.from.app"

            try:
                pipe_to = open(tofile, "wb", buffering=0)
                pipe_from = open(fromfile, "rb", buffering=0)
                try:
                    def send(cmd):
                        pipe_to.write((cmd + "\\n").encode("utf-8"))
                        lines = []
                        while True:
                            line = pipe_from.readline().decode("utf-8")
                            if line in ("", "\\n"):
                                break
                            lines.append(line.rstrip())
                        return lines

                    send("New:")
                    wav_count = 0
                    for name, filename in layers:
                        if not filename.endswith(".wav"):
                            continue
                        full_path = os.path.join(BASE_DIR, filename)
                        send(f"Import2: Filename={{full_path}}")
                        send(f"SetTrackStatus: Name={{name}}")
                        wav_count += 1
                    print(f"[✓] Imported {{wav_count}} WAV tracks into Audacity.")
                    # _SAVE_PLACEHOLDER_
                finally:
                    pipe_to.close()
                    pipe_from.close()
                return True
            except Exception as exc:
                print(f"[!] Pipe import failed: {{exc}}")
                return False


        def print_instructions(layers):
            \"\"\"Print manual import instructions.\"\"\"
            wavs = [(n, f) for n, f in layers if f.endswith(".wav")]
            labels = [(n, f) for n, f in layers if f.endswith(".txt")]
            print()
            print(f"{show_label} {tag} — Audacity Layer Import")
            print("=" * 45)
            print()
            print(f"Import these {{len(wavs)}} WAV files into Audacity:")
            print("  File > Import > Audio...  (Ctrl+Shift+I on Windows/Linux)")
            print()
            for i, (name, filename) in enumerate(wavs, 1):
                full = os.path.join(BASE_DIR, filename)
                print(f"  {{i}}. {{name:<12}}  {{full}}")
            if labels:
                print()
                print("Optional label tracks (File > Import > Labels...):")
                for i, (name, filename) in enumerate(labels, 1):
                    full = os.path.join(BASE_DIR, filename)
                    print(f"  {{i}}. {{name:<20}}  {{full}}")
            print()
            print("After importing, all tracks are pre-aligned at t=0.")
            print("No repositioning needed — just mix levels and export.")
            print()
            print("Suggested track order (top to bottom in Audacity):")
            print("  1. Dialogue  — speaking parts (phone filter already applied)")
            print("  2. Music     — stings and themes")
            print("  3. SFX       — one-shot effects")
            print("  4. Ambience  — background loops")
            print()


        if __name__ == "__main__":
            if not try_pipe_import(LAYERS):
                print_instructions(LAYERS)
        """)

    if save_aup3:
        aup3_save_code = (
            f'            aup3_path = os.path.join(BASE_DIR, "{tag}.aup3")\n'
            '            send(f"SaveProject2: Filename={aup3_path}")\n'
            '            print(f"[\u2713] Saved project: {aup3_path}")\n'
        )
        script = script.replace("            # _SAVE_PLACEHOLDER_\n", aup3_save_code)
    else:
        script = script.replace("            # _SAVE_PLACEHOLDER_\n", "")

    return script


def dry_run_daw(tag: str, stem_plans, entries_index: dict, output_dir: str) -> None:
    """Print a DAW export summary without writing any files.

    Args:
        tag: Episode tag.
        stem_plans: Classified stem list.
        entries_index: Parsed entry index.
        output_dir: Target directory (shown in summary).
    """
    bg_plans = [p for p in stem_plans if p.is_background]
    ambience = [p for p in bg_plans if p.direction_type == "AMBIENCE"]
    music = [p for p in bg_plans if p.direction_type == "MUSIC"]
    sfx = [p for p in stem_plans if p.direction_type in ("SFX", "BEAT")]
    dialogue = [p for p in stem_plans if p.entry_type == "dialogue"]

    print(f"\n--- DAW Export Dry Run: {tag} ---")
    print(f"   Stems directory : stems/{tag}/")
    print(f"   Output directory: {output_dir}/")
    print()
    print("   Layer             Stems")
    print("   ─────────────────────────────")
    print(f"   dialogue          {len(dialogue):3d} stems")
    print(f"   ambience          {len(ambience):3d} stems  (looped to scene boundaries)")
    print(f"   music             {len(music):3d} stems  (one-shot at cue points)")
    print(f"   sfx               {len(sfx):3d} stems")
    print()
    print("   Output files (all same duration as foreground track):")
    for _, suffix, desc in LAYERS:
        print(f"     {output_dir}/{tag}_{suffix}.wav  — {desc}")
    print(f"     {output_dir}/{tag}_open_in_audacity.py")
    print()


def export_daw_layers(
    config: dict[str, dict],
    stems_dir: str,
    parsed_path: str,
    output_dir: str,
    tag: str,
    save_aup3: bool = False,
    macro: bool = False,
    show: str = "Sample Show",
    season_title: str | None = None,
    episode_title: str | None = None,
    artist: str = "XIL Pipeline",
    timeline: bool = False,
    timeline_html: bool = False,
    sfx_config=None,
    gap_ms: int = 600,
) -> None:
    """Build and export all four DAW layer WAV files.

    Args:
        config: Per-speaker voice settings from cast config.
        stems_dir: Directory containing episode stem MP3 files.
        parsed_path: Path to the parsed script JSON (XILP001 output).
        output_dir: Directory to write the layer WAV files.
        tag: Episode tag used to name output files.
        save_aup3: When True, include a SaveProject2 step in the helper script.
        macro: When True, write an Audacity macro file to the Audacity Macros dir.
        preamble_cfg: Optional :class:`~models.Preamble` instance; when set,
            preamble stems are prepended at seq -2 (voice) and -1 (music).
        show: Show name for audio metadata (default ``"Sample Show"``).
        season_title: Season title for metadata artist field.
        episode_title: Episode title for metadata.
    """
    entries_index = load_entries_index(parsed_path)
    stem_plans = collect_stem_plans(stems_dir, entries_index, sfx_config=sfx_config)

    if not stem_plans:
        print(f" [!] No stems found in {stems_dir}/. Run XILP002 first.")
        return

    print(f"--- Building foreground timeline from {len(stem_plans)} stems ---")
    foreground, timeline = build_foreground(
        stem_plans, config, apply_phone_filter, gap_ms=gap_ms
    )

    if len(foreground) == 0:
        print(" [!] No foreground stems — cannot determine episode duration.")
        return

    total_ms = len(foreground)
    print(f"    Episode duration: {total_ms / 1000:.1f}s")

    os.makedirs(output_dir, exist_ok=True)

    layer_files: list[tuple[str, str]] = []

    # --- Dialogue layer ---
    print("--- Building dialogue layer ---")
    dlg, labels = build_dialogue_layer(
        stem_plans, timeline, total_ms, config, apply_phone_filter
    )
    fname = f"{tag}_layer_dialogue.wav"
    wav_path = os.path.join(output_dir, fname)
    dlg.export(wav_path, format="wav")
    tag_wav(wav_path, show=show, title=f"{tag} Dialogue", artist=artist)
    layer_files.append(("Dialogue", fname))
    print(f"    Written: {output_dir}/{fname}")

    # --- Dialogue label track ---
    _write_labels(output_dir, f"{tag}_labels_dialogue.txt", labels)
    layer_files.append(("Labels (Dialogue)", f"{tag}_labels_dialogue.txt"))
    print(f"    Written: {output_dir}/{tag}_labels_dialogue.txt")

    # --- Ambience layer ---
    print("--- Building ambience layer ---")
    amb, amb_labels = build_ambience_layer(stem_plans, timeline, total_ms, level_db=0)
    fname = f"{tag}_layer_ambience.wav"
    wav_path = os.path.join(output_dir, fname)
    amb.export(wav_path, format="wav")
    tag_wav(wav_path, show=show, title=f"{tag} Ambience", artist=artist)
    layer_files.append(("Ambience", fname))
    print(f"    Written: {output_dir}/{fname}")
    _write_labels(output_dir, f"{tag}_labels_ambience.txt", amb_labels)
    layer_files.append(("Labels (Ambience)", f"{tag}_labels_ambience.txt"))
    print(f"    Written: {output_dir}/{tag}_labels_ambience.txt")

    # --- Music layer ---
    print("--- Building music layer ---")
    mus, mus_labels = build_music_layer(stem_plans, timeline, total_ms, level_db=0)
    fname = f"{tag}_layer_music.wav"
    wav_path = os.path.join(output_dir, fname)
    mus.export(wav_path, format="wav")
    tag_wav(wav_path, show=show, title=f"{tag} Music", artist=artist)
    layer_files.append(("Music", fname))
    print(f"    Written: {output_dir}/{fname}")
    _write_labels(output_dir, f"{tag}_labels_music.txt", mus_labels)
    layer_files.append(("Labels (Music)", f"{tag}_labels_music.txt"))
    print(f"    Written: {output_dir}/{tag}_labels_music.txt")

    # --- SFX layer ---
    print("--- Building SFX layer ---")
    sfx, sfx_labels = build_sfx_layer(stem_plans, timeline, total_ms)
    fname = f"{tag}_layer_sfx.wav"
    wav_path = os.path.join(output_dir, fname)
    sfx.export(wav_path, format="wav")
    tag_wav(wav_path, show=show, title=f"{tag} SFX", artist=artist)
    layer_files.append(("SFX", fname))
    print(f"    Written: {output_dir}/{fname}")
    _write_labels(output_dir, f"{tag}_labels_sfx.txt", sfx_labels)
    layer_files.append(("Labels (SFX)", f"{tag}_labels_sfx.txt"))
    print(f"    Written: {output_dir}/{tag}_labels_sfx.txt")

    # --- Audacity helper script ---
    script_fname = f"{tag}_open_in_audacity.py"
    script_path = os.path.join(output_dir, script_fname)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(_make_audacity_script(tag, layer_files, save_aup3=save_aup3, show=show))
    os.chmod(script_path, 0o755)
    print(f"    Written: {output_dir}/{script_fname}")

    # --- Audacity macro (optional) ---
    if macro:
        macro_path = generate_audacity_macro(
            output_dir, tag, layer_files,
            show=show, season_title=season_title, episode_title=episode_title,
            artist=artist,
        )
        if macro_path:
            print(f"    Written: {macro_path}")
        else:
            print(" [!] Audacity Macros directory not found — macro not written.")

    # --- Timeline visualization (optional) ---
    if timeline or timeline_html:
        dlg_labels = compute_dialogue_labels(stem_plans, timeline)
        td = build_timeline_data(
            tag, total_ms / 1000.0,
            dlg_labels, amb_labels, mus_labels, sfx_labels,
        )
        if timeline:
            print(render_terminal_timeline(td))
        if timeline_html:
            html_path = os.path.join(output_dir, f"{tag}_timeline.html")
            render_html_timeline(td, html_path)
            print(f"    Written: {html_path}")

    print()
    print(f"--- Done! {len(layer_files)} layer WAVs in {output_dir}/ ---")
    print(f"    Import into Audacity: python {output_dir}/{script_fname}")
    if macro:
        from xil_pipeline.models import show_slug as _show_slug
        macro_label = _show_slug(show).upper() if show else "THE413"
        print(f"    Audacity macro:       Tools → Macros → {macro_label}_{tag} → Apply to Project")
    if save_aup3:
        print(f"    Will save project:    {output_dir}/{tag}.aup3")


def main() -> None:
    """CLI entry point for DAW layer export.

    Loads cast config, derives stem and parsed JSON paths from the
    episode tag, builds four per-layer WAV files and an Audacity helper
    script.  No ElevenLabs API key required.
    """
    with run_banner():
        parser = argparse.ArgumentParser(
            description="DAW Export — export episode as layered WAV files for Audacity"
        )
        parser.add_argument(
            "--episode", required=True,
            help="Episode tag (e.g. S01E02) — derives cast config, stems, and parsed JSON paths"
        )
        parser.add_argument(
            "--show", default=None,
            help="Show name override (default: from project.json)"
        )
        parser.add_argument(
            "--parsed", default=None,
            help="Path to parsed script JSON (default: parsed/parsed_<slug>_<TAG>.json)"
        )
        parser.add_argument(
            "--output-dir", default=None,
            help="Output directory for layer WAVs (default: daw/<TAG>/)"
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show export summary without writing files"
        )
        parser.add_argument(
            "--save-aup3", action="store_true",
            help="Include SaveProject2 step in the Audacity helper script (requires mod-script-pipe)"
        )
        parser.add_argument(
            "--macro", action="store_true",
            help="Write an Audacity macro to %%APPDATA%%\\audacity\\Macros\\ for one-click import"
        )
        parser.add_argument(
            "--timeline", action="store_true",
            help="Print an ASCII timeline visualization of asset placement to stdout"
        )
        parser.add_argument(
            "--timeline-html", action="store_true",
            help="Write an interactive HTML timeline to daw/<TAG>/<TAG>_timeline.html"
        )
        parser.add_argument(
            "--gap-ms", type=int, default=SILENCE_GAP_MS,
            help=f"Silence gap between foreground stems in ms (default: {SILENCE_GAP_MS})"
        )
        args = parser.parse_args()

        slug = resolve_slug(args.show)
        p = derive_paths(slug, args.episode)
        cast_path = p["cast"]
        with open(cast_path, encoding="utf-8") as f:
            cast_data = json.load(f)

        cast_cfg = CastConfiguration(**cast_data)
        tag = cast_cfg.tag
        config = {
            key: VoiceConfig(id=member.voice_id, pan=member.pan, filter=member.filter).model_dump()
            for key, member in cast_cfg.cast.items()
        }

        stems_dir = os.path.join(STEMS_DIR, tag)
        parsed_path = args.parsed or p["parsed"]
        output_dir = args.output_dir or os.path.join(DAW_DIR, tag)

        if not os.path.exists(parsed_path):
            print(f" [!] Parsed JSON not found: {parsed_path!r}. Run XILP001 first.")
            return

        sfx_path = p["sfx"]
        sfx_config = None
        if os.path.exists(sfx_path):
            with open(sfx_path, encoding="utf-8") as f:
                sfx_config = SfxConfiguration(**json.load(f))

        entries_index = load_entries_index(parsed_path)
        stem_plans = collect_stem_plans(stems_dir, entries_index, sfx_config=sfx_config)

        if args.dry_run:
            dry_run_daw(tag, stem_plans, entries_index, output_dir)
            if args.timeline or args.timeline_html:
                total_ms, timeline = build_foreground_timeline_only(
                    stem_plans, gap_ms=args.gap_ms
                )
                dlg_labels = compute_dialogue_labels(stem_plans, timeline)
                amb_labels = compute_ambience_labels(stem_plans, timeline, total_ms)
                mus_labels = compute_music_labels(stem_plans, timeline, total_ms)
                sfx_labels = compute_sfx_labels(stem_plans, timeline, total_ms)
                td = build_timeline_data(
                    tag, total_ms / 1000.0,
                    dlg_labels, amb_labels, mus_labels, sfx_labels,
                )
                if args.timeline:
                    print(render_terminal_timeline(td))
                if args.timeline_html:
                    html_path = os.path.join(output_dir, f"{tag}_timeline.html")
                    render_html_timeline(td, html_path)
                    print(f"    Written: {html_path}")
            return

        export_daw_layers(
            config, stems_dir, parsed_path, output_dir, tag,
            save_aup3=args.save_aup3,
            macro=args.macro,
            show=cast_cfg.show,
            season_title=cast_cfg.season_title,
            episode_title=cast_cfg.title,
            artist=cast_cfg.artist,
            timeline=args.timeline,
            timeline_html=args.timeline_html,
            sfx_config=sfx_config,
            gap_ms=args.gap_ms,
        )


if __name__ == "__main__":
    main()
