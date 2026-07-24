# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
test_webapp_smoke.py — Playwright + gradio_client regression tests for the
xil-pipeline Gradio dashboard (xil_pipeline.xil_gui._build_app).

Run:
    pytest tests/test_webapp_smoke.py --headed       # watch it in a real browser
    pytest tests/test_webapp_smoke.py                # headless (CI default)
    pytest tests/test_webapp_smoke.py --tracing on   # Playwright trace on failure

Every test runs against the single, session-scoped Gradio server + isolated
tmp workspace set up by the root conftest.py (gradio_server / workspace_dir
fixtures) — no ElevenLabs API key or real show data required.
"""

import datetime
import json
import os
import shutil

import pytest

pytest.importorskip("playwright", reason="requires pytest-playwright + chromium")
from playwright.sync_api import expect  # noqa: E402

# xil-init's podcast sample script header declares "Season 1: Episode 1",
# matching the sample_S01E01.md filename (see SAMPLE_TAG_BY_TYPE and the
# header_season default in xil_init.py's scaffold()).
PODCAST_SAMPLE_TAG = "S01E01"


def _create_show_via_ui(page, show_name: str) -> None:
    page.locator("#init-show-name textarea").fill(show_name)
    page.locator("#init-create-btn").click()
    page.wait_for_function(
        "document.querySelector('#init-log textarea').value.includes('[exit')",
        timeout=30_000,
    )
    log = page.locator("#init-log textarea").input_value()
    assert "[exit 0]" in log, f"show creation failed:\n{log}"


# ---------- UI smoke tests (Playwright) ----------


def test_app_loads(app_page):
    """Page renders and the main tab container mounts."""
    expect(app_page.locator("#app-root")).to_be_visible()


def test_setup_create_show_and_parse(app_page, workspace_dir, gui_env):
    """
    End-to-end user journey: Setup tab creates a new show workspace, then
    Run Stage > Parse turns its auto-generated sample script into parsed
    JSON. Exercises the Setup -> active-show -> Parse slug-resolution path.

    Needs gui_env: Parse resolves get_workspace_root() live per request, and
    the autouse _clear_xil_projectroot fixture (tests/conftest.py) unsets
    XIL_PROJECTROOT for every test unless something re-pins it.
    """
    _create_show_via_ui(app_page, "UI Journey Show")

    app_page.get_by_role("tab", name="Run Stage").click()
    app_page.get_by_role("tab", name="Parse").click()
    app_page.locator("#parse-episode input").fill(PODCAST_SAMPLE_TAG)
    app_page.locator("#parse-run-btn").click()

    app_page.wait_for_function(
        "document.querySelector('#run-stage-log textarea').value.includes('[exit')",
        timeout=30_000,
    )
    log = app_page.locator("#run-stage-log textarea").input_value()
    assert "[exit 0]" in log, f"parse failed:\n{log}"
    assert "Total entries:" in log

    parsed_path = os.path.join(
        workspace_dir, "parsed", "uijourneyshow", f"parsed_{PODCAST_SAMPLE_TAG}.json"
    )
    assert os.path.exists(parsed_path), f"expected {parsed_path} to be written"
    with open(parsed_path) as f:
        data = json.load(f)
    assert data["stats"]["total_entries"] > 0


# ---------- API-level tests (gradio_client — fast path) ----------


def test_setup_create_show_api(api_client, workspace_dir):
    """
    Hit the setup_create_show endpoint directly, skipping the browser.
    api_name must match the event listener's api_name= (init_btn.click(...,
    api_name="setup_create_show") in xil_gui.py's _build_app()).
    """
    output, _show_dropdown_update = api_client.predict(
        "Api Journey Show", "podcast", "", "",
        api_name="/setup_create_show",
    )
    assert "[exit 0]" in output

    assert os.path.exists(
        os.path.join(workspace_dir, "configs", "apijourneyshow", "project.json")
    )


def test_run_parse_api(api_client, workspace_dir, gui_env):
    """API-level parse: create a show, then parse its sample script.

    Needs gui_env: see test_setup_create_show_and_parse.
    """
    api_client.predict(
        "Api Parse Show", "podcast", "", "",
        api_name="/setup_create_show",
    )
    output = api_client.predict(
        PODCAST_SAMPLE_TAG, "", 0, False, True, False, "",
        api_name="/run_parse",
    )
    assert "[exit 0]" in output
    assert "Total entries:" in output

    parsed_path = os.path.join(
        workspace_dir, "parsed", "apiparseshow", f"parsed_{PODCAST_SAMPLE_TAG}.json"
    )
    assert os.path.exists(parsed_path)


# ---------- Timeline sound-profile editor (double-click -> modal -> journal) ----------

# Dumped to disk under /tmp (not the scratchpad) at the user's request, so the
# artifacts survive this pytest run for manual review.
_REPORT_DIR = os.path.join(
    "/tmp", "xil-timeline-sfx-test",
    datetime.datetime.now().strftime("%Y%m%d-%H%M%S"),
)


def test_timeline_sfx_edit_dialog_writes_journal(page, gradio_server, workspace_dir, gui_env):
    """Double-click a MUSIC span in a real rendered timeline, edit its sound
    profile in the modal, save, and confirm the edit landed both in the sfx
    config and in the append-only journal (sfx_{tag}_edits.jsonl) that
    survives config regeneration — see docs/sfx-reuse-guide equivalent,
    sfx_common.append_sfx_edit / XILU020_sfx_restore.

    Drives the actual frontend JS in timeline_viz.py (dblclick listener,
    #sfx-modal fields, fetch to /xil/get-sfx and /xil/update-sfx) rather than
    calling the FastAPI routes directly (that's already covered by
    TestSfxRouteJournaling in test_xil_gui.py) — this closes the gap between
    "the backend endpoint works" and "the browser dialog actually calls it."

    The timeline HTML is rendered directly (not via a full parse/produce/daw
    run — no audio needed) and loaded through Gradio's own file-serving route
    so relative fetch('/xil/...') calls resolve against gradio_server's
    origin, exactly like the real Timeline tab iframe.
    """
    from xil_pipeline import timeline_viz

    slug, tag = "timelinesfxshow", "S01E01"
    effect_key = "MUSIC: THEME"

    cfg_dir = os.path.join(workspace_dir, "configs", slug)
    os.makedirs(cfg_dir, exist_ok=True)
    sfx_path = os.path.join(cfg_dir, f"sfx_{tag}.json")
    sfx_config = {
        "show": "Timeline SFX Show", "season": 1, "episode": 1,
        "defaults": {"music_volume_percentage": 100},
        "effects": {effect_key: {"source": "SFX/theme.mp3", "duration_seconds": 5.0}},
    }
    with open(sfx_path, "w", encoding="utf-8") as f:
        json.dump(sfx_config, f, indent=2)

    td = timeline_viz.TimelineData(
        tag=tag,
        total_duration_s=10.0,
        layers={
            "music": [
                timeline_viz.LayerSpan(start_s=0.0, end_s=5.0, label=effect_key),
            ],
        },
    )
    html_path = os.path.join(workspace_dir, f"{tag}_timeline_sfx_test.html")
    timeline_viz.render_html_timeline(td, html_path, slug=slug, tag=tag)

    os.makedirs(_REPORT_DIR, exist_ok=True)
    shutil.copy(html_path, os.path.join(_REPORT_DIR, "rendered_timeline.html"))

    # Same URL scheme the real Timeline tab iframe uses (xil_gui._timeline_iframe_html)
    # so the page's origin matches gradio_server and relative /xil/* fetches resolve.
    page.goto(f"{gradio_server}/gradio_api/file={os.path.abspath(html_path)}")

    span = page.locator(".span[data-effect-key]")
    expect(span).to_have_count(1, timeout=10_000)
    assert span.get_attribute("data-effect-key") == effect_key

    span.dblclick()

    modal = page.locator("#sfx-modal-overlay")
    expect(modal).to_be_visible(timeout=5_000)
    expect(page.locator("#sfx-modal-title")).to_have_text(effect_key)

    volume_field = page.locator("#sfxf-volume_percentage")
    volume_field.fill("42")
    ramp_in_field = page.locator("#sfxf-ramp_in_seconds")
    ramp_in_field.fill("1.5")

    page.screenshot(path=os.path.join(_REPORT_DIR, "modal_before_save.png"))

    page.locator("#sfx-modal-save").click()

    status = page.locator("#sfx-modal-status")
    expect(status).to_have_text("Saved 'MUSIC: THEME' — re-run xil daw to apply.", timeout=10_000)

    page.screenshot(path=os.path.join(_REPORT_DIR, "modal_after_save.png"))

    # --- On-disk assertions: config updated, journal appended ---

    with open(sfx_path, encoding="utf-8") as f:
        saved_config = json.load(f)
    effect = saved_config["effects"][effect_key]
    assert effect["volume_percentage"] == 42
    assert effect["ramp_in_seconds"] == 1.5

    journal_path = os.path.join(cfg_dir, f"sfx_{tag}_edits.jsonl")
    assert os.path.exists(journal_path), f"journal was not created at {journal_path}"
    journal_lines = [
        json.loads(line) for line in open(journal_path, encoding="utf-8") if line.strip()
    ]
    assert len(journal_lines) == 1
    record = journal_lines[0]
    assert record["key"] == effect_key
    assert record["fields"]["volume_percentage"] == 42
    assert record["fields"]["ramp_in_seconds"] == 1.5
    assert record["fields"]["ramp_out_seconds"] is None
    assert record["fields"]["play_duration"] is None

    shutil.copy(sfx_path, os.path.join(_REPORT_DIR, f"sfx_{tag}.json"))
    shutil.copy(journal_path, os.path.join(_REPORT_DIR, f"sfx_{tag}_edits.jsonl"))
    with open(os.path.join(_REPORT_DIR, "README.txt"), "w", encoding="utf-8") as f:
        f.write(
            "Timeline sound-profile editor test artifacts\n"
            f"Generated: {datetime.datetime.now().isoformat()}\n\n"
            "rendered_timeline.html   — the standalone timeline page under test\n"
            "modal_before_save.png    — dialog after filling volume=42, ramp_in=1.5\n"
            "modal_after_save.png     — dialog showing the 'Saved' confirmation\n"
            f"sfx_{tag}.json           — config after the save (effects.{effect_key!r})\n"
            f"sfx_{tag}_edits.jsonl    — append-only journal (1 record expected)\n"
        )
    print(f"\nTimeline SFX edit test artifacts: {_REPORT_DIR}")
