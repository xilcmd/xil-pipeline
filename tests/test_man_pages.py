# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for man page infrastructure.

Verifies that:
- All 18 argparse-based modules expose a get_parser() callable.
- Each get_parser() returns an argparse.ArgumentParser.
- Each parser's prog attribute matches the installed entry-point name.
- All 19 committed .1 files exist in man/man1/.
- Each .1 file starts with a .TH header (valid troff).

No argparse-manpage dependency required to run these tests.
"""

import argparse
import importlib
from pathlib import Path

import pytest

# Repo root is two directories above this test file (tests/test_man_pages.py)
REPO_ROOT = Path(__file__).parent.parent
MAN_DIR = REPO_ROOT / "man" / "man1"

# (entry-point-name, importable-module-path)
ARGPARSE_COMMANDS: list[tuple[str, str]] = [
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
]

# All committed man pages (18 generated + 1 hand-crafted dispatcher)
ALL_MAN_PAGES: list[str] = ["xil"] + [cmd for cmd, _ in ARGPARSE_COMMANDS]


@pytest.mark.parametrize("cmd_name,mod_path", ARGPARSE_COMMANDS, ids=[c for c, _ in ARGPARSE_COMMANDS])
def test_get_parser_exists(cmd_name: str, mod_path: str) -> None:
    """Each module must expose a callable get_parser attribute."""
    mod = importlib.import_module(mod_path)
    assert hasattr(mod, "get_parser"), f"{mod_path} has no get_parser()"
    assert callable(mod.get_parser), f"{mod_path}.get_parser is not callable"


@pytest.mark.parametrize("cmd_name,mod_path", ARGPARSE_COMMANDS, ids=[c for c, _ in ARGPARSE_COMMANDS])
def test_get_parser_returns_argument_parser(cmd_name: str, mod_path: str) -> None:
    """get_parser() must return an argparse.ArgumentParser instance."""
    mod = importlib.import_module(mod_path)
    parser = mod.get_parser()
    assert isinstance(parser, argparse.ArgumentParser), (
        f"{mod_path}.get_parser() returned {type(parser).__name__}, expected ArgumentParser"
    )


@pytest.mark.parametrize("cmd_name,mod_path", ARGPARSE_COMMANDS, ids=[c for c, _ in ARGPARSE_COMMANDS])
def test_get_parser_prog_matches_entry_point(cmd_name: str, mod_path: str) -> None:
    """parser.prog must match the CLI entry-point name (e.g. 'xil-parse')."""
    mod = importlib.import_module(mod_path)
    parser = mod.get_parser()
    assert parser.prog == cmd_name, (
        f"{mod_path}.get_parser().prog = {parser.prog!r}, expected {cmd_name!r}"
    )


@pytest.mark.parametrize("page_name", ALL_MAN_PAGES)
def test_man1_file_exists(page_name: str) -> None:
    """Each committed man page file must exist in man/man1/."""
    path = MAN_DIR / f"{page_name}.1"
    assert path.exists(), f"Missing man page: {path}"


@pytest.mark.parametrize("page_name", ALL_MAN_PAGES)
def test_man1_file_has_th_header(page_name: str) -> None:
    """Each .1 file must start with a .TH macro (valid troff header)."""
    path = MAN_DIR / f"{page_name}.1"
    if not path.exists():
        pytest.skip(f"{path} does not exist (covered by test_man1_file_exists)")
    content = path.read_text(encoding="utf-8")
    assert content.startswith(".TH "), (
        f"{path.name} does not start with '.TH ' — not a valid troff man page"
    )
