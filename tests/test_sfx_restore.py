# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU020_sfx_restore.py — replay journaled timeline sound edits."""

import json
import unittest.mock

import pytest

from xil_pipeline import XILU020_sfx_restore as restore
from xil_pipeline.sfx_common import append_sfx_edit


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    cfg_dir = tmp_path / "configs" / "myshow"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "sfx_S01E01.json").write_text(json.dumps({
        "defaults": {}, "effects": {"MUSIC: THEME": {"prompt": "t"}}}), encoding="utf-8")
    return tmp_path


def _run(argv):
    with unittest.mock.patch("sys.argv", argv):
        restore.main()


class TestSfxRestoreCLI:
    def test_applies_journaled_edits(self, ws):
        sfx = ws / "configs" / "myshow" / "sfx_S01E01.json"
        append_sfx_edit(str(sfx), "MUSIC: THEME", {"volume_percentage": 33})
        _run(["xil-sfx-restore", "--episode", "S01E01", "--show", "myshow"])
        data = json.loads(sfx.read_text())
        assert data["effects"]["MUSIC: THEME"]["volume_percentage"] == 33

    def test_dry_run_does_not_write(self, ws):
        sfx = ws / "configs" / "myshow" / "sfx_S01E01.json"
        append_sfx_edit(str(sfx), "MUSIC: THEME", {"volume_percentage": 33})
        before = sfx.read_text()
        _run(["xil-sfx-restore", "--episode", "S01E01", "--show", "myshow", "--dry-run"])
        assert sfx.read_text() == before

    def test_missing_journal_exits_1(self, ws):
        with pytest.raises(SystemExit) as exc:
            _run(["xil-sfx-restore", "--episode", "S01E01", "--show", "myshow"])
        assert exc.value.code == 1

    def test_missing_config_exits_1(self, ws):
        with pytest.raises(SystemExit) as exc:
            _run(["xil-sfx-restore", "--episode", "S09E99", "--show", "myshow"])
        assert exc.value.code == 1

    def test_get_parser_exists_for_man_generation(self):
        parser = restore.get_parser()
        assert parser.prog == "xil-sfx-restore"
