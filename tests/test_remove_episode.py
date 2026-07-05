# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for xil-remove-episode (XILU018)."""

import sys

import pytest

from xil_pipeline.XILU018_remove_episode import _collect, main

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _scaffold_episode(root, slug, tag):
    """Create a realistic episode artifact tree."""
    # configs
    cfg = root / "configs" / slug
    cfg.mkdir(parents=True)
    (cfg / f"cast_{tag}.json").write_text("{}", encoding="utf-8")
    (cfg / f"sfx_{tag}.json").write_text("{}", encoding="utf-8")

    # parsed
    psd = root / "parsed" / slug
    psd.mkdir(parents=True)
    for prefix in ("parsed", "orig_parsed", "pre_splice_parsed", "stem_verify"):
        (psd / f"{prefix}_{tag}.json").write_text("{}", encoding="utf-8")
    (psd / f"parsed_{tag}.csv").write_text("seq\n1\n", encoding="utf-8")

    # stems
    stems = root / "stems" / slug / tag
    stems.mkdir(parents=True)
    (stems / "001_cold-open_host.mp3").write_bytes(b"\xff\xfb" * 100)

    # daw
    daw = root / "daw" / slug / tag
    daw.mkdir(parents=True)
    (daw / f"{tag}_layer_dialogue.wav").write_bytes(b"RIFF")

    # masters — both the slug-subdir canonical name and the flat XILP011 output
    masters = root / "masters" / slug
    masters.mkdir(parents=True)
    (masters / f"{tag}_master.mp3").write_bytes(b"\xff\xfb" * 50)
    flat_masters = root / "masters"
    (flat_masters / f"{tag}_{slug}_2026-06-19.mp3").write_bytes(b"\xff\xfb" * 50)

    # cues
    cues = root / "cues" / slug
    cues.mkdir(parents=True)
    (cues / f"cues_{tag}.md").write_text("# Cues\n", encoding="utf-8")
    (cues / f"cues_manifest_{tag}.json").write_text("{}", encoding="utf-8")

    # posts
    posts = root / "posts" / slug
    posts.mkdir(parents=True)
    (posts / f"{tag}_posts.md").write_text("# Posts\n", encoding="utf-8")

    # voice_samples
    vs = root / "voice_samples" / tag
    vs.mkdir(parents=True)
    (vs / "host.mp3").write_bytes(b"\xff\xfb" * 20)

    # source script — must NOT be touched
    scripts = root / "scripts"
    scripts.mkdir(exist_ok=True)
    script = scripts / f"{tag}_mypodcast_Pilot_v1.md"
    script.write_text("# Script\n", encoding="utf-8")

    return script


# ── _collect ─────────────────────────────────────────────────────────────────


class TestCollect:
    def test_collects_cast_config(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert workspace / "configs" / "mypodcast" / "cast_S01E01.json" in paths

    def test_collects_sfx_config(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert workspace / "configs" / "mypodcast" / "sfx_S01E01.json" in paths

    def test_collects_stems_dir(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert workspace / "stems" / "mypodcast" / "S01E01" in paths

    def test_collects_daw_dir(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert workspace / "daw" / "mypodcast" / "S01E01" in paths

    def test_does_not_collect_master_mp3(self, workspace):
        # masters/ is never touched by remove-episode — it must survive erasure.
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert workspace / "masters" / "mypodcast" / "S01E01_master.mp3" not in paths

    def test_does_not_collect_flat_master_mp3(self, workspace):
        # XILP011 writes masters/{tag}_{slug}_{date}.mp3 flat under masters/ —
        # still preserved, same as the nested layout.
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert workspace / "masters" / "S01E01_mypodcast_2026-06-19.mp3" not in paths

    def test_flat_master_scoped_by_slug(self, workspace):
        # A different show sharing the same tag must NOT be collected.
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        other = workspace / "masters" / "S01E01_othershow_2026-06-19.mp3"
        other.write_bytes(b"\xff\xfb" * 50)
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert other not in paths

    def test_collects_posts(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert workspace / "posts" / "mypodcast" / "S01E01_posts.md" in paths

    def test_collects_voice_samples_dir(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert workspace / "voice_samples" / "S01E01" in paths

    def test_collects_legacy_cast_at_root(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        legacy = workspace / "cast_mypodcast_S01E01.json"
        legacy.write_text("{}", encoding="utf-8")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert legacy in paths

    def test_does_not_collect_source_script(self, workspace):
        script = _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert script not in paths

    def test_does_not_collect_other_episode_stems(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        # Create a second episode's stems directory
        other = workspace / "stems" / "mypodcast" / "S01E02"
        other.mkdir(parents=True)
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert other not in paths

    def test_no_duplicate_paths(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        items = _collect("mypodcast", "S01E01")
        paths = [i.path for i in items]
        assert len(paths) == len(set(paths))


# ── End-to-end via CLI ────────────────────────────────────────────────────────


def _run_main(argv):
    old_argv = sys.argv
    sys.argv = argv
    try:
        main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv


class TestRemoveEpisodeCLI:
    def test_dry_run_removes_nothing(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        old_argv = sys.argv
        sys.argv = ["xil-remove-episode", "S01E01", "--show", "mypodcast", "--dry-run"]
        try:
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0
        finally:
            sys.argv = old_argv

        # Everything must still exist
        assert (workspace / "configs" / "mypodcast" / "cast_S01E01.json").exists()
        assert (workspace / "stems" / "mypodcast" / "S01E01").is_dir()

    def test_yes_removes_episode_artifacts(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        _run_main(["xil-remove-episode", "S01E01", "--show", "mypodcast", "--yes"])

        assert not (workspace / "configs" / "mypodcast" / "cast_S01E01.json").exists()
        assert not (workspace / "stems" / "mypodcast" / "S01E01").exists()
        assert not (workspace / "daw" / "mypodcast" / "S01E01").exists()
        assert not (workspace / "voice_samples" / "S01E01").exists()

    def test_masters_survive_removal(self, workspace):
        # masters/ is never touched by remove-episode.
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        _run_main(["xil-remove-episode", "S01E01", "--show", "mypodcast", "--yes"])
        assert (workspace / "masters" / "mypodcast" / "S01E01_master.mp3").exists()
        assert (workspace / "masters" / "S01E01_mypodcast_2026-06-19.mp3").exists()

    def test_script_survives_removal(self, workspace):
        script = _scaffold_episode(workspace, "mypodcast", "S01E01")
        _run_main(["xil-remove-episode", "S01E01", "--show", "mypodcast", "--yes"])
        assert script.exists()

    def test_other_episode_survives(self, workspace):
        _scaffold_episode(workspace, "mypodcast", "S01E01")
        other_stems = workspace / "stems" / "mypodcast" / "S01E02"
        other_stems.mkdir(parents=True)
        _run_main(["xil-remove-episode", "S01E01", "--show", "mypodcast", "--yes"])
        assert other_stems.exists()
