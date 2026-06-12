# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for xil-remove-show (XILU017)."""

import json

import pytest

from xil_pipeline.XILU017_remove_show import _collect, _resolve_slug

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Isolated workspace with a scaffolded show."""
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _scaffold(root, slug, show_name):
    """Create the minimal per-show directory structure."""
    (root / "configs" / slug).mkdir(parents=True)
    (root / "configs" / slug / "project.json").write_text(
        json.dumps({"show": show_name}), encoding="utf-8"
    )
    (root / "configs" / slug / "speakers.json").write_text("[]", encoding="utf-8")
    for category in ("parsed", "stems", "daw", "masters", "cues", "posts"):
        (root / category / slug).mkdir(parents=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / ".active_show").write_text(slug, encoding="utf-8")


# ── _resolve_slug ────────────────────────────────────────────────────────────


class TestResolveSlug:
    def test_raw_slug_resolves(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        assert _resolve_slug("mypodcast") == "mypodcast"

    def test_show_name_resolves(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        assert _resolve_slug("My Podcast") == "mypodcast"

    def test_case_insensitive_slug(self, workspace):
        _scaffold(workspace, "theharbor", "The Harbor")
        assert _resolve_slug("the harbor") == "theharbor"

    def test_unknown_show_returns_slugified(self, workspace):
        # No matching show — fall back to slugified input
        result = _resolve_slug("Unknown Show")
        assert result == "unknownshow"


# ── _collect ─────────────────────────────────────────────────────────────────


class TestCollect:
    def test_collects_config_dir(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        items = _collect("mypodcast", include_scripts=False)
        paths = [str(i.path) for i in items]
        assert any("configs/mypodcast" in p for p in paths)

    def test_collects_all_category_dirs(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        items = _collect("mypodcast", include_scripts=False)
        dirs = {i.path.name for i in items if i.path.is_dir()}
        assert "mypodcast" in dirs  # at least one category dir found

    def test_collects_active_show_file(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        items = _collect("mypodcast", include_scripts=False)
        paths = [str(i.path) for i in items]
        assert any(".active_show" in p for p in paths)

    def test_does_not_collect_active_show_for_other_show(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        # Point active_show at a different show
        (workspace / ".active_show").write_text("otherpodcast", encoding="utf-8")
        items = _collect("mypodcast", include_scripts=False)
        paths = [str(i.path) for i in items]
        assert not any(".active_show" in p for p in paths)

    def test_collects_legacy_cast_configs(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        legacy = workspace / "cast_mypodcast_S01E01.json"
        legacy.write_text("{}", encoding="utf-8")
        items = _collect("mypodcast", include_scripts=False)
        paths = [str(i.path) for i in items]
        assert any("cast_mypodcast_S01E01.json" in p for p in paths)

    def test_does_not_collect_other_show_legacy_files(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        other = workspace / "cast_otherpodcast_S01E01.json"
        other.write_text("{}", encoding="utf-8")
        items = _collect("mypodcast", include_scripts=False)
        paths = [str(i.path) for i in items]
        assert not any("otherpodcast" in p for p in paths)

    def test_include_scripts_matches_slug_named_files(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        script = workspace / "scripts" / "S01E01_mypodcast_Pilot_v1.md"
        script.write_text("## header", encoding="utf-8")
        items = _collect("mypodcast", include_scripts=True)
        paths = [str(i.path) for i in items]
        assert any("S01E01_mypodcast_Pilot_v1.md" in p for p in paths)

    def test_include_scripts_false_excludes_scripts(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        script = workspace / "scripts" / "S01E01_mypodcast_Pilot_v1.md"
        script.write_text("## header", encoding="utf-8")
        items = _collect("mypodcast", include_scripts=False)
        paths = [str(i.path) for i in items]
        assert not any(".md" in p for p in paths)

    def test_sample_script_not_collected_without_slug_in_name(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        sample = workspace / "scripts" / "sample_S01E01.md"
        sample.write_text("## header", encoding="utf-8")
        # Even with --include-scripts, sample_S01E01.md has no slug in name
        items = _collect("mypodcast", include_scripts=True)
        paths = [str(i.path) for i in items]
        assert not any("sample_S01E01.md" in p for p in paths)

    def test_empty_workspace_returns_no_items_for_unknown_slug(self, workspace):
        items = _collect("unknownshow", include_scripts=False)
        existing = [i for i in items if i.path.exists()]
        assert existing == []


# ── End-to-end via CLI ────────────────────────────────────────────────────────


class TestRemoveShowCLI:
    def test_dry_run_removes_nothing(self, workspace, capsys):
        _scaffold(workspace, "mypodcast", "My Podcast")
        import sys as _sys

        from xil_pipeline.XILU017_remove_show import main
        old_argv = _sys.argv
        _sys.argv = ["xil-remove-show", "mypodcast", "--dry-run"]
        try:
            with pytest.raises(SystemExit) as exc:
                main()
            assert exc.value.code == 0
        finally:
            _sys.argv = old_argv

        # configs dir must still exist
        assert (workspace / "configs" / "mypodcast").is_dir()
        # .active_show must still exist
        assert (workspace / ".active_show").exists()

    def test_yes_removes_all_items(self, workspace, capsys):
        _scaffold(workspace, "mypodcast", "My Podcast")
        import sys as _sys
        old_argv = _sys.argv
        _sys.argv = ["xil-remove-show", "mypodcast", "--yes"]
        try:
            main_import()
        finally:
            _sys.argv = old_argv

        assert not (workspace / "configs" / "mypodcast").exists()
        assert not (workspace / ".active_show").exists()

    def test_yes_legacy_cast_config_removed(self, workspace):
        _scaffold(workspace, "mypodcast", "My Podcast")
        legacy = workspace / "cast_mypodcast_S01E01.json"
        legacy.write_text("{}", encoding="utf-8")
        import sys as _sys
        old_argv = _sys.argv
        _sys.argv = ["xil-remove-show", "mypodcast", "--yes"]
        try:
            main_import()
        finally:
            _sys.argv = old_argv

        assert not legacy.exists()


def main_import():
    """Helper to call main() catching SystemExit."""
    from xil_pipeline.XILU017_remove_show import main
    try:
        main()
    except SystemExit:
        pass
