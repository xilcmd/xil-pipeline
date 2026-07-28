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
