#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Generate man/man1/*.1 troff man pages for all argparse-based xil commands.

Each module must expose a ``get_parser()`` function that returns a fully
configured ``argparse.ArgumentParser``.  The ``xil`` dispatcher page is
hand-crafted (man/man1/xil.1) and is never overwritten by this script.

Usage::

    python docs/build_man.py               # regenerate all registered pages
    python docs/build_man.py xil-parse     # regenerate one page
    python docs/build_man.py --check       # exit 1 if any file is stale

The ``--check`` comparison ignores the date field on the ``.TH`` line, so it
is stable across days (argparse-manpage stamps the current date unless
``SOURCE_DATE_EPOCH`` is set).

Requirements::

    pip install argparse-manpage>=4.6,<5

(Included in the ``dev`` optional-dependency group:
``pip install -e ".[dev]"``)
"""

import argparse
import importlib
import re
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
    ("xil-assemble",       "xil_pipeline.XILP003_audio_assembly"),
    ("xil-studio-onboard", "xil_pipeline.XILP004_studio_onboard"),
    ("xil-daw",            "xil_pipeline.XILP005_daw_export"),
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
    ("xil-migrate-workspace", "xil_pipeline.XILU009_migrate_workspace"),
    ("xil-remove-show", "xil_pipeline.XILU017_remove_show"),
    ("xil-remove-episode", "xil_pipeline.XILU018_remove_episode"),
    ("xil-status", "xil_pipeline.XILU019_episode_status"),
    ("xil-sfx-restore", "xil_pipeline.XILU020_sfx_restore"),
    ("xil-sfx-impact", "xil_pipeline.XILU021_sfx_impact"),
    ("xil-db-profile",       "xil_pipeline.XILU010_db_profile"),
    ("xil-sfx-csv",          "xil_pipeline.XILU011_sfx_csv"),
    ("xil-parsed-csv",       "xil_pipeline.XILU012_parsed_csv"),
    ("xil-sfx-hydrate",      "xil_pipeline.XILU013_sfx_hydrate"),
    ("xil-episode-summary",  "xil_pipeline.XILU014_episode_summary"),
    ("xil-stem-verify",      "xil_pipeline.XILU015_stem_verify"),
    ("xil-stem-compare",     "xil_pipeline.XILU016_stem_compare"),
    ("xil-publish",          "xil_pipeline.XILP012_publish"),
    ("xil-gui",              "xil_pipeline.xil_gui"),
    ("xil-use",              "xil_pipeline.xil_use"),
]

def _see_also_block(current: str) -> str:
    """Return the SEE ALSO + AUTHOR troff block, derived from COMMANDS.

    Lists ``xil`` plus every registered command except *current* (a page
    should not reference itself), so the block can never drift from the
    COMMANDS registry.
    """
    names = ["xil"] + [n for n, _ in COMMANDS if n != current]
    refs = [f".BR {n} (1)," for n in names]
    refs[-1] = refs[-1].rstrip(",")
    lines = [".SH SEE ALSO", *refs, ".SH AUTHOR", "John Brissette <xilcmd@gmail.com>"]
    return "\n" + "\n".join(lines) + "\n"


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
    return str(mp) + _see_also_block(cmd_name)


_TH_DATE_RE = re.compile(r'^(\.TH .*?")\d{4}\\?-\d{2}\\?-\d{2}(")', flags=re.MULTILINE)


def _mask_th_date(troff: str) -> str:
    """Return *troff* with the date field of any ``.TH`` line masked.

    argparse-manpage stamps the current date into ``.TH`` unless
    ``SOURCE_DATE_EPOCH`` is set; masking keeps ``--check`` stable across days.
    """
    return _TH_DATE_RE.sub(r"\1DATE\2", troff)


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
            on_disk = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
            if _mask_th_date(on_disk) == _mask_th_date(troff):
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
        print("Run:  python docs/build_man.py", file=sys.stderr)
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
