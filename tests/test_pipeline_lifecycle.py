# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Continuous pipeline lifecycle test.

Exercises the full xil-pipeline chain end-to-end in an isolated workspace
without touching the ElevenLabs API:

    xil-init → xil-scan → xil-parse → xil-produce (gtts) → xil-daw → xil-master

A module-scoped fixture runs every stage once and asserts exit-code 0 at
each step.  The individual test methods then verify the output artifacts,
giving precise failure attribution when something breaks.

Requires:
    - gtts installed (pulled in via the tts-alt optional dep / pip install .[all])
    - network access (gTTS makes HTTP calls to Google Translate TTS)
"""

import json
import os
import subprocess
import sys

import pytest

pytest.importorskip("gtts", reason="gtts not installed — skipping pipeline lifecycle tests")

# ── Test constants ───────────────────────────────────────────────────────────

_SHOW = "Test Podcast"
_SLUG = "testpodcast"
_TAG  = "S01E01"

# Minimal two-line script — keeps gTTS calls fast (≈2 HTTP round-trips).
# Must use the canonical format: header line → CAST block → === divider → body.
_SCRIPT_NAME = f"{_TAG}_{_SLUG}_Pilot_v1.md"
_SCRIPT_CONTENT = """\
Test Podcast Season 1: Episode 1: "Pilot"

CAST:
* HOST — the host

===

COLD OPEN

SCENE 1: THE STUDIO

HOST
Hello and welcome to the podcast.

HOST
We will begin shortly.

END OF EPISODE
"""

# ── Module-scoped fixture: run the full pipeline once ────────────────────────

@pytest.fixture(scope="module")
def pipeline_ws(tmp_path_factory):
    """Scaffold a workspace, run every pipeline stage, return the workspace Path.

    Each stage is invoked as a real subprocess with XIL_PROJECTROOT pointing
    at the isolated tmp directory — exactly how xil-gui drives the pipeline.
    Asserts exit code 0 after every stage so a breakage is attributed to the
    correct step rather than appearing as a missing-artifact failure later.
    """
    ws = tmp_path_factory.mktemp("lifecycle")
    env = {**os.environ, "XIL_PROJECTROOT": str(ws)}

    def _run(*module_and_args):
        result = subprocess.run(
            [sys.executable, "-m"] + list(module_and_args),
            env=env,
            capture_output=True,
            text=True,
        )
        return result

    # ── Stage 0: init ────────────────────────────────────────────────────────
    r = _run(
        "xil_pipeline.xil_init",
        "--show", _SHOW, "--type", "podcast", "--flat",
    )
    assert r.returncode == 0, f"xil-init failed:\n{r.stdout}\n{r.stderr}"

    # ── Write the test script ─────────────────────────────────────────────────
    script_path = ws / "scripts" / _SCRIPT_NAME
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_SCRIPT_CONTENT, encoding="utf-8")

    # ── Stage 1: scan ────────────────────────────────────────────────────────
    r = _run(
        "xil_pipeline.XILP000_script_scanner",
        "--show", _SLUG,
        str(script_path),
    )
    assert r.returncode == 0, f"xil-scan failed:\n{r.stdout}\n{r.stderr}"

    # ── Stage 2: parse ───────────────────────────────────────────────────────
    r = _run(
        "xil_pipeline.XILP001_script_parser",
        str(script_path), "--episode", _TAG,
    )
    assert r.returncode == 0, f"xil-parse failed:\n{r.stdout}\n{r.stderr}"

    # ── Stage 3: produce (gTTS, no ElevenLabs key required) ──────────────────
    r = _run(
        "xil_pipeline.XILP002_producer",
        "--episode", _TAG,
        "--backend", "gtts",
        "--local-only",
    )
    assert r.returncode == 0, f"xil-produce failed:\n{r.stdout}\n{r.stderr}"

    # ── Stage 4: daw ─────────────────────────────────────────────────────────
    r = _run(
        "xil_pipeline.XILP005_daw_export",
        "--episode", _TAG,
    )
    assert r.returncode == 0, f"xil-daw failed:\n{r.stdout}\n{r.stderr}"

    # ── Stage 5: master ──────────────────────────────────────────────────────
    r = _run(
        "xil_pipeline.XILP011_master_export",
        "--episode", _TAG,
    )
    assert r.returncode == 0, f"xil-master failed:\n{r.stdout}\n{r.stderr}"

    return ws


# ── Per-stage artifact checks ─────────────────────────────────────────────────

class TestPipelineLifecycle:
    """Each method verifies a specific artifact produced by one pipeline stage."""

    # -- init -----------------------------------------------------------------

    def test_init_project_json_exists(self, pipeline_ws):
        assert (pipeline_ws / "configs" / _SLUG / "project.json").exists()

    def test_init_project_json_show_name(self, pipeline_ws):
        data = json.loads(
            (pipeline_ws / "configs" / _SLUG / "project.json").read_text(encoding="utf-8")
        )
        assert data.get("show") == _SHOW

    def test_init_speakers_json_exists(self, pipeline_ws):
        assert (pipeline_ws / "configs" / _SLUG / "speakers.json").exists()

    # -- scan -----------------------------------------------------------------

    def test_scan_exits_cleanly(self, pipeline_ws):
        # Scan already asserted exit 0 in the fixture; this confirms the
        # scanner left no crash artifacts (i.e. the workspace is intact).
        assert (pipeline_ws / "configs" / _SLUG / "project.json").exists()

    # -- parse ----------------------------------------------------------------

    def test_parse_creates_parsed_json(self, pipeline_ws):
        assert (pipeline_ws / "parsed" / _SLUG / f"parsed_{_TAG}.json").exists()

    def test_parse_json_has_dialogue_entries(self, pipeline_ws):
        data = json.loads(
            (pipeline_ws / "parsed" / _SLUG / f"parsed_{_TAG}.json").read_text(encoding="utf-8")
        )
        dialogue = [e for e in data.get("entries", []) if e.get("type") == "dialogue"]
        assert len(dialogue) >= 2, f"Expected ≥2 dialogue entries, got {len(dialogue)}"

    def test_parse_creates_cast_config(self, pipeline_ws):
        assert (pipeline_ws / "configs" / _SLUG / f"cast_{_TAG}.json").exists()

    def test_parse_creates_sfx_config(self, pipeline_ws):
        assert (pipeline_ws / "configs" / _SLUG / f"sfx_{_TAG}.json").exists()

    # -- produce --------------------------------------------------------------

    def test_produce_creates_stem_directory(self, pipeline_ws):
        assert (pipeline_ws / "stems" / _SLUG / _TAG).is_dir()

    def test_produce_creates_mp3_stems(self, pipeline_ws):
        stems = list((pipeline_ws / "stems" / _SLUG / _TAG).glob("*.mp3"))
        assert len(stems) >= 2, f"Expected ≥2 stems, found {len(stems)}"

    def test_produce_creates_stem_manifest(self, pipeline_ws):
        assert (pipeline_ws / "stems" / _SLUG / _TAG / f"{_TAG}_stem_manifest.json").exists()

    # -- daw ------------------------------------------------------------------

    def test_daw_creates_output_directory(self, pipeline_ws):
        assert (pipeline_ws / "daw" / _SLUG / _TAG).is_dir()

    def test_daw_creates_dialogue_layer(self, pipeline_ws):
        assert (pipeline_ws / "daw" / _SLUG / _TAG / f"{_TAG}_layer_dialogue.wav").exists()

    def test_daw_creates_core_layers(self, pipeline_ws):
        # vintage_filter layer is only written when the script contains VINTAGE FILTER markers
        daw_dir = pipeline_ws / "daw" / _SLUG / _TAG
        for layer in ("dialogue", "ambience", "music", "sfx"):
            wav = daw_dir / f"{_TAG}_layer_{layer}.wav"
            assert wav.exists(), f"DAW layer missing: {layer}"

    def test_daw_dialogue_layer_is_nonzero(self, pipeline_ws):
        wav = pipeline_ws / "daw" / _SLUG / _TAG / f"{_TAG}_layer_dialogue.wav"
        assert wav.stat().st_size > 0

    # -- master ---------------------------------------------------------------

    def test_master_creates_mp3(self, pipeline_ws):
        masters_dir = pipeline_ws / "masters"
        mp3s = list(masters_dir.glob("*.mp3"))
        assert len(mp3s) == 1, f"Expected exactly 1 master MP3, found: {[p.name for p in mp3s]}"

    def test_master_mp3_is_nonzero(self, pipeline_ws):
        mp3 = next((pipeline_ws / "masters").glob("*.mp3"))
        assert mp3.stat().st_size > 0

    def test_master_mp3_filename_contains_slug_and_tag(self, pipeline_ws):
        mp3 = next((pipeline_ws / "masters").glob("*.mp3"))
        assert _TAG in mp3.name
        assert _SLUG in mp3.name
