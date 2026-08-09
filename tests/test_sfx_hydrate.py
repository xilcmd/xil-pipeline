# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU013_sfx_hydrate.py — applying script hints without re-parsing."""

import json

from xil_pipeline.XILU013_sfx_hydrate import hydrate_sfx_config


def _write(tmp_path, effects):
    path = tmp_path / "sfx.json"
    path.write_text(json.dumps({"effects": effects}), encoding="utf-8")
    return path


def _direction(text, **extra):
    return {"type": "direction", "text": text, **extra}


class TestSourceHydration:
    def test_missing_source_is_queued_and_written(self, tmp_path):
        path = _write(tmp_path, {"SFX: STATIC": {"prompt": "SFX: STATIC"}})
        parsed = {"entries": [_direction("SFX: STATIC", sfx_source="SFX/static.mp3")]}

        assert hydrate_sfx_config(parsed, str(path)) == 1
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["effects"]["SFX: STATIC"]["source"] == "SFX/static.mp3"

    def test_existing_source_is_not_replaced(self, tmp_path):
        path = _write(tmp_path, {"SFX: STATIC": {"source": "SFX/old.mp3"}})
        parsed = {"entries": [_direction("SFX: STATIC", sfx_source="SFX/new.mp3")]}

        assert hydrate_sfx_config(parsed, str(path)) == 0
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["effects"]["SFX: STATIC"]["source"] == "SFX/old.mp3"

    def test_dry_run_does_not_write(self, tmp_path):
        path = _write(tmp_path, {"SFX: STATIC": {"prompt": "SFX: STATIC"}})
        parsed = {"entries": [_direction("SFX: STATIC", sfx_source="SFX/static.mp3")]}

        assert hydrate_sfx_config(parsed, str(path), dry_run=True) == 1
        result = json.loads(path.read_text(encoding="utf-8"))
        assert "source" not in result["effects"]["SFX: STATIC"]


class TestAttributeHydration:
    """A volume hint must land even on a cue that is already source-hydrated."""

    def test_volume_change_on_hydrated_cue(self, tmp_path):
        path = _write(tmp_path, {"OUTRO MUSIC": {"source": "SFX/outro.mp3"}})
        parsed = {"entries": [_direction(
            "OUTRO MUSIC",
            sfx_source="SFX/outro.mp3",
            sfx_overrides={"volume_percentage": 20.0},
        )]}

        assert hydrate_sfx_config(parsed, str(path)) == 1
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["effects"]["OUTRO MUSIC"]["volume_percentage"] == 20.0

    def test_matching_volume_is_a_no_op(self, tmp_path):
        path = _write(tmp_path, {"OUTRO MUSIC": {"source": "SFX/outro.mp3",
                                                 "volume_percentage": 20.0}})
        parsed = {"entries": [_direction(
            "OUTRO MUSIC",
            sfx_source="SFX/outro.mp3",
            sfx_overrides={"volume_percentage": 20.0},
        )]}

        assert hydrate_sfx_config(parsed, str(path)) == 0

    def test_differing_volume_is_overwritten(self, tmp_path):
        path = _write(tmp_path, {"OUTRO MUSIC": {"source": "SFX/outro.mp3",
                                                 "volume_percentage": 75.0}})
        parsed = {"entries": [_direction(
            "OUTRO MUSIC", sfx_overrides={"volume_percentage": 20.0},
        )]}

        assert hydrate_sfx_config(parsed, str(path)) == 1
        result = json.loads(path.read_text(encoding="utf-8"))
        assert result["effects"]["OUTRO MUSIC"]["volume_percentage"] == 20.0


class TestForceReplacesSources:
    """--force makes the script authoritative for `source`.

    Without it, a cue whose source is a "NEW STEM NEEDED" placeholder or a stale
    path can never be corrected from the script — hydrate only fills gaps.
    """

    def _cfg(self, tmp_path, effects):
        path = tmp_path / "sfx.json"
        path.write_text(json.dumps({"effects": effects}), encoding="utf-8")
        return path

    def _parsed(self, key, source):
        return {"entries": [_direction(key, sfx_source=source)]}

    def _existing_asset(self, tmp_path, monkeypatch, rel):
        """Create rel under a fake workspace so the existence guard passes."""
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"ID3")
        return rel

    def test_without_force_existing_source_stands(self, tmp_path, monkeypatch):
        rel = self._existing_asset(tmp_path, monkeypatch, "SFX/show/new.mp3")
        path = self._cfg(tmp_path, {"SFX: X": {"source": "SFX/show/old.mp3"}})

        hydrate_sfx_config(self._parsed("SFX: X", rel), str(path))

        assert json.loads(path.read_text())["effects"]["SFX: X"]["source"] == "SFX/show/old.mp3"

    def test_force_replaces_a_differing_source(self, tmp_path, monkeypatch):
        rel = self._existing_asset(tmp_path, monkeypatch, "SFX/show/new.mp3")
        path = self._cfg(tmp_path, {"SFX: X": {"source": "SFX/show/old.mp3"}})

        hydrate_sfx_config(self._parsed("SFX: X", rel), str(path), force=True)

        assert json.loads(path.read_text())["effects"]["SFX: X"]["source"] == rel

    def test_force_replaces_a_placeholder(self, tmp_path, monkeypatch):
        """The motivating case: 20 sources are literal "NEW STEM NEEDED" strings."""
        rel = self._existing_asset(tmp_path, monkeypatch, "SFX/show/real.mp3")
        path = self._cfg(
            tmp_path,
            {"SFX: X": {"source": "SFX/show/NEW STEM NEEDED: sfx_chair-scraping.mp3"}},
        )

        hydrate_sfx_config(self._parsed("SFX: X", rel), str(path), force=True)

        assert json.loads(path.read_text())["effects"]["SFX: X"]["source"] == rel

    def test_force_skips_when_the_hinted_file_is_missing(self, tmp_path, monkeypatch):
        """The guard. Without it ~24 working bare-path sources would be repointed
        at slug-form paths with no file behind them."""
        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        working = tmp_path / "SFX" / "works.mp3"
        working.parent.mkdir(parents=True, exist_ok=True)
        working.write_bytes(b"ID3")
        path = self._cfg(tmp_path, {"SFX: X": {"source": "SFX/works.mp3"}})

        hydrate_sfx_config(
            self._parsed("SFX: X", "SFX/show/works.mp3"), str(path), force=True
        )

        assert json.loads(path.read_text())["effects"]["SFX: X"]["source"] == "SFX/works.mp3", \
            "a working source must not be repointed at a nonexistent file"

    def test_force_is_idempotent(self, tmp_path, monkeypatch):
        rel = self._existing_asset(tmp_path, monkeypatch, "SFX/show/new.mp3")
        path = self._cfg(tmp_path, {"SFX: X": {"source": "SFX/show/old.mp3"}})
        parsed = self._parsed("SFX: X", rel)

        hydrate_sfx_config(parsed, str(path), force=True)
        second = hydrate_sfx_config(parsed, str(path), force=True)

        assert second == 0, "a second forced run should have nothing to do"


class TestSourceIsJournaled:
    """A source assignment must survive a rebuild from a fresh script .md."""

    def test_applying_a_source_appends_a_journal_record(self, tmp_path, monkeypatch):
        from xil_pipeline.sfx_common import sfx_edits_path

        monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
        asset = tmp_path / "SFX" / "show" / "a.mp3"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"ID3")
        path = tmp_path / "sfx.json"
        path.write_text(json.dumps({"effects": {"SFX: X": {"prompt": "SFX: X"}}}), encoding="utf-8")

        hydrate_sfx_config(
            {"entries": [_direction("SFX: X", sfx_source="SFX/show/a.mp3")]}, str(path)
        )

        records = [json.loads(ln) for ln in
                   open(sfx_edits_path(str(path)), encoding="utf-8") if ln.strip()]
        assert any(r["key"] == "SFX: X" and r["fields"].get("source") == "SFX/show/a.mp3"
                   for r in records)

    def test_replay_reinstates_a_source_the_script_no_longer_hints(self, tmp_path):
        """The case that motivated journaling: rebuild from a script with the hint gone."""
        from xil_pipeline.sfx_common import append_sfx_edit, replay_sfx_edits

        path = tmp_path / "sfx.json"
        path.write_text(json.dumps({"effects": {"SFX: X": {"prompt": "SFX: X"}}}), encoding="utf-8")
        append_sfx_edit(str(path), "SFX: X", {"source": "SFX/show/a.mp3"})

        applied, _ = replay_sfx_edits(str(path))

        assert applied == 1
        assert json.loads(path.read_text())["effects"]["SFX: X"]["source"] == "SFX/show/a.mp3"

    def test_replay_warns_when_it_overrides_a_script_hint(self, tmp_path, caplog):
        """Journal beats script hint — that must never be silent."""
        import logging

        from xil_pipeline.sfx_common import append_sfx_edit, replay_sfx_edits

        path = tmp_path / "sfx.json"
        path.write_text(
            json.dumps({"effects": {"SFX: X": {"source": "SFX/show/from-script.mp3"}}}),
            encoding="utf-8",
        )
        append_sfx_edit(str(path), "SFX: X", {"source": "SFX/show/from-journal.mp3"})

        with caplog.at_level(logging.WARNING):
            replay_sfx_edits(str(path))

        assert "from-script.mp3" in caplog.text and "from-journal.mp3" in caplog.text

    def test_source_is_a_journaled_field(self):
        from xil_pipeline.sfx_common import SFX_EDIT_FIELDS

        assert "source" in SFX_EDIT_FIELDS
