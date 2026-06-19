# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for xil-status (XILU019_episode_status) — make-style staleness checker.

A tiny fake workspace is built under ``tmp_path`` with files ``touch``ed in
controlled mtime order via ``os.utime`` so staleness is deterministic, then the
evaluator/CLI are exercised directly (in-process) rather than via subprocess.
"""

from __future__ import annotations

import json
import os

import pytest

from xil_pipeline import XILU019_episode_status as status

# ── Fixtures ──────────────────────────────────────────────────────────────────

_SLUG = "testpodcast"
_TAG = "S01E01"


def _touch(path, mtime):
    """Create *path* (with parents) and stamp it to epoch second *mtime*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A fresh workspace rooted at tmp_path with XIL_PROJECTROOT pointing at it.

    ``get_workspace_root`` reads ``XIL_PROJECTROOT`` live on each call, so simply
    setting the env var is enough to isolate the workspace per test.
    """
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    return tmp_path


def _build_fresh(root, *, base=1000):
    """Create a full, in-order (fresh) chain. Each stage newer than the last."""
    _touch(root / "scripts" / f"{_TAG}_{_SLUG}_pilot.md", base + 1)
    _touch(root / "parsed" / _SLUG / f"parsed_{_TAG}.json", base + 2)
    _touch(root / "stems" / _SLUG / _TAG / "001_intro_host.mp3", base + 3)
    _touch(root / "stems" / _SLUG / _TAG / f"{_TAG}_stem_manifest.json", base + 3)
    _touch(root / "daw" / _SLUG / _TAG / f"{_TAG}_layer_dialogue.wav", base + 4)
    _touch(root / "masters" / _SLUG / f"{_TAG}_{_SLUG}_2026-06-18.mp3", base + 5)


def _no_gdoc_dir(tmp_path):
    """A gdoc dir path that does not exist."""
    return tmp_path / "nonexistent_drive"


# ── evaluate_episode ──────────────────────────────────────────────────────────


def test_all_fresh_is_ok(ws):
    _build_fresh(ws)
    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    for name in ("script", "parsed", "stems", "daw", "master"):
        assert by_name[name].status == status._OK, f"{name} not OK"
    assert status._worst(stages) == status._OK


def test_bumping_parsed_makes_stems_stale(ws):
    _build_fresh(ws)
    # Make parsed newer than the stems derived from it.
    parsed = ws / "parsed" / _SLUG / f"parsed_{_TAG}.json"
    os.utime(parsed, (9000, 9000))

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    assert by_name["stems"].status == status._STALE
    assert by_name["stems"].refresh == f"xil produce --episode {_TAG}"
    assert status._worst(stages) == status._STALE


def test_missing_master_is_missing(ws):
    _build_fresh(ws)
    # Remove the master.
    for p in (ws / "masters" / _SLUG).glob("*.mp3"):
        p.unlink()

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    assert by_name["master"].status == status._MISSING
    assert by_name["master"].refresh == f"xil master --episode {_TAG}"
    assert status._worst(stages) == status._MISSING


def test_missing_gdoc_dir_source_is_informational(ws):
    _build_fresh(ws)
    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    src = next(s for s in stages if s.name == "source")
    assert src.status == status._NONE
    assert "no gdoc dir" in src.note
    # Source being absent must not affect the overall verdict.
    assert status._worst(stages) == status._OK


def test_gdoc_newer_makes_script_stale(ws, tmp_path):
    _build_fresh(ws)
    gdoc_dir = tmp_path / "drive"
    _touch(gdoc_dir / f"{_TAG}_{_SLUG}.md.gdoc", 5000)  # newer than script (1001)

    stages = status.evaluate_episode(_SLUG, _TAG, gdoc_dir)
    by_name = {s.name: s for s in stages}
    assert by_name["script"].status == status._STALE


def test_parse_suggestion_uses_newest_script_draft(ws):
    """With multiple drafts on disk, the parse hint points at the newest one."""
    _build_fresh(ws)
    scripts = ws / "scripts"
    # Older drafts that sort *after* the build-fresh script alphabetically but
    # are older by mtime; and one newer draft. Newest must win regardless of name.
    _touch(scripts / f"{_TAG}_{_SLUG}_bridge_v1.md", 900)
    _touch(scripts / f"{_TAG}_{_SLUG}_diner_v3.md", 9000)  # newest
    # Make parsed older than the newest script so the parse stage is flagged.
    os.utime(ws / "parsed" / _SLUG / f"parsed_{_TAG}.json", (1500, 1500))

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    assert by_name["parsed"].status == status._STALE
    assert by_name["parsed"].refresh.endswith(f"_diner_v3.md --episode {_TAG}")


# ── --all discovery ───────────────────────────────────────────────────────────


def test_discover_tags_finds_all_episodes(ws):
    _build_fresh(ws)
    _touch(ws / "parsed" / _SLUG / "parsed_S01E02.json", 1002)
    _touch(ws / "daw" / _SLUG / "S02E05" / "S02E05_layer_dialogue.wav", 1003)

    tags = status._discover_tags(_SLUG)
    assert tags == ["S01E01", "S01E02", "S02E05"]


# ── CLI / exit codes ──────────────────────────────────────────────────────────


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as exc:
        status.main()
    return exc.value.code


def test_cli_exit_zero_when_fresh(ws, monkeypatch):
    _build_fresh(ws)
    code = _run_cli(
        monkeypatch,
        ["xil-status", "--episode", _TAG, "--show", _SLUG,
         "--gdoc-dir", str(_no_gdoc_dir(ws))],
    )
    assert code == 0


def test_cli_exit_one_when_stale(ws, monkeypatch):
    _build_fresh(ws)
    os.utime(ws / "parsed" / _SLUG / f"parsed_{_TAG}.json", (9000, 9000))
    code = _run_cli(
        monkeypatch,
        ["xil-status", _TAG, "--show", _SLUG, "--gdoc-dir", str(_no_gdoc_dir(ws))],
    )
    assert code == 1


def test_cli_no_tag_is_usage_error(ws, monkeypatch):
    code = _run_cli(monkeypatch, ["xil-status", "--show", _SLUG])
    assert code == 2


def test_cli_json_output(ws, monkeypatch, capsys):
    _build_fresh(ws)
    code = _run_cli(
        monkeypatch,
        ["xil-status", _TAG, "--show", _SLUG, "--json",
         "--gdoc-dir", str(_no_gdoc_dir(ws))],
    )
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["episode"] == _TAG
    assert payload["show"] == _SLUG
    assert payload["overall"] == status._OK
    assert {s["name"] for s in payload["stages"]} == {
        "source", "script", "parsed", "stems", "daw", "master"
    }


def test_cli_json_with_all_is_error(ws, monkeypatch):
    code = _run_cli(monkeypatch, ["xil-status", "--all", "--json", "--show", _SLUG])
    assert code == 2


def test_cli_all_exit_one_when_any_stale(ws, monkeypatch):
    _build_fresh(ws)
    # second episode missing everything downstream of parse
    _touch(ws / "parsed" / _SLUG / "parsed_S01E02.json", 1002)
    code = _run_cli(
        monkeypatch,
        ["xil-status", "--all", "--show", _SLUG, "--gdoc-dir", str(_no_gdoc_dir(ws))],
    )
    assert code == 1
