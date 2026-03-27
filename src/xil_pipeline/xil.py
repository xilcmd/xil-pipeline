# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unified CLI dispatcher for xil-pipeline commands.

This command keeps existing `xil-*` entry points intact while providing a
single ergonomic top-level command:

    xil <subcommand> [args...]
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from typing import TextIO


_PIPELINE = "pipeline"
_UTILITY = "utility"


@dataclass(frozen=True)
class CommandSpec:
    module: str
    description: str
    group: str
    hint: str = ""


# Dict insertion order defines the display order within each group.
XIL_SCRIPT_COMMANDS: dict[str, CommandSpec] = {
    "init": CommandSpec("xil_pipeline.xil_init", "workspace scaffolding", _PIPELINE),
    "scan": CommandSpec("xil_pipeline.XILP000_script_scanner", "pre-flight script scanner", _PIPELINE),
    "parse": CommandSpec("xil_pipeline.XILP001_script_parser", "script parser", _PIPELINE),
    "cues": CommandSpec("xil_pipeline.XILP006_cues_ingester", "cues sheet ingestion", _PIPELINE),
    "produce": CommandSpec("xil_pipeline.XILP002_producer", "voice stem generation", _PIPELINE),
    "assemble": CommandSpec("xil_pipeline.XILP003_audio_assembly", "master audio assembly", _PIPELINE),
    "daw": CommandSpec("xil_pipeline.XILP005_daw_export", "DAW layer export", _PIPELINE),
    "migrate": CommandSpec("xil_pipeline.XILP007_stem_migrator", "stem migration", _PIPELINE),
    "cleanup": CommandSpec("xil_pipeline.XILP008_stale_stem_cleanup", "stale stem cleanup", _PIPELINE),
    "studio": CommandSpec("xil_pipeline.XILP004_studio_onboard", "ElevenLabs Studio onboarding", _PIPELINE),
    "import": CommandSpec("xil_pipeline.XILP010_studio_import", "Studio export import", _PIPELINE),
    "regen": CommandSpec("xil_pipeline.XILP009_script_regenerator", "script regeneration", _PIPELINE),
    "master": CommandSpec("xil_pipeline.XILP011_master_export", "final master MP3 export", _PIPELINE),
    "voices": CommandSpec(
        "xil_pipeline.XILU001_discover_voices_T2S",
        "voice discovery",
        _UTILITY,
        "(before parse/produce)",
    ),
    "csv-join": CommandSpec(
        "xil_pipeline.XILU003_csv_sfx_join",
        "CSV + SFX/cast annotation join",
        _UTILITY,
        "(after parse)",
    ),
    "sfx": CommandSpec(
        "xil_pipeline.XILU002_generate_SFX",
        "standalone SFX generation",
        _UTILITY,
        "(after cues/parse)",
    ),
    "sample": CommandSpec(
        "xil_pipeline.XILU004_sample_voices_T2S",
        "voice sample generation",
        _UTILITY,
        "(after voices/cast config)",
    ),
    "sfx-lib": CommandSpec(
        "xil_pipeline.XILU005_discover_SFX",
        "SFX library discovery",
        _UTILITY,
        "(any time)",
    ),
    "splice": CommandSpec(
        "xil_pipeline.XILU006_splice_parsed",
        "parsed JSON splice utility",
        _UTILITY,
        "(advanced)",
    ),
}
"""Subcommand registry. Insertion order defines display order within each group."""


def _print_help(stream: TextIO) -> None:
    print("Usage: xil <command> [args...]", file=stream)
    print("", file=stream)
    width = max(len(name) for name in XIL_SCRIPT_COMMANDS)
    for group_label, group_key in (("Pipeline Stages (recommended order):", _PIPELINE), ("Utilities:", _UTILITY)):
        print(group_label, file=stream)
        for name, spec in XIL_SCRIPT_COMMANDS.items():
            if spec.group != group_key:
                continue
            suffix = f"  {spec.hint}" if spec.hint else ""
            print(f"  {name:<{width}}  {spec.description}{suffix}", file=stream)
        print("", file=stream)
    print("Run 'xil <command> --help' for command-specific options.", file=stream)


def _normalize_exit_code(code: object) -> int:
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    print(code, file=sys.stderr)
    return 1


def run_subcommand(command: str, args: list[str]) -> int:
    spec = XIL_SCRIPT_COMMANDS[command]
    module = importlib.import_module(spec.module)
    main_fn = getattr(module, "main", None)
    if main_fn is None:
        print(f"Command module has no main(): {spec.module}", file=sys.stderr)
        return 1

    original_argv = sys.argv[:]
    sys.argv = [f"xil {command}", *args]
    try:
        try:
            result = main_fn()
        except SystemExit as exc:
            return _normalize_exit_code(exc.code)
        return _normalize_exit_code(result)
    finally:
        sys.argv = original_argv


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv or argv[0] in {"-h", "--help", "help"}:
        _print_help(sys.stdout)
        return 0

    if argv[0] in {"-V", "--version", "version"}:
        from xil_pipeline import __version__

        print(__version__)
        return 0

    command = argv[0]
    if command not in XIL_SCRIPT_COMMANDS:
        print(f"Unknown command: {command}", file=sys.stderr)
        _print_help(sys.stderr)
        return 2

    return run_subcommand(command, argv[1:])


if __name__ == "__main__":
    main()
