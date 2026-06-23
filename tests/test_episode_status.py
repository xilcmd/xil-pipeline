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
    # XILP011 writes the master flat under masters/ as {tag}_{slug}_{date}.mp3.
    _touch(root / "masters" / f"{_TAG}_{_SLUG}_2026-06-18.mp3", base + 5)


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


def test_old_stems_with_fresh_manifest_are_ok(ws):
    """Dedup keeps old stem mtimes; the fresh manifest must keep stems OK (#2)."""
    _build_fresh(ws)
    # Simulate content-hash dedup: most stem MP3s are reused from a much older run.
    for p in (ws / "stems" / _SLUG / _TAG).glob("*.mp3"):
        os.utime(p, (1, 1))  # epoch — far older than parsed
    # The manifest is rewritten on every produce run, so it stays fresh: newer
    # than parsed (1002) yet still older than the downstream daw layer (1004).
    manifest = ws / "stems" / _SLUG / _TAG / f"{_TAG}_stem_manifest.json"
    os.utime(manifest, (1003, 1003))

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    assert by_name["stems"].status == status._OK
    # daw must also stay OK: its input marker is the manifest, not the old stems.
    assert by_name["daw"].status == status._OK


def test_noop_reproduce_does_not_stale_daw(ws):
    """A no-op re-produce bumps the manifest but not the stems; daw stays OK."""
    _build_fresh(ws)  # stems/manifest @1003, daw @1004
    # Later no-op produce: manifest jumps past daw, but no stem MP3 is rewritten.
    os.utime(ws / "stems" / _SLUG / _TAG / f"{_TAG}_stem_manifest.json", (9999, 9999))

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    assert by_name["stems"].status == status._OK
    # The manifest is not daw's input — a no-op bump must not invalidate daw.
    assert by_name["daw"].status == status._OK


def test_old_reused_stems_do_not_stale_stage(ws):
    """Ancient dedup-reused stems coexisting with fresh ones don't trip STALE."""
    _build_fresh(ws)  # parsed @1002, newest stem/manifest @1003
    _touch(ws / "stems" / _SLUG / _TAG / "099_reused_sfx.mp3", 1)  # epoch-old reuse

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    # Newest output (1003) > parsed (1002): the stage ran after the parse.
    assert by_name["stems"].status == status._OK


def test_stems_stale_when_not_reproduced_after_parse(ws):
    """Re-parse without re-produce: newest stem/manifest predates parsed → STALE."""
    _build_fresh(ws)
    os.utime(ws / "parsed" / _SLUG / f"parsed_{_TAG}.json", (9000, 9000))

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    assert by_name["stems"].status == status._STALE


def test_stems_fall_back_to_files_without_manifest(ws):
    """Pre-manifest workspaces still judge stems by their MP3 files (#2 fallback)."""
    _build_fresh(ws)
    (ws / "stems" / _SLUG / _TAG / f"{_TAG}_stem_manifest.json").unlink()
    os.utime(ws / "parsed" / _SLUG / f"parsed_{_TAG}.json", (9000, 9000))

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    assert by_name["stems"].status == status._STALE


def test_stems_file_count_excludes_manifest(ws):
    """The FILES tally counts MP3 stems, not the manifest (#2 count override)."""
    _build_fresh(ws)
    _touch(ws / "stems" / _SLUG / _TAG / "002_intro_guest.mp3", 1003)

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    by_name = {s.name: s for s in stages}
    assert by_name["stems"].output_count == 2  # two MP3s, manifest not counted


def test_stale_stage_exposes_oldest_output(ws):
    """A STALE stage records the deciding (oldest) output value (#1)."""
    _build_fresh(ws)
    # Old reused stem files + a manifest that predates the re-parse → STALE.
    for p in (ws / "stems" / _SLUG / _TAG).glob("*"):
        os.utime(p, (100, 100))
    os.utime(ws / "parsed" / _SLUG / f"parsed_{_TAG}.json", (9000, 9000))

    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    stems = next(s for s in stages if s.name == "stems")
    assert stems.status == status._STALE
    assert stems.oldest_output == 100


def test_flat_master_is_found(ws):
    """The flat masters/{tag}_{slug}_{date}.mp3 written by XILP011 is detected."""
    _build_fresh(ws)
    stages = status.evaluate_episode(_SLUG, _TAG, _no_gdoc_dir(ws))
    master = next(s for s in stages if s.name == "master")
    assert master.status == status._OK
    assert master.output_count == 1


def test_missing_master_is_missing(ws):
    _build_fresh(ws)
    # Remove the master (flat under masters/, per XILP011).
    for p in (ws / "masters").glob("*.mp3"):
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
    # Every stage carries the deciding (oldest) output field for scripting.
    assert all("oldest_output" in s for s in payload["stages"])


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


# ── rejected-SFX status messaging ──────────────────────────────────────────────

def _make_sfx(path, mtime=1000):
    from pydub import AudioSegment
    path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.silent(duration=80).export(str(path), format="mp3")
    os.utime(path, (mtime, mtime))


class TestRejectedSfx:
    def test_lists_rejected_effect_keys(self, ws):
        from xil_pipeline.sfx_common import slugify_effect_key, write_sfx_grade
        sfx = ws / "SFX"
        door = sfx / f"{slugify_effect_key('SFX: DOOR')}.mp3"
        bell = sfx / f"{slugify_effect_key('SFX: BELL')}.mp3"
        _make_sfx(door)
        _make_sfx(bell)
        write_sfx_grade(str(door), "rejected")
        write_sfx_grade(str(bell), "accurate")

        cfg = {"show": "X", "effects": {
            "SFX: DOOR": {"prompt": "d", "duration_seconds": 1.0},
            "SFX: BELL": {"prompt": "b", "duration_seconds": 1.0},
        }}
        cfg_dir = ws / "configs" / _SLUG
        cfg_dir.mkdir(parents=True)
        (cfg_dir / f"sfx_{_TAG}.json").write_text(json.dumps(cfg), encoding="utf-8")

        assert status._rejected_sfx(_SLUG, _TAG) == ["SFX: DOOR"]

    def test_no_config_returns_empty(self, ws):
        assert status._rejected_sfx(_SLUG, _TAG) == []

    def test_source_effect_rejection_detected(self, ws):
        from xil_pipeline.sfx_common import write_sfx_grade
        src = ws / "SFX" / "my custom door.mp3"
        _make_sfx(src)
        write_sfx_grade(str(src), "rejected")
        cfg = {"show": "X", "effects": {
            "SFX: DOOR": {"source": "SFX/my custom door.mp3", "duration_seconds": 1.0},
        }}
        cfg_dir = ws / "configs" / _SLUG
        cfg_dir.mkdir(parents=True)
        (cfg_dir / f"sfx_{_TAG}.json").write_text(json.dumps(cfg), encoding="utf-8")
        assert status._rejected_sfx(_SLUG, _TAG) == ["SFX: DOOR"]

    def test_json_includes_rejected_sfx(self, ws, monkeypatch, capsys):
        from xil_pipeline.sfx_common import slugify_effect_key, write_sfx_grade
        _build_fresh(ws)
        door = ws / "SFX" / f"{slugify_effect_key('SFX: DOOR')}.mp3"
        _make_sfx(door)
        write_sfx_grade(str(door), "rejected")
        cfg_dir = ws / "configs" / _SLUG
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / f"sfx_{_TAG}.json").write_text(
            json.dumps({"show": "X", "effects": {"SFX: DOOR": {"prompt": "d", "duration_seconds": 1.0}}}),
            encoding="utf-8",
        )
        code = _run_cli(
            monkeypatch,
            ["xil-status", _TAG, "--show", _SLUG, "--json", "--gdoc-dir", str(_no_gdoc_dir(ws))],
        )
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["rejected_sfx"] == ["SFX: DOOR"]
