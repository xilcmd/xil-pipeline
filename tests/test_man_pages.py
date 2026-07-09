# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for man page infrastructure.

The command registry is imported from docs/build_man.py (single source of
truth) and cross-checked against pyproject.toml [project.scripts], so a new
CLI entry point without man-page registration is a test failure.

Verifies that:
- Every registered module exposes a get_parser() returning ArgumentParser
  whose prog matches the entry-point name.
- Every [project.scripts] entry point has a committed man/man1/<name>.1 page.
- Every entry point is registered in build_man COMMANDS or HAND_CRAFTED,
  and every COMMANDS entry is a real entry point (bidirectional).
- Each .1 file starts with a .TH header (valid troff).

No argparse-manpage dependency required to run these tests.
"""

import argparse
import importlib
import importlib.util
import tomllib
from pathlib import Path

import pytest

# Repo root is two directories above this test file (tests/test_man_pages.py)
REPO_ROOT = Path(__file__).parent.parent
MAN_DIR = REPO_ROOT / "man" / "man1"

# docs/build_man.py is not an installed module — load it by path
_spec = importlib.util.spec_from_file_location(
    "build_man", REPO_ROOT / "docs" / "build_man.py"
)
build_man = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_man)

COMMANDS: list[tuple[str, str]] = build_man.COMMANDS
HAND_CRAFTED: set[str] = build_man.HAND_CRAFTED
ALL_MAN_PAGES: list[str] = sorted(HAND_CRAFTED) + [cmd for cmd, _ in COMMANDS]


def _project_scripts() -> dict[str, str]:
    """Return the [project.scripts] table from pyproject.toml."""
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["scripts"]


@pytest.mark.parametrize("cmd_name,mod_path", COMMANDS, ids=[c for c, _ in COMMANDS])
def test_get_parser_exists(cmd_name: str, mod_path: str) -> None:
    """Each module must expose a callable get_parser attribute."""
    mod = importlib.import_module(mod_path)
    assert hasattr(mod, "get_parser"), f"{mod_path} has no get_parser()"
    assert callable(mod.get_parser), f"{mod_path}.get_parser is not callable"


@pytest.mark.parametrize("cmd_name,mod_path", COMMANDS, ids=[c for c, _ in COMMANDS])
def test_get_parser_returns_argument_parser(cmd_name: str, mod_path: str) -> None:
    """get_parser() must return an argparse.ArgumentParser instance."""
    mod = importlib.import_module(mod_path)
    parser = mod.get_parser()
    assert isinstance(parser, argparse.ArgumentParser), (
        f"{mod_path}.get_parser() returned {type(parser).__name__}, expected ArgumentParser"
    )


@pytest.mark.parametrize("cmd_name,mod_path", COMMANDS, ids=[c for c, _ in COMMANDS])
def test_get_parser_prog_matches_entry_point(cmd_name: str, mod_path: str) -> None:
    """parser.prog must match the CLI entry-point name (e.g. 'xil-parse')."""
    mod = importlib.import_module(mod_path)
    parser = mod.get_parser()
    assert parser.prog == cmd_name, (
        f"{mod_path}.get_parser().prog = {parser.prog!r}, expected {cmd_name!r}"
    )


@pytest.mark.parametrize("page_name", ALL_MAN_PAGES)
def test_man1_file_exists(page_name: str) -> None:
    """Each registered man page file must exist in man/man1/."""
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


class TestEntryPointCoverage:
    """Every console script must be man-paged and registered — no drift."""

    def test_every_entry_point_has_man_page(self) -> None:
        missing = [n for n in _project_scripts() if not (MAN_DIR / f"{n}.1").exists()]
        assert not missing, f"Entry points without a man page: {missing}"

    def test_every_entry_point_is_registered(self) -> None:
        registered = {c for c, _ in COMMANDS} | HAND_CRAFTED
        unregistered = [n for n in _project_scripts() if n not in registered]
        assert not unregistered, (
            f"Entry points not in build_man COMMANDS/HAND_CRAFTED: {unregistered}"
        )

    def test_every_command_is_an_entry_point(self) -> None:
        scripts = _project_scripts()
        stale = [c for c, _ in COMMANDS if c not in scripts]
        assert not stale, f"COMMANDS entries with no [project.scripts] entry point: {stale}"

    def test_command_modules_match_entry_points(self) -> None:
        scripts = _project_scripts()
        mismatched = [
            (c, m, scripts[c])
            for c, m in COMMANDS
            if c in scripts and not scripts[c].startswith(f"{m}:")
        ]
        assert not mismatched, f"COMMANDS module differs from entry point: {mismatched}"
