# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
test_gui_pipeline_lifecycle.py — GUI-driven counterpart to
tests/test_pipeline_lifecycle.py.

That file drives the full production chain (init -> scan -> parse -> produce
-> daw -> master) as six subprocess calls, for fast, precise per-stage
failure attribution. This file exercises the *same* six stages, but through
the real Gradio dashboard in a browser — clicking the actual Run Stage tabs,
dropdowns, and buttons — to catch UI wiring regressions that a subprocess
test structurally cannot see (a broken button, a dropdown bound to the wrong
input, a selector that silently no-ops).

Run:
    pytest tests/test_gui_pipeline_lifecycle.py -v
    pytest tests/test_gui_pipeline_lifecycle.py --headed   # watch it live

Requires:
    - gtts installed (produce uses --backend gtts, no ElevenLabs key needed)
    - pytest-playwright + chromium (`playwright install chromium`)

Screenshots + on-disk artifacts from the run are written to
/tmp/xil-gui-lifecycle-test/<timestamp>/ for manual review (see README.txt
written at the end of the test).
"""

import datetime
import json
import os
import re

import pytest

pytest.importorskip("gtts", reason="gtts not installed — skipping GUI pipeline lifecycle test")
pytest.importorskip("playwright", reason="requires pytest-playwright + chromium")

_SHOW = "GUI Lifecycle Show"
_SLUG = "guilifecycleshow"
_TAG = "S01E01"

_REPORT_DIR = os.path.join(
    "/tmp", "xil-gui-lifecycle-test",
    datetime.datetime.now().strftime("%Y%m%d-%H%M%S"),
)


def _run_stage(page, button_selector, shot, stage_name,
                log_selector="#run-stage-log textarea", timeout=60_000):
    """Screenshot, click a Run Stage button, wait for a *new* '[exit N]'
    marker, then screenshot again.

    The log textarea is shared across every Scan/Produce/Assemble/DAW/Master
    button — after the first stage runs, it already contains '[exit 0]', so
    a naive "wait until the log includes '[exit'" check would resolve
    immediately against stale content from the *previous* stage. Snapshot
    the value before clicking and require it to both change AND contain
    '[exit' before accepting it as this stage's completion.

    *stage_name* is "{NN}_{label}" (e.g. "02_scan") — the before/after shots
    get a "_1_"/"_2_" infix (e.g. "02_1_scan_before.png", "02_2_scan_after.png")
    so before/after pairs sort and click through together in a file browser
    instead of every "_before" landing before every "_after".
    """
    num, label = stage_name.split("_", 1)
    shot(f"{num}_1_{label}_before.png")
    prev = page.locator(log_selector).input_value()
    page.locator(button_selector).click()
    page.wait_for_function(
        "([sel, prev]) => { "
        "  const v = document.querySelector(sel).value; "
        "  return v !== prev && v.includes('[exit'); "
        "}",
        arg=[log_selector, prev],
        timeout=timeout,
    )
    log = page.locator(log_selector).input_value()
    assert "[exit 0]" in log, f"stage failed (button {button_selector!r}):\n{log}"
    shot(f"{num}_2_{label}_after.png")
    return log


def _select_dropdown_option(page, container_selector, option_pattern):
    """Open a Gradio Dropdown and click the option matching *option_pattern*.

    Gradio renders the dropdown trigger as an <input role="listbox"> and its
    options as role="option" items appended at document level (not nested
    under the trigger), so options are looked up globally after opening the
    correct dropdown by its elem_id-scoped container.
    """
    page.locator(f"{container_selector} input").click()
    page.get_by_role("option", name=option_pattern).first.click()


def test_gui_pipeline_lifecycle(page, gradio_server, workspace_dir, gui_env, create_show_via_ui):
    """Walk init -> scan -> parse -> produce -> daw -> master through the
    real browser UI, screenshotting after each stage, then verify the same
    on-disk artifacts test_pipeline_lifecycle.py checks via subprocess."""
    os.makedirs(_REPORT_DIR, exist_ok=True)
    shots = []

    def _shot(name):
        path = os.path.join(_REPORT_DIR, name)
        # full_page=True: several Run Stage tabs (Produce especially) are
        # taller than the viewport, and the button click auto-scrolls the
        # page — a viewport-only screenshot taken right after clicking cuts
        # off the header/nav and whatever scrolled out of view.
        page.screenshot(path=path, full_page=True)
        shots.append(name)

    page.goto(gradio_server)
    page.get_by_role("heading", name="xil-pipeline").wait_for(timeout=15_000)

    # ── Stage 0: init ────────────────────────────────────────────────────
    _shot("01_1_setup_before.png")
    create_show_via_ui(_SHOW)
    _shot("01_2_setup_after.png")

    ws = workspace_dir
    assert os.path.exists(os.path.join(ws, "configs", _SLUG, "project.json"))

    # ── Stage 1: scan ───────────────────────────────────────────────────
    # Scan, Produce, Assemble, DAW, and Master all share one "Episode"
    # dropdown at the top of the Run Stage tab (#run-episode) — only Parse
    # has its own separate field (#parse-episode). Before any episode is
    # parsed, the only available choice is the show stub ("{slug}  [show]
    # -- {name}"), which is what run_scan needs to resolve --show for the
    # scanner (scan itself takes no episode tag).
    page.get_by_role("tab", name="Run Stage").click()
    _select_dropdown_option(page, "#run-episode", re.compile(rf"^{_SLUG}\s+\[show\]"))
    page.get_by_role("tab", name="Scan").click()
    page.locator("#scan-script input").fill(f"scripts/{_SLUG}/sample_{_TAG}.md")
    _run_stage(page, "#scan-run-btn", _shot, "02_scan")

    # ── Stage 2: parse ──────────────────────────────────────────────────
    page.get_by_role("tab", name="Parse").click()
    page.locator("#parse-episode input").fill(_TAG)
    _run_stage(page, "#parse-run-btn", _shot, "03_parse")

    parsed_path = os.path.join(ws, "parsed", _SLUG, f"parsed_{_TAG}.json")
    assert os.path.exists(parsed_path)
    with open(parsed_path, encoding="utf-8") as f:
        parsed = json.load(f)
    dialogue = [e for e in parsed.get("entries", []) if e.get("type") == "dialogue"]
    assert len(dialogue) >= 2, f"Expected >=2 dialogue entries, got {len(dialogue)}"
    assert os.path.exists(os.path.join(ws, "configs", _SLUG, f"cast_{_TAG}.json"))
    assert os.path.exists(os.path.join(ws, "configs", _SLUG, f"sfx_{_TAG}.json"))

    # ── Stage 3: produce (gtts, --local-only) ──────────────────────────
    page.get_by_role("tab", name="Produce").click()
    _select_dropdown_option(page, "#run-episode", re.compile(rf"^{_SLUG}\s+{_TAG}\b"))
    _select_dropdown_option(page, "#prod-backend", re.compile(r"^gtts$"))
    page.locator("#prod-dry-run input").uncheck()
    _run_stage(page, "#prod-run-btn", _shot, "04_produce", timeout=90_000)

    stems_dir = os.path.join(ws, "stems", _SLUG, _TAG)
    assert os.path.isdir(stems_dir)
    stems = [f for f in os.listdir(stems_dir) if f.endswith(".mp3")]
    assert len(stems) >= 2, f"Expected >=2 stems, found {len(stems)}"
    assert os.path.exists(os.path.join(stems_dir, f"{_TAG}_stem_manifest.json"))

    # ── Stage 4: daw ────────────────────────────────────────────────────
    page.get_by_role("tab", name="DAW").click()
    page.locator("#daw-dry-run input").uncheck()
    # GUI default is True; the CLI lifecycle test never passes --macro, and
    # on Linux/WSL it targets a Windows %APPDATA% path — turn it off to
    # match CLI behavior and avoid an unrelated failure.
    page.locator("#daw-macro input").uncheck()
    _run_stage(page, "#daw-run-btn", _shot, "05_daw", timeout=60_000)

    daw_dir = os.path.join(ws, "daw", _SLUG, _TAG)
    assert os.path.isdir(daw_dir)
    for layer in ("dialogue", "ambience", "music", "sfx"):
        wav = os.path.join(daw_dir, f"{_TAG}_layer_{layer}.wav")
        assert os.path.exists(wav), f"DAW layer missing: {layer}"
    dialogue_wav = os.path.join(daw_dir, f"{_TAG}_layer_dialogue.wav")
    assert os.path.getsize(dialogue_wav) > 0

    # ── Stage 5: master ─────────────────────────────────────────────────
    page.get_by_role("tab", name="Master").click()
    page.locator("#master-dry-run input").uncheck()
    _run_stage(page, "#master-run-btn", _shot, "06_master", timeout=30_000)

    masters_dir = os.path.join(ws, "masters")
    mp3s = [f for f in os.listdir(masters_dir) if f.endswith(".mp3")] if os.path.isdir(masters_dir) else []
    assert len(mp3s) == 1, f"Expected exactly 1 master MP3, found: {mp3s}"
    assert _TAG in mp3s[0] and _SLUG in mp3s[0]
    assert os.path.getsize(os.path.join(masters_dir, mp3s[0])) > 0

    # ── Bonus: Timeline tab, real DAW-rendered timeline ────────────────
    page.locator("#global-refresh-btn").click()
    page.wait_for_timeout(500)  # dropdown choices refresh is synchronous but not animation-framed
    page.get_by_role("tab", name="Timeline").click()
    _shot("07_1_timeline_before.png")
    _select_dropdown_option(page, "#tl-episode", re.compile(rf"^{_SLUG}\s+{_TAG}\b"))
    page.locator("#tl-html iframe").wait_for(state="visible", timeout=15_000)
    page.wait_for_timeout(1000)  # let the iframe's own render() pass complete
    _shot("07_2_timeline_after.png")

    # ── Report ──────────────────────────────────────────────────────────
    with open(os.path.join(_REPORT_DIR, "README.txt"), "w", encoding="utf-8") as f:
        f.write(
            "GUI pipeline lifecycle test artifacts\n"
            f"Generated: {datetime.datetime.now().isoformat()}\n"
            f"Show: {_SHOW!r} (slug={_SLUG!r}, tag={_TAG!r})\n\n"
            + "\n".join(shots) + "\n"
        )
    print(f"\nGUI pipeline lifecycle test artifacts: {_REPORT_DIR}")
