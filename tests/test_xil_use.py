# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for xil_use — active show context switcher."""

import argparse
import json

import pytest

from xil_pipeline import xil_use


def _make_show(root, slug, name):
    """Create configs/{slug}/project.json declaring *name*."""
    d = root / "configs" / slug
    d.mkdir(parents=True)
    (d / "project.json").write_text(json.dumps({"show": name}), encoding="utf-8")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    return tmp_path


def _run(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["xil-use", *argv])
    return xil_use.main()


class TestGetParser:
    def test_returns_argument_parser(self):
        assert isinstance(xil_use.get_parser(), argparse.ArgumentParser)

    def test_prog_matches_entry_point(self):
        assert xil_use.get_parser().prog == "xil-use"

    def test_show_positional_accepts_multiple_words(self):
        args = xil_use.get_parser().parse_args(["Night", "Owls"])
        assert args.show == ["Night", "Owls"]

    def test_show_positional_optional(self):
        assert xil_use.get_parser().parse_args([]).show == []


class TestListMode:
    def test_no_shows_returns_zero(self, workspace, monkeypatch):
        assert _run(monkeypatch) == 0

    def test_lists_shows_returns_zero(self, workspace, monkeypatch):
        _make_show(workspace, "the413", "THE 413")
        _make_show(workspace, "nightowls", "Night Owls")
        assert _run(monkeypatch) == 0

    def test_does_not_change_active_show(self, workspace, monkeypatch):
        _make_show(workspace, "the413", "THE 413")
        (workspace / ".active_show").write_text("the413", encoding="utf-8")
        _run(monkeypatch)
        assert (workspace / ".active_show").read_text(encoding="utf-8") == "the413"


class TestSetMode:
    def test_match_by_slug(self, workspace, monkeypatch):
        _make_show(workspace, "the413", "THE 413")
        assert _run(monkeypatch, "the413") == 0
        assert (workspace / ".active_show").read_text(encoding="utf-8") == "the413"

    def test_match_by_show_name(self, workspace, monkeypatch):
        _make_show(workspace, "the413", "THE 413")
        assert _run(monkeypatch, "THE 413") == 0
        assert (workspace / ".active_show").read_text(encoding="utf-8") == "the413"

    def test_match_by_unquoted_multiword_name(self, workspace, monkeypatch):
        _make_show(workspace, "nightowls", "Night Owls")
        assert _run(monkeypatch, "Night", "Owls") == 0
        assert (workspace / ".active_show").read_text(encoding="utf-8") == "nightowls"

    def test_no_match_returns_one(self, workspace, monkeypatch):
        _make_show(workspace, "the413", "THE 413")
        assert _run(monkeypatch, "nosuchshow") == 1
        assert not (workspace / ".active_show").exists()
