# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Root conftest — in-process Gradio server fixtures for xil-gui UI tests.

Complements the subprocess-based tests in tests/test_xil_gui_integration.py:
these fixtures launch the dashboard in-process (fast, ephemeral port) for
gradio_client API tests and optional Playwright UI smoke tests.

Requirements (beyond the ``[gui]``/dev extras, which cover gradio and
gradio_client)::

    pip install pytest-playwright
    playwright install chromium

All heavy imports happen inside fixtures, so collecting the suite never
fails when gradio or playwright is absent — tests that request these
fixtures are skipped instead.

Interaction note: the autouse fixture in tests/conftest.py deletes
``XIL_PROJECTROOT`` for every test. The server built here captures its
workspace at build time, but xil_gui callbacks re-resolve
``get_workspace_root()`` per request — tests that exercise
workspace-dependent callbacks should also request ``gui_env`` to pin the
environment for the duration of the test.
"""

import socket

import pytest


def _free_port() -> int:
    """Grab an ephemeral port so parallel CI jobs don't collide."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def workspace_dir(tmp_path_factory):
    """Empty, isolated workspace directory the GUI session is bound to."""
    return tmp_path_factory.mktemp("gui-workspace")


@pytest.fixture(scope="session")
def gradio_server(workspace_dir):
    """Launch the xil-gui dashboard once, in-process, for the whole session.

    Mirrors xil_gui.main(): builds via _build_app(), launches with
    prevent_thread_lock=True so pytest keeps control, then attaches the
    /xil/* FastAPI routes (only possible after launch, when demo.app is
    the serving FastAPI instance). Yields the base URL.
    """
    pytest.importorskip("gradio", reason="requires the [gui] extra")

    mp = pytest.MonkeyPatch()
    mp.setenv("XIL_PROJECTROOT", str(workspace_dir))

    from xil_pipeline.xil_gui import _build_app, _register_sfx_routes

    port = _free_port()
    demo = _build_app()
    demo.launch(
        server_name="127.0.0.1",
        server_port=port,
        prevent_thread_lock=True,
        allowed_paths=[str(workspace_dir)],
        show_error=True,
        quiet=True,
    )
    _register_sfx_routes(demo.app)
    yield f"http://127.0.0.1:{port}"
    demo.close()
    mp.undo()


@pytest.fixture
def gui_env(workspace_dir, monkeypatch):
    """Pin XIL_PROJECTROOT to the GUI session workspace for one test.

    Needed because tests/conftest.py clears the variable per test while
    xil_gui callbacks re-resolve the workspace on every request.
    """
    monkeypatch.setenv("XIL_PROJECTROOT", str(workspace_dir))
    return workspace_dir


@pytest.fixture(scope="session")
def base_url(gradio_server):
    """pytest-playwright reads this fixture name for page.goto('/') support."""
    return gradio_server


@pytest.fixture
def app_page(page, gradio_server):
    """A Playwright page navigated to the dashboard, with Gradio hydrated.

    Gradio is a Svelte SPA — 'load' fires before components mount, so wait
    for the dashboard's own "# xil-pipeline" heading (xil_gui.py renders it
    as the first Markdown block; generated Gradio class names are NOT
    stable across versions).
    """
    page.goto(gradio_server)
    page.get_by_role("heading", name="xil-pipeline").wait_for(timeout=15_000)
    return page


@pytest.fixture
def create_show_via_ui(page):
    """Callable that drives the Setup tab's "Create show" flow to completion.

    A fixture (not a plain importable function) because two conftest.py
    files exist in this repo (this one and tests/conftest.py) — "conftest"
    as a bare module name is ambiguous for direct imports, but pytest's own
    fixture resolution has no such ambiguity. Shared by test_webapp_smoke.py
    and test_gui_pipeline_lifecycle.py, both of which need a fresh show
    scaffolded through the real UI before exercising later stages.
    """
    def _create(show_name: str) -> None:
        page.locator("#init-show-name textarea").fill(show_name)
        page.locator("#init-create-btn").click()
        page.wait_for_function(
            "document.querySelector('#init-log textarea').value.includes('[exit')",
            timeout=30_000,
        )
        log = page.locator("#init-log textarea").input_value()
        assert "[exit 0]" in log, f"show creation failed:\n{log}"

    return _create


@pytest.fixture(scope="session")
def api_client(gradio_server):
    """gradio_client for API-level regression tests (fast path).

    Use this for pipeline-logic assertions; save Playwright for UI smoke
    tests.
    """
    from gradio_client import Client

    return Client(gradio_server)
