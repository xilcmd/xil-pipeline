#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Generate man/man1/*.1 troff man pages for all argparse-based xil commands.

Each module must expose a ``get_parser()`` function that returns a fully
configured ``argparse.ArgumentParser``.  The ``xil`` dispatcher page is
hand-crafted (man/man1/xil.1) and is never overwritten by this script.

Usage::

    python scripts/build_man.py               # regenerate all 20 pages
    python scripts/build_man.py xil-parse     # regenerate one page
    python scripts/build_man.py --check       # exit 1 if any file is stale

Requirements::

    pip install argparse-manpage>=4.6,<5

(Included in the ``dev`` optional-dependency group:
``pip install -e ".[dev]"``)
"""

import argparse
import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MAN_DIR = REPO_ROOT / "man" / "man1"
HAND_CRAFTED = {"xil"}  # pages managed manually — never overwritten

# Ordered list of (entry-point-name, importable-module-path)
COMMANDS: list[tuple[str, str]] = [
    ("xil-init",     "xil_pipeline.xil_init"),
    ("xil-scan",     "xil_pipeline.XILP000_script_scanner"),
    ("xil-parse",    "xil_pipeline.XILP001_script_parser"),
    ("xil-cues",     "xil_pipeline.XILP006_cues_ingester"),
    ("xil-produce",  "xil_pipeline.XILP002_producer"),
    ("xil-assemble", "xil_pipeline.XILP003_audio_assembly"),
    ("xil-daw",      "xil_pipeline.XILP005_daw_export"),
    ("xil-migrate",  "xil_pipeline.XILP007_stem_migrator"),
    ("xil-cleanup",  "xil_pipeline.XILP008_stale_stem_cleanup"),
    ("xil-import",   "xil_pipeline.XILP010_studio_import"),
    ("xil-regen",    "xil_pipeline.XILP009_script_regenerator"),
    ("xil-master",   "xil_pipeline.XILP011_master_export"),
    ("xil-voices",   "xil_pipeline.XILU001_discover_voices_T2S"),
    ("xil-csv-join", "xil_pipeline.XILU003_csv_sfx_join"),
    ("xil-sfx",      "xil_pipeline.XILU002_generate_SFX"),
    ("xil-sample",   "xil_pipeline.XILU004_sample_voices_T2S"),
    ("xil-sfx-lib",  "xil_pipeline.XILU005_discover_SFX"),
    ("xil-splice",   "xil_pipeline.XILU006_splice_parsed"),
    ("xil-mp3-hash", "xil_pipeline.XILU007_mp3_hash"),
    ("xil-stem-log", "xil_pipeline.XILU008_stem_log_report"),
]

SEE_ALSO_LINES = [
    ".SH SEE ALSO",
    ".BR xil (1),",
    ".BR xil-scan (1),",
    ".BR xil-parse (1),",
    ".BR xil-cues (1),",
    ".BR xil-produce (1),",
    ".BR xil-assemble (1),",
    ".BR xil-daw (1),",
    ".BR xil-migrate (1),",
    ".BR xil-cleanup (1),",
    ".BR xil-import (1),",
    ".BR xil-regen (1),",
    ".BR xil-master (1),",
    ".BR xil-voices (1),",
    ".BR xil-csv-join (1),",
    ".BR xil-sfx (1),",
    ".BR xil-sample (1),",
    ".BR xil-sfx-lib (1),",
    ".BR xil-splice (1),",
    ".BR xil-mp3-hash (1),",
    ".BR xil-init (1)",
    ".SH AUTHOR",
    "John Brissette <xilcmd@gmail.com>",
]

SEE_ALSO_BLOCK = "\n" + "\n".join(SEE_ALSO_LINES) + "\n"


def _import_manpage():
    """Import argparse_manpage, with a friendly error if not installed."""
    try:
        from argparse_manpage.manpage import Manpage  # type: ignore[import]
        return Manpage
    except ImportError:
        print(
            "ERROR: argparse-manpage is not installed.\n"
            "Install it with:  pip install argparse-manpage>=4.6,<5\n"
            "Or:               pip install -e '.[dev]'",
            file=sys.stderr,
        )
        sys.exit(1)


def _get_version() -> str:
    try:
        import xil_pipeline
        return xil_pipeline.__version__
    except Exception:
        return "0.0.0"


def generate_one(cmd_name: str, mod_path: str, Manpage) -> str:  # noqa: N803
    """Import module, call get_parser(), return troff string."""
    mod = importlib.import_module(mod_path)
    if not hasattr(mod, "get_parser"):
        raise AttributeError(f"{mod_path} does not expose get_parser()")
    parser = mod.get_parser()

    mp = Manpage(parser)
    mp.source = f"xil-pipeline {_get_version()}"
    mp.manual = "User Commands"
    mp.section = 1
    return str(mp) + SEE_ALSO_BLOCK


def build(target: str | None, check: bool) -> int:
    """Generate man pages.  Returns exit code (0 = success, 1 = stale/error)."""
    Manpage = _import_manpage()
    MAN_DIR.mkdir(parents=True, exist_ok=True)

    commands = COMMANDS
    if target is not None:
        commands = [(n, m) for n, m in COMMANDS if n == target]
        if not commands:
            print(f"ERROR: unknown command {target!r}", file=sys.stderr)
            print(f"Valid commands: {', '.join(n for n, _ in COMMANDS)}", file=sys.stderr)
            return 1

    # Warn if xil.1 is missing (it's hand-crafted, not generated)
    xil_page = MAN_DIR / "xil.1"
    if not xil_page.exists():
        print(f"WARNING: hand-crafted {xil_page} not found — create it manually.", file=sys.stderr)

    stale: list[str] = []
    errors: list[str] = []

    for cmd_name, mod_path in commands:
        out_path = MAN_DIR / f"{cmd_name}.1"
        try:
            troff = generate_one(cmd_name, mod_path, Manpage)
        except Exception as exc:
            msg = f"ERROR generating {cmd_name}: {exc}"
            print(msg, file=sys.stderr)
            errors.append(msg)
            continue

        if check:
            if out_path.exists() and out_path.read_text(encoding="utf-8") == troff:
                print(f"OK       {out_path.name}")
            else:
                print(f"STALE    {out_path.name}")
                stale.append(cmd_name)
        else:
            out_path.write_text(troff, encoding="utf-8")
            print(f"Generated {out_path}")

    if errors:
        return 1
    if check and stale:
        print(f"\n{len(stale)} stale page(s): {', '.join(stale)}", file=sys.stderr)
        print("Run:  python scripts/build_man.py", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate man/man1/*.1 man pages for all argparse-based xil commands.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default=None,
        metavar="COMMAND",
        help="Regenerate only this command (e.g. xil-parse). Default: all.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if any committed .1 file is out of date (for CI).",
    )
    args = parser.parse_args()
    sys.exit(build(args.command, args.check))


if __name__ == "__main__":
    main()
