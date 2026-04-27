# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
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
import re
import subprocess
import textwrap

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.mix_common import (
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

logger = get_logger(__name__)

# Tags are used verbatim inside generated Python source — restrict to safe chars.
_SAFE_TAG_RE = re.compile(r'^[A-Za-z0-9_\-]+$')


def _validate_tag_for_script(tag: str) -> str:
    """Raise ValueError if *tag* contains characters that could escape a generated script."""
    if not _SAFE_TAG_RE.match(tag):
        raise ValueError(
            f"Tag {tag!r} contains characters that are not safe for script generation. "
            "Tags must match ^[A-Za-z0-9_-]+$ (letters, digits, hyphens, underscores only)."
        )
    return tag

STEMS_DIR = "stems"
DAW_DIR = "daw"
SILENCE_GAP_MS = 600

# Layer definitions: (key, filename_suffix, description)
LAYERS: list[tuple[str, str, str]] = [
    ("dialogue", "layer_dialogue", "Spoken dialogue (audio filter chain + pan applied per speaker)"),
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
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
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
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
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

    from xil_pipeline.models import DEFAULT_SLUG
    from xil_pipeline.models import show_slug as _show_slug
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
                except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
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
            except OSError as exc:
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


def dry_run_daw(
    tag: str,
    stem_plans,
    entries_index: dict,
    output_dir: str,
    stems_dir: str = "",
    sfx_config=None,
    cast_config: dict | None = None,
) -> None:
    """Print a DAW export summary without writing any files.

    Args:
        tag: Episode tag.
        stem_plans: Classified stem list.
        entries_index: Parsed entry index.
        output_dir: Target directory (shown in summary).
        stems_dir: Stems source directory (shown in summary).
        sfx_config: SfxConfiguration instance for vintage_scenes lookup.
        cast_config: Per-speaker voice settings dict for filter reporting.
    """
    _validate_tag_for_script(tag)
    bg_plans = [p for p in stem_plans if p.is_background]
    ambience = [p for p in bg_plans if p.direction_type == "AMBIENCE"]
    music = [p for p in bg_plans if p.direction_type == "MUSIC"]
    sfx = [p for p in stem_plans if p.direction_type in ("SFX", "BEAT")]
    dialogue = [p for p in stem_plans if p.entry_type == "dialogue"]

    vintage_scenes = sfx_config.vintage_scenes if sfx_config else []
    vintage_count = sum(1 for p in dialogue if p.scene in vintage_scenes) if vintage_scenes else 0

    logger.info(f"\n--- DAW Export Dry Run: {tag} ---")
    logger.info(f"   Stems directory : {stems_dir or f'stems/{tag}'}")
    logger.info(f"   Output directory: {output_dir}/")
    logger.info("")
    logger.info("   Layer             Stems")
    logger.info("   ─────────────────────────────")
    logger.info(f"   dialogue          {len(dialogue):3d} stems")
    if vintage_scenes:
        scenes_str = ", ".join(vintage_scenes)
        logger.info(f"     vintage scenes : {scenes_str}  ({vintage_count} stems — mono collapse + LPF 5kHz)")
    if cast_config:
        filtered = {k: v.get("filter") for k, v in cast_config.items() if v.get("filter")}
        if filtered:
            parts = "  ".join(f"{k}={v}" for k, v in filtered.items())
            logger.info(f"     per-speaker    : {parts}")
    logger.info(f"   ambience          {len(ambience):3d} stems  (looped to scene boundaries)")
    logger.info(f"   music             {len(music):3d} stems  (one-shot at cue points)")
    logger.info(f"   sfx               {len(sfx):3d} stems")
    logger.info("")
    logger.info("   Output files (all same duration as foreground track):")
    for _, suffix, desc in LAYERS:
        logger.info(f"     {output_dir}/{tag}_{suffix}.wav  — {desc}")
    logger.info(f"     {output_dir}/{tag}_open_in_audacity.py")
    logger.info("")


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
        show: Show name for audio metadata (default ``"Sample Show"``).
        season_title: Season title for metadata artist field.
        episode_title: Episode title for metadata.
    """
    _validate_tag_for_script(tag)
    entries_index = load_entries_index(parsed_path)
    stem_plans = collect_stem_plans(stems_dir, entries_index, sfx_config=sfx_config)

    if not stem_plans:
        logger.warning(f"No stems found in {stems_dir}/. Run XILP002 first.")
        return

    logger.info(f"--- Building foreground timeline from {len(stem_plans)} stems ---")
    vintage_scenes = sfx_config.vintage_scenes if sfx_config else []
    foreground, cue_timeline = build_foreground(
        stem_plans, config, gap_ms=gap_ms, vintage_scenes=vintage_scenes
    )

    if len(foreground) == 0:
        logger.warning("No foreground stems — cannot determine episode duration.")
        return

    total_ms = len(foreground)
    logger.info(f"    Episode duration: {total_ms / 1000:.1f}s")

    os.makedirs(output_dir, exist_ok=True)

    layer_files: list[tuple[str, str]] = []

    # --- Dialogue layer ---
    logger.info("--- Building dialogue layer ---")
    dialogue_plans = [p for p in stem_plans if p.entry_type == "dialogue"]
    vintage_count = sum(1 for p in dialogue_plans if p.scene in vintage_scenes) if vintage_scenes else 0
    if vintage_scenes:
        scenes_str = ", ".join(vintage_scenes)
        logger.info(f"    vintage scenes : {scenes_str}  ({vintage_count} stems — mono collapse + LPF 5kHz)")
    speaker_filters = {k: v.get("filter") for k, v in config.items() if v.get("filter")}
    if speaker_filters:
        for speaker, fval in speaker_filters.items():
            logger.info(f"    per-speaker    : {speaker} → {fval}")
    dlg, labels = build_dialogue_layer(
        stem_plans, cue_timeline, total_ms, config, vintage_scenes=vintage_scenes
    )
    fname = f"{tag}_layer_dialogue.wav"
    wav_path = os.path.join(output_dir, fname)
    dlg.export(wav_path, format="wav")
    tag_wav(wav_path, show=show, title=f"{tag} Dialogue", artist=artist)
    layer_files.append(("Dialogue", fname))
    logger.info(f"    Written: {output_dir}/{fname}")

    # --- Dialogue label track ---
    _write_labels(output_dir, f"{tag}_labels_dialogue.txt", labels)
    layer_files.append(("Labels (Dialogue)", f"{tag}_labels_dialogue.txt"))
    logger.info(f"    Written: {output_dir}/{tag}_labels_dialogue.txt")

    # --- Ambience layer ---
    logger.info("--- Building ambience layer ---")
    for plan in sorted(stem_plans, key=lambda p: p.seq):
        if plan.direction_type != "AMBIENCE" or not plan.filepath:
            continue
        pd_str = f"{plan.play_duration:.1f}%" if plan.play_duration is not None else "100% (full)"
        vol_str = f"{plan.volume_percentage:.0f}%" if plan.volume_percentage is not None else "unity"
        ri_str = f"{plan.ramp_in_seconds:.1f}s" if plan.ramp_in_seconds is not None else "none"
        ro_str = f"{plan.ramp_out_seconds:.1f}s" if plan.ramp_out_seconds is not None else "none"
        logger.info("    seq %d: vol=%s  trim=%s  ramp_in=%s  ramp_out=%s  %s",
                    plan.seq, vol_str, pd_str, ri_str, ro_str, os.path.basename(plan.filepath))
    amb, amb_labels = build_ambience_layer(stem_plans, cue_timeline, total_ms, level_db=0)
    fname = f"{tag}_layer_ambience.wav"
    wav_path = os.path.join(output_dir, fname)
    amb.export(wav_path, format="wav")
    tag_wav(wav_path, show=show, title=f"{tag} Ambience", artist=artist)
    layer_files.append(("Ambience", fname))
    logger.info(f"    Written: {output_dir}/{fname}")
    _write_labels(output_dir, f"{tag}_labels_ambience.txt", amb_labels)
    layer_files.append(("Labels (Ambience)", f"{tag}_labels_ambience.txt"))
    logger.info(f"    Written: {output_dir}/{tag}_labels_ambience.txt")

    # --- Music layer ---
    logger.info("--- Building music layer ---")
    for plan in sorted(stem_plans, key=lambda p: p.seq):
        if plan.direction_type != "MUSIC" or not plan.filepath:
            continue
        pd_str = f"{plan.play_duration:.1f}%" if plan.play_duration is not None else "100% (full)"
        vol_str = f"{plan.volume_percentage:.0f}%" if plan.volume_percentage is not None else "unity"
        ro_str = f"{plan.ramp_out_seconds:.1f}s" if plan.ramp_out_seconds is not None else "none"
        logger.info("    seq %d: vol=%s  trim=%s  ramp_out=%s  %s",
                    plan.seq, vol_str, pd_str, ro_str, os.path.basename(plan.filepath))
    mus, mus_labels = build_music_layer(
        stem_plans, cue_timeline, total_ms, level_db=0,
        include_foreground_override=True,  # show preamble/postamble music in DAW layer
    )
    fname = f"{tag}_layer_music.wav"
    wav_path = os.path.join(output_dir, fname)
    mus.export(wav_path, format="wav")
    tag_wav(wav_path, show=show, title=f"{tag} Music", artist=artist)
    layer_files.append(("Music", fname))
    logger.info(f"    Written: {output_dir}/{fname}")
    _write_labels(output_dir, f"{tag}_labels_music.txt", mus_labels)
    layer_files.append(("Labels (Music)", f"{tag}_labels_music.txt"))
    logger.info(f"    Written: {output_dir}/{tag}_labels_music.txt")

    # --- SFX layer ---
    logger.info("--- Building SFX layer ---")
    sfx, sfx_labels = build_sfx_layer(stem_plans, cue_timeline, total_ms)
    fname = f"{tag}_layer_sfx.wav"
    wav_path = os.path.join(output_dir, fname)
    sfx.export(wav_path, format="wav")
    tag_wav(wav_path, show=show, title=f"{tag} SFX", artist=artist)
    layer_files.append(("SFX", fname))
    logger.info(f"    Written: {output_dir}/{fname}")
    _write_labels(output_dir, f"{tag}_labels_sfx.txt", sfx_labels)
    layer_files.append(("Labels (SFX)", f"{tag}_labels_sfx.txt"))
    logger.info(f"    Written: {output_dir}/{tag}_labels_sfx.txt")

    # --- Audacity helper script ---
    script_fname = f"{tag}_open_in_audacity.py"
    script_path = os.path.join(output_dir, script_fname)
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(_make_audacity_script(tag, layer_files, save_aup3=save_aup3, show=show))
    os.chmod(script_path, 0o755)
    logger.info(f"    Written: {output_dir}/{script_fname}")

    # --- Audacity macro (optional) ---
    if macro:
        macro_path = generate_audacity_macro(
            output_dir, tag, layer_files,
            show=show, season_title=season_title, episode_title=episode_title,
            artist=artist,
        )
        if macro_path:
            logger.info(f"    Written: {macro_path}")
        else:
            logger.warning("Audacity Macros directory not found — macro not written.")

    # --- Timeline visualization (optional) ---
    if timeline or timeline_html:
        dlg_labels = compute_dialogue_labels(stem_plans, cue_timeline)
        td = build_timeline_data(
            tag, total_ms / 1000.0,
            dlg_labels, amb_labels, mus_labels, sfx_labels,
        )
        if timeline:
            print(render_terminal_timeline(td))
        if timeline_html:
            html_path = os.path.join(output_dir, f"{tag}_timeline.html")
            render_html_timeline(td, html_path, stems_dir=stems_dir)
            logger.info(f"    Written: {html_path}")

    logger.info("")
    logger.info(f"--- Done! {len(layer_files)} layer WAVs in {output_dir}/ ---")
    logger.info(f"    Import into Audacity: python {output_dir}/{script_fname}")
    if macro:
        from xil_pipeline.models import DEFAULT_SLUG
        from xil_pipeline.models import show_slug as _show_slug
        macro_label = _show_slug(show).upper() if show else DEFAULT_SLUG.upper()
        logger.info(f"    Audacity macro:       Tools → Macros → {macro_label}_{tag} → Apply to Project")
    if save_aup3:
        logger.info(f"    Will save project:    {output_dir}/{tag}.aup3")


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-daw",
        description="DAW Export — export episode as layered WAV files for Audacity",
    )
    tag_group = parser.add_mutually_exclusive_group(required=True)
    tag_group.add_argument("--episode",
                           help="Episode tag (e.g. S01E02) — derives cast config, stems, and parsed JSON paths")
    tag_group.add_argument("--tag",
                           help="Raw tag for non-episodic content (e.g. V01C03, D01)")
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
    return parser


def main() -> None:
    """CLI entry point for DAW layer export.

    Loads cast config, derives stem and parsed JSON paths from the
    episode tag, builds four per-layer WAV files and an Audacity helper
    script.  No ElevenLabs API key required.
    """
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        tag = args.episode or args.tag
        slug = resolve_slug(args.show)
        p = derive_paths(slug, tag)
        cast_path = p["cast"]
        if not os.path.exists(cast_path):
            logger.error(f"Cast config not found: {cast_path}")
            logger.info("Run XILP001 first or check your --episode flag.")
            return
        with open(cast_path, encoding="utf-8") as f:
            cast_data = json.load(f)

        cast_cfg = CastConfiguration(**cast_data)
        tag = cast_cfg.tag
        config = {
            key: VoiceConfig(id=member.voice_id, pan=member.pan, filter=member.filter).model_dump()
            for key, member in cast_cfg.cast.items()
        }

        stems_dir = os.path.join(STEMS_DIR, slug, tag)
        parsed_path = args.parsed or p["parsed"]
        output_dir = args.output_dir or p["daw"]

        if not os.path.exists(parsed_path):
            logger.warning(f"Parsed JSON not found: {parsed_path!r}. Run XILP001 first.")
            return

        sfx_path = p["sfx"]
        sfx_config = None
        if os.path.exists(sfx_path):
            with open(sfx_path, encoding="utf-8") as f:
                sfx_config = SfxConfiguration(**json.load(f))

        entries_index = load_entries_index(parsed_path)
        stem_plans = collect_stem_plans(stems_dir, entries_index, sfx_config=sfx_config)

        if args.dry_run:
            dry_run_daw(tag, stem_plans, entries_index, output_dir, stems_dir,
                        sfx_config=sfx_config, cast_config=config)
            if args.timeline or args.timeline_html:
                total_ms, timeline = build_foreground_timeline_only(
                    stem_plans, gap_ms=args.gap_ms
                )
                dlg_labels = compute_dialogue_labels(stem_plans, timeline)
                amb_labels = compute_ambience_labels(stem_plans, timeline, total_ms)
                mus_labels = compute_music_labels(
                    stem_plans, timeline, total_ms,
                    include_foreground_override=True,
                )
                sfx_labels = compute_sfx_labels(stem_plans, timeline, total_ms)
                td = build_timeline_data(
                    tag, total_ms / 1000.0,
                    dlg_labels, amb_labels, mus_labels, sfx_labels,
                )
                if args.timeline:
                    print(render_terminal_timeline(td))
                if args.timeline_html:
                    html_path = os.path.join(output_dir, f"{tag}_timeline.html")
                    render_html_timeline(td, html_path, stems_dir=stems_dir)
                    logger.info(f"    Written: {html_path}")
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
