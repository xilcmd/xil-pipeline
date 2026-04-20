# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP006_cues_ingester.py — Cues Sheet Ingester."""

import json
import os
import unittest.mock

import pytest

# ── Module import ────────────────────────────────────────────────────────────
from xil_pipeline import XILP006_cues_ingester as cues_ingester

# ── Sample markdown fixture ───────────────────────────────────────────────────

SAMPLE_CUES_MD = """\
# The 413 — Test Episode Sound Cues

## **MUSIC CUES**

### **MUS-THEME-01 (REUSE)**

**Prompt:** Eerie folk theme, acoustic guitar **Duration:** 60 seconds **Used:** Cold open, closing

### **MUS-STING-01 (NEW)**

**Prompt:** Brief musical release, hopeful, 5 seconds **Duration:** 5 seconds **Used:** Scene 1

### **MUS-LONG-01 (NEW)**

**Prompt:** Epic underscore, 3 minutes loopable **Duration:** 3 minutes **Used:** Scene 3

## AMBIENCE

### **AMB-DINER-01 (REUSE)**

**Prompt:** Morning diner ambience, coffee percolating **Duration:** Loop **Used:** Scene 1

### **AMB-QUARRY-01 (NEW)**

**Prompt:** Winter quarry ambience, wind moaning **Duration:** Loop **Used:** Scene 3

## **SOUND EFFECTS**

### Scene 1: Morrison's Diner

| Asset Name | Prompt | Placement |
| ----- | ----- | ----- |
| SFX-DOOR-BELL-01 (REUSE) | Door opening with small bell chime, cold air | Karen's entrance |
| SFX-BOOTS-STAMP-01 (NEW) | Snow being stamped off boots on doormat | Karen entering |

### Transitions & Technical

| Asset Name | Prompt | Placement |
| ----- | ----- | ----- |
| SFX-RADIO-STATIC-01 (REUSE) | Radio static tuning, vintage broadcast feel | Cold open |

## **ASSET SUMMARY**

This section should NOT be parsed as assets.

| MUS-THEME-01 | some text | 60 sec |

## **RUNTIME ESTIMATES**

This section should NOT be parsed either.
"""


@pytest.fixture
def cues_file(tmp_path):
    f = tmp_path / "test_cues.md"
    f.write_text(SAMPLE_CUES_MD, encoding="utf-8")
    return str(f)


@pytest.fixture
def sfx_config_file(tmp_path):
    config = {
        "show": "THE 413",
        "season": 2,
        "episode": 3,
        "defaults": {"prompt_influence": 0.3},
        "effects": {
            "MUSIC: MUS-THEME-01 — EERIE FOLK, FADES UNDER": {
                "prompt": "MUSIC: MUS-THEME-01 — EERIE FOLK, FADES UNDER",
                "duration_seconds": 3.0,
            },
            "MUSIC: MUS-THEME-01 — UP BRIEFLY, THEN OUT": {
                "prompt": "MUSIC: MUS-THEME-01 — UP BRIEFLY, THEN OUT",
                "duration_seconds": 3.0,
            },
            "BEAT": {
                "type": "silence",
                "duration_seconds": 1.0,
            },
            "AMBIENCE: DINER": {
                "prompt": "Diner ambience",
                "duration_seconds": 5.0,
                "loop": True,
            },
        },
    }
    p = tmp_path / "sfx_the413_S02E03.json"
    p.write_text(json.dumps(config), encoding="utf-8")
    return str(p)


# ── Tests: module import ──────────────────────────────────────────────────────


class TestModuleImport:
    def test_importable(self):
        assert cues_ingester is not None

    def test_has_main(self):
        assert hasattr(cues_ingester, "main")

    def test_has_parse_cues_markdown(self):
        assert hasattr(cues_ingester, "parse_cues_markdown")

    def test_has_write_manifest(self):
        assert hasattr(cues_ingester, "write_manifest")

    def test_constants_defined(self):
        assert cues_ingester.SFX_DIR == "SFX"
        assert cues_ingester.API_MAX_DURATION == 30.0
        assert cues_ingester.DEFAULT_SFX_DURATION == 5.0


# ── Tests: parse_duration ─────────────────────────────────────────────────────


class TestParseDuration:
    def test_seconds(self):
        assert cues_ingester.parse_duration("60 seconds") == 60.0

    def test_minutes(self):
        assert cues_ingester.parse_duration("2 minutes") == 120.0

    def test_minutes_abbreviated(self):
        assert cues_ingester.parse_duration("3 min") == 180.0

    def test_seconds_abbreviated(self):
        assert cues_ingester.parse_duration("5 sec") == 5.0

    def test_fractional(self):
        assert cues_ingester.parse_duration("1.5 seconds") == 1.5

    def test_loop_returns_none(self):
        assert cues_ingester.parse_duration("Loop") is None

    def test_loopable_returns_none(self):
        assert cues_ingester.parse_duration("2 minutes (loopable)") is None

    def test_empty_returns_none(self):
        assert cues_ingester.parse_duration("") is None

    def test_whitespace_returns_none(self):
        assert cues_ingester.parse_duration("   ") is None

    def test_unrecognised_returns_none(self):
        assert cues_ingester.parse_duration("variable") is None


# ── Tests: parse_cues_markdown ────────────────────────────────────────────────


class TestParseCuesMarkdown:
    def test_total_asset_count(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        # 3 MUSIC + 2 AMBIENCE + 3 SFX = 8
        assert len(assets) == 8

    def test_music_assets_parsed(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        music = [a for a in assets if a["category"] == "MUSIC"]
        assert len(music) == 3

    def test_ambience_assets_parsed(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        amb = [a for a in assets if a["category"] == "AMBIENCE"]
        assert len(amb) == 2

    def test_sfx_assets_parsed(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        sfx = [a for a in assets if a["category"] == "SFX"]
        assert len(sfx) == 3

    def test_reuse_flag_music(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        theme = next(a for a in assets if a["asset_id"] == "MUS-THEME-01")
        assert theme["reuse"] is True

    def test_new_flag_music(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        sting = next(a for a in assets if a["asset_id"] == "MUS-STING-01")
        assert sting["reuse"] is False

    def test_music_duration_seconds(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        theme = next(a for a in assets if a["asset_id"] == "MUS-THEME-01")
        assert theme["duration_seconds"] == 60.0

    def test_music_sting_duration(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        sting = next(a for a in assets if a["asset_id"] == "MUS-STING-01")
        assert sting["duration_seconds"] == 5.0

    def test_long_music_duration(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        long_m = next(a for a in assets if a["asset_id"] == "MUS-LONG-01")
        assert long_m["duration_seconds"] == 180.0

    def test_ambience_loop_true(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        diner = next(a for a in assets if a["asset_id"] == "AMB-DINER-01")
        assert diner["loop"] is True

    def test_ambience_duration_none_for_loop(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        diner = next(a for a in assets if a["asset_id"] == "AMB-DINER-01")
        assert diner["duration_seconds"] is None

    def test_sfx_reuse_flag(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        door = next(a for a in assets if a["asset_id"] == "SFX-DOOR-BELL-01")
        assert door["reuse"] is True

    def test_sfx_new_flag(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        boots = next(a for a in assets if a["asset_id"] == "SFX-BOOTS-STAMP-01")
        assert boots["reuse"] is False

    def test_sfx_scene_assigned(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        door = next(a for a in assets if a["asset_id"] == "SFX-DOOR-BELL-01")
        assert "Diner" in door["scene"] or "Scene 1" in (door["scene"] or "")

    def test_sfx_prompt_captured(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        boots = next(a for a in assets if a["asset_id"] == "SFX-BOOTS-STAMP-01")
        assert "boot" in boots["prompt"].lower()

    def test_asset_summary_not_parsed(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        # ASSET SUMMARY table rows should not generate additional assets
        ids = [a["asset_id"] for a in assets]
        assert ids.count("MUS-THEME-01") == 1

    def test_music_asset_id_uppercase(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        for a in assets:
            assert a["asset_id"] == a["asset_id"].upper()

    def test_transitions_scene_parsed(self, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        static = next(a for a in assets if a["asset_id"] == "SFX-RADIO-STATIC-01")
        assert static["scene"] is not None


# ── Tests: asset_library_path ─────────────────────────────────────────────────


class TestAssetLibraryPath:
    def test_music_naming(self):
        path = cues_ingester.asset_library_path("MUS-THEME-MAIN-01")
        assert path == os.path.join("SFX", "mus-theme-main-01.mp3")

    def test_sfx_naming(self):
        path = cues_ingester.asset_library_path("SFX-BOOTS-STAMP-01")
        assert path == os.path.join("SFX", "sfx-boots-stamp-01.mp3")

    def test_custom_sfx_dir(self):
        path = cues_ingester.asset_library_path("AMB-QUARRY-01", sfx_dir="custom/")
        assert path == os.path.join("custom/", "amb-quarry-01.mp3")


# ── Tests: generation_duration ────────────────────────────────────────────────


class TestGenerationDuration:
    def test_under_cap_passthrough(self):
        asset = {"duration_seconds": 10.0}
        assert cues_ingester.generation_duration(asset) == 10.0

    def test_over_cap_clamped(self):
        asset = {"duration_seconds": 180.0}
        assert cues_ingester.generation_duration(asset) == 30.0

    def test_at_cap_passthrough(self):
        asset = {"duration_seconds": 30.0}
        assert cues_ingester.generation_duration(asset) == 30.0

    def test_none_uses_default(self):
        asset = {"duration_seconds": None}
        assert cues_ingester.generation_duration(asset) == cues_ingester.DEFAULT_SFX_DURATION

    def test_missing_key_uses_default(self):
        asset = {}
        assert cues_ingester.generation_duration(asset) == cues_ingester.DEFAULT_SFX_DURATION

    def test_zero_uses_default(self):
        asset = {"duration_seconds": 0.0}
        assert cues_ingester.generation_duration(asset) == cues_ingester.DEFAULT_SFX_DURATION

    def test_negative_uses_default(self):
        asset = {"duration_seconds": -5.0}
        assert cues_ingester.generation_duration(asset) == cues_ingester.DEFAULT_SFX_DURATION


# ── Tests: credits_for_duration ───────────────────────────────────────────────


class TestCreditsForDuration:
    def test_whole_seconds(self):
        assert cues_ingester.credits_for_duration(5.0) == 200

    def test_exact_result_no_rounding_needed(self):
        assert cues_ingester.credits_for_duration(1.5) == 60

    def test_fractional_rounds_up(self):
        # 0.025 * 40 = 1.0 exactly, but floating point may produce 0.999…
        # ceil must return 1, not 0
        assert cues_ingester.credits_for_duration(0.025) >= 1

    def test_sub_second_rounds_up_to_one(self):
        assert cues_ingester.credits_for_duration(0.001) == 1

    def test_default_duration_credits(self):
        expected = cues_ingester.credits_for_duration(cues_ingester.DEFAULT_SFX_DURATION)
        assert expected == 200  # 5.0 * 40

    def test_max_duration_credits(self):
        assert cues_ingester.credits_for_duration(cues_ingester.API_MAX_DURATION) == 1200  # 30 * 40


# ── Tests: asset_status ───────────────────────────────────────────────────────


class TestAssetStatus:
    def test_exists_when_file_present(self, tmp_path):
        asset = {"asset_id": "MUS-TEST-01", "reuse": False}
        lib = str(tmp_path)
        path = cues_ingester.asset_library_path("MUS-TEST-01", sfx_dir=lib)
        open(path, "wb").write(b"data")
        assert cues_ingester.asset_status(asset, sfx_dir=lib) == "EXISTS"

    def test_reuse_when_reuse_flag_and_missing(self, tmp_path):
        asset = {"asset_id": "MUS-TEST-01", "reuse": True}
        st = cues_ingester.asset_status(asset, sfx_dir=str(tmp_path))
        assert st.strip() == "REUSE"

    def test_new_when_new_flag_and_missing(self, tmp_path):
        asset = {"asset_id": "MUS-TEST-01", "reuse": False}
        st = cues_ingester.asset_status(asset, sfx_dir=str(tmp_path))
        assert st.strip() == "NEW"

    def test_zero_byte_file_not_exists(self, tmp_path):
        asset = {"asset_id": "MUS-TEST-01", "reuse": False}
        lib = str(tmp_path)
        path = cues_ingester.asset_library_path("MUS-TEST-01", sfx_dir=lib)
        open(path, "wb").close()  # zero bytes
        assert cues_ingester.asset_status(asset, sfx_dir=lib).strip() == "NEW"


# ── Tests: find_sfx_config_matches ────────────────────────────────────────────


class TestFindSfxConfigMatches:
    def test_finds_single_match(self):
        effects = {
            "MUSIC: MUS-STING-01 — HOPEFUL": {"prompt": "...", "duration_seconds": 3.0},
            "BEAT": {"type": "silence", "duration_seconds": 1.0},
        }
        matches = cues_ingester.find_sfx_config_matches("MUS-STING-01", effects)
        assert matches == ["MUSIC: MUS-STING-01 — HOPEFUL"]

    def test_finds_multiple_matches(self):
        effects = {
            "MUSIC: MUS-THEME-01 — FADES UNDER": {"prompt": "...", "duration_seconds": 3.0},
            "MUSIC: MUS-THEME-01 — UP BRIEFLY": {"prompt": "...", "duration_seconds": 3.0},
            "BEAT": {"type": "silence", "duration_seconds": 1.0},
        }
        matches = cues_ingester.find_sfx_config_matches("MUS-THEME-01", effects)
        assert len(matches) == 2
        assert all("MUS-THEME-01" in k for k in matches)

    def test_no_match_returns_empty(self):
        effects = {"BEAT": {"type": "silence", "duration_seconds": 1.0}}
        matches = cues_ingester.find_sfx_config_matches("MUS-STING-01", effects)
        assert matches == []

    def test_case_insensitive(self):
        effects = {"MUSIC: MUS-THEME-01 — TEST": {"prompt": "...", "duration_seconds": 3.0}}
        matches = cues_ingester.find_sfx_config_matches("mus-theme-01", effects)
        assert len(matches) == 1


# ── Tests: enrich_sfx_config ──────────────────────────────────────────────────


class TestEnrichSfxConfig:
    def _make_assets(self):
        return [
            {
                "asset_id": "MUS-THEME-01",
                "category": "MUSIC",
                "reuse": True,
                "prompt": "Eerie folk theme, acoustic guitar, warm",
                "duration_seconds": 60.0,
                "loop": False,
                "scene": None,
            }
        ]

    def test_dry_run_does_not_write(self, sfx_config_file, caplog):
        assets = self._make_assets()
        cues_ingester.enrich_sfx_config(assets, sfx_config_file, dry_run=True)
        with open(sfx_config_file) as f:
            config = json.load(f)
        # Duration should still be 3.0 (unchanged)
        assert config["effects"]["MUSIC: MUS-THEME-01 — EERIE FOLK, FADES UNDER"][
            "duration_seconds"
        ] == 3.0

    def test_dry_run_prints_diff(self, sfx_config_file, caplog):
        assets = self._make_assets()
        cues_ingester.enrich_sfx_config(assets, sfx_config_file, dry_run=True)
        assert "WOULD UPDATE" in caplog.text

    def test_updates_duration(self, sfx_config_file):
        assets = self._make_assets()
        cues_ingester.enrich_sfx_config(assets, sfx_config_file, dry_run=False)
        with open(sfx_config_file) as f:
            config = json.load(f)
        # MUS-THEME-01 matched 2 keys; both durations should update to 30.0 (capped)
        entry = config["effects"]["MUSIC: MUS-THEME-01 — EERIE FOLK, FADES UNDER"]
        assert entry["duration_seconds"] == 30.0

    def test_updates_prompt(self, sfx_config_file):
        assets = self._make_assets()
        cues_ingester.enrich_sfx_config(assets, sfx_config_file, dry_run=False)
        with open(sfx_config_file) as f:
            config = json.load(f)
        entry = config["effects"]["MUSIC: MUS-THEME-01 — EERIE FOLK, FADES UNDER"]
        assert entry["prompt"] == "Eerie folk theme, acoustic guitar, warm"

    def test_unmatched_entries_unchanged(self, sfx_config_file):
        assets = self._make_assets()
        cues_ingester.enrich_sfx_config(assets, sfx_config_file, dry_run=False)
        with open(sfx_config_file) as f:
            config = json.load(f)
        # BEAT and AMBIENCE: DINER have no matching asset
        assert config["effects"]["BEAT"]["duration_seconds"] == 1.0

    def test_no_match_reports_nothing_updated(self, sfx_config_file, caplog):
        assets = [
            {
                "asset_id": "MUS-UNKNOWN-99",
                "category": "MUSIC",
                "reuse": False,
                "prompt": "Something",
                "duration_seconds": 5.0,
                "loop": False,
                "scene": None,
            }
        ]
        cues_ingester.enrich_sfx_config(assets, sfx_config_file, dry_run=False)
        assert "nothing to update" in caplog.text.lower()


# ── Tests: write_manifest ─────────────────────────────────────────────────────


class TestWriteManifest:
    def test_creates_file(self, tmp_path, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        original_cues_dir = cues_ingester.CUES_DIR
        cues_ingester.CUES_DIR = str(tmp_path)
        try:
            out = cues_ingester.write_manifest(assets, "S02E03", cues_file)
        finally:
            cues_ingester.CUES_DIR = original_cues_dir
        assert os.path.exists(out)

    def test_manifest_structure(self, tmp_path, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        original_cues_dir = cues_ingester.CUES_DIR
        cues_ingester.CUES_DIR = str(tmp_path)
        try:
            out = cues_ingester.write_manifest(assets, "S02E03", cues_file)
        finally:
            cues_ingester.CUES_DIR = original_cues_dir
        with open(out) as f:
            manifest = json.load(f)
        assert manifest["episode"] == "S02E03"
        assert manifest["total_assets"] == len(assets)
        assert manifest["new_count"] + manifest["reuse_count"] == len(assets)
        assert isinstance(manifest["assets"], list)

    def test_manifest_filename(self, tmp_path, cues_file):
        assets = cues_ingester.parse_cues_markdown(cues_file)
        original_cues_dir = cues_ingester.CUES_DIR
        cues_ingester.CUES_DIR = str(tmp_path)
        try:
            out = cues_ingester.write_manifest(assets, "S02E03", cues_file)
        finally:
            cues_ingester.CUES_DIR = original_cues_dir
        assert "cues_manifest_S02E03.json" in out


# ── Tests: find_cues_file ─────────────────────────────────────────────────────


class TestFindCuesFile:
    def test_finds_canonical_name(self, tmp_path):
        f = tmp_path / "cues_the413_S02E03.md"
        f.write_text("content")
        result = cues_ingester.find_cues_file("S02E03", cues_dir=str(tmp_path))
        assert result == str(f)

    def test_finds_sole_md_file(self, tmp_path):
        f = tmp_path / "Season 2, Episode 3 Sound Cues.md"
        f.write_text("content")
        result = cues_ingester.find_cues_file("S02E03", cues_dir=str(tmp_path))
        assert result == str(f)

    def test_returns_none_for_multiple_md_files(self, tmp_path):
        (tmp_path / "a.md").write_text("a")
        (tmp_path / "b.md").write_text("b")
        result = cues_ingester.find_cues_file("S02E03", cues_dir=str(tmp_path))
        assert result is None

    def test_returns_none_when_dir_missing(self, tmp_path):
        result = cues_ingester.find_cues_file(
            "S02E03", cues_dir=str(tmp_path / "nonexistent")
        )
        assert result is None


# ── Tests: main() CLI wiring ──────────────────────────────────────────────────


class TestMainCli:
    def test_dry_run_produces_report(self, tmp_path, cues_file, caplog):
        original_sfx = cues_ingester.SFX_DIR
        original_cues = cues_ingester.CUES_DIR
        cues_ingester.SFX_DIR = str(tmp_path / "SFX")
        cues_ingester.CUES_DIR = str(tmp_path / "cues")
        try:
            with unittest.mock.patch(
                "sys.argv",
                ["XILP006", "--episode", "S02E03", "--cues", cues_file, "--dry-run"],
            ):
                cues_ingester.main()
        finally:
            cues_ingester.SFX_DIR = original_sfx
            cues_ingester.CUES_DIR = original_cues
        assert "AUDIT" in caplog.text
        assert "assets" in caplog.text.lower()

    def test_manifest_written_without_flags(self, tmp_path, cues_file, caplog):
        original_sfx = cues_ingester.SFX_DIR
        original_cues = cues_ingester.CUES_DIR
        cues_ingester.SFX_DIR = str(tmp_path / "SFX")
        cues_ingester.CUES_DIR = str(tmp_path / "cues")
        try:
            with unittest.mock.patch(
                "sys.argv",
                ["XILP006", "--episode", "S02E03", "--cues", cues_file],
            ):
                cues_ingester.main()
        finally:
            cues_ingester.SFX_DIR = original_sfx
            cues_ingester.CUES_DIR = original_cues
        manifest = tmp_path / "cues" / "cues_manifest_S02E03.json"
        assert manifest.exists()

    def test_generate_dry_run_skips_api(self, tmp_path, cues_file, caplog):
        original_sfx = cues_ingester.SFX_DIR
        original_cues = cues_ingester.CUES_DIR
        cues_ingester.SFX_DIR = str(tmp_path / "SFX")
        cues_ingester.CUES_DIR = str(tmp_path / "cues")
        try:
            with unittest.mock.patch(
                "sys.argv",
                [
                    "XILP006", "--episode", "S02E03",
                    "--cues", cues_file, "--generate", "--dry-run",
                ],
            ):
                cues_ingester.main()
        finally:
            cues_ingester.SFX_DIR = original_sfx
            cues_ingester.CUES_DIR = original_cues
        assert "dry-run active" in caplog.text.lower()

    def test_enrich_sfx_config_dry_run(
        self, tmp_path, cues_file, sfx_config_file, caplog
    ):
        original_sfx = cues_ingester.SFX_DIR
        original_cues = cues_ingester.CUES_DIR
        cues_ingester.SFX_DIR = str(tmp_path / "SFX")
        cues_ingester.CUES_DIR = str(tmp_path / "cues")
        (tmp_path / "project.json").write_text(json.dumps({"show": "THE 413"}))
        # Create a legacy cast config to trigger derive_paths() legacy layout detection
        (tmp_path / "cast_the413_S02E03.json").write_text(
            json.dumps({"show": "THE 413", "episode": 3, "cast": {}}), encoding="utf-8"
        )
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        # sfx_config_file fixture already writes to tmp_path — no copy needed
        try:
            with unittest.mock.patch(
                "sys.argv",
                [
                    "XILP006", "--episode", "S02E03",
                    "--cues", cues_file,
                    "--enrich-sfx-config", "--dry-run",
                ],
            ):
                cues_ingester.main()
        finally:
            cues_ingester.SFX_DIR = original_sfx
            cues_ingester.CUES_DIR = original_cues
            os.chdir(original_cwd)
        assert "WOULD UPDATE" in caplog.text or "nothing to update" in caplog.text.lower()
