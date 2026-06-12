# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Integration tests for xil-gui Setup tab using gradio_client.

These tests start a real xil-gui server process (on port 7863) and exercise
the two Setup tab actions end-to-end via the Gradio HTTP API:

  /setup_create_show  — "▶ Create show" button (run_init_and_refresh)
  /setup_use_show     — "▶ Use this show" button (_use_show_and_reload)

Skip automatically when gradio_client is not installed or the gui extra is
absent (e.g. in a bare-minimum CI environment).
"""

import json
import os
import subprocess
import sys
import time

import pytest

gradio_client = pytest.importorskip(
    "gradio_client",
    reason="gradio_client not installed — skipping GUI integration tests",
)
Client = gradio_client.Client

_PORT = 7863
_URL = f"http://127.0.0.1:{_PORT}"
_STARTUP_TIMEOUT = 45  # seconds to wait for Gradio to bind


# ── Session-scoped fixtures ──────────────────────────────────────────────────

@pytest.fixture(scope="session")
def gui_workspace(tmp_path_factory):
    """Isolated workspace directory for the GUI server session."""
    return tmp_path_factory.mktemp("gui_ws")


@pytest.fixture(scope="session")
def gui_client(gui_workspace):
    """Start xil-gui on _PORT, yield (workspace_path, Client), then terminate."""
    env = {**os.environ, "XIL_PROJECTROOT": str(gui_workspace)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "xil_pipeline.xil_gui", "--port", str(_PORT)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Poll until the server is ready or timeout expires.
    deadline = time.time() + _STARTUP_TIMEOUT
    client = None
    while time.time() < deadline:
        try:
            client = Client(_URL, verbose=False)
            break
        except Exception:
            time.sleep(1)

    if client is None:
        proc.terminate()
        pytest.fail(f"xil-gui did not start within {_STARTUP_TIMEOUT}s on {_URL}")

    yield gui_workspace, client

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# ── Helper ───────────────────────────────────────────────────────────────────

def _call_create_show(client, show_name, content_type="podcast", season="", season_title=""):
    """Call /setup_create_show and return the final (log_text, dropdown_update) tuple."""
    job = client.submit(show_name, content_type, season, season_title,
                        api_name="/setup_create_show")
    result = None
    for result in job:
        pass
    return result  # last yielded value: (log_str, dropdown_dict)


# ── Tests: /setup_create_show ────────────────────────────────────────────────

class TestSetupCreateShow:
    def test_create_podcast_show(self, gui_client):
        """Creating a podcast show produces a success log and updates the dropdown."""
        workspace, client = gui_client
        result = _call_create_show(client, "Night Owls")
        log, dropdown = result
        assert "Night Owls" in log or "Created" in log or "nightowls" in log
        # Dropdown choices should now include the new show
        choices = dropdown.get("choices", []) if isinstance(dropdown, dict) else []
        assert any("Night Owls" in str(c) for c in choices)

    def test_create_drama_show(self, gui_client):
        """Creating a drama-type show scaffolds the right structure."""
        workspace, client = gui_client
        result = _call_create_show(client, "The Harbor", content_type="drama")
        log, _dropdown = result
        assert "The Harbor" in log or "theharbor" in log or "Created" in log
        # configs/theharbor/project.json should exist on disk
        pj = workspace / "configs" / "theharbor" / "project.json"
        assert pj.exists(), f"project.json not found at {pj}"
        data = json.loads(pj.read_text())
        assert data["show"] == "The Harbor"
        assert data["type"] == "drama"

    def test_create_show_with_season(self, gui_client):
        """Season number and title are written to project.json."""
        workspace, client = gui_client
        result = _call_create_show(
            client, "River Run", season="3", season_title="The Crossing"
        )
        log, _dropdown = result
        pj = workspace / "configs" / "riverrun" / "project.json"
        assert pj.exists()
        data = json.loads(pj.read_text())
        assert data["season"] == 3
        assert data["season_title"] == "The Crossing"

    def test_create_show_sets_active(self, gui_client):
        """After creating a show, .active_show points to the new slug."""
        workspace, client = gui_client
        _call_create_show(client, "Pilot Wave")
        active = (workspace / ".active_show").read_text(encoding="utf-8").strip()
        assert active == "pilotwave"

    def test_create_show_creates_sample_script(self, gui_client):
        """scaffold() writes a sample script to scripts/."""
        workspace, client = gui_client
        _call_create_show(client, "Echo Bay")
        scripts = list((workspace / "scripts").glob("sample_*.md"))
        assert scripts, "No sample script found in scripts/"

    def test_create_show_empty_name_is_rejected(self, gui_client):
        """An empty show name should not create any workspace files."""
        workspace, client = gui_client
        before = set(workspace.rglob("project.json"))
        result = _call_create_show(client, "")
        log, _dd = result
        after = set(workspace.rglob("project.json"))
        # No new project.json should have been created
        assert after == before, f"Unexpected new project.json: {after - before}"


# ── Tests: /setup_use_show ───────────────────────────────────────────────────

class TestSetupUseShow:
    @pytest.fixture(autouse=True)
    def ensure_night_owls(self, gui_client):
        """Make sure Night Owls exists before use-show tests run."""
        workspace, client = gui_client
        pj = workspace / "configs" / "nightowls" / "project.json"
        if not pj.exists():
            _call_create_show(client, "Night Owls")

    def test_use_show_updates_active_show_file(self, gui_client):
        """Selecting a show writes its slug to .active_show."""
        workspace, client = gui_client
        _call_create_show(client, "Harbor Lights")  # ensure a second show exists
        client.predict("Night Owls", api_name="/setup_use_show")
        active = (workspace / ".active_show").read_text(encoding="utf-8").strip()
        assert active == "nightowls"

    def test_use_show_returns_status_message(self, gui_client):
        """The status output is non-empty after selecting a show."""
        workspace, client = gui_client
        status, _content, _path = client.predict(
            "Night Owls", api_name="/setup_use_show"
        )
        assert isinstance(status, str) and status.strip()

    def test_use_show_reloads_project_json_path(self, gui_client):
        """The returned file path points to the selected show's project.json."""
        workspace, client = gui_client
        _status, _content, path = client.predict(
            "Night Owls", api_name="/setup_use_show"
        )
        assert "nightowls" in path
        assert path.endswith("project.json")

    def test_use_show_reloads_project_json_content(self, gui_client):
        """The returned JSON content matches the selected show."""
        workspace, client = gui_client
        _status, content, _path = client.predict(
            "Night Owls", api_name="/setup_use_show"
        )
        data = json.loads(content)
        assert data["show"] == "Night Owls"


# ── Tests: --output activity log ─────────────────────────────────────────────

class TestActivityLog:
    """Start a separate xil-gui instance with --output and verify log content."""

    _LOG_PORT = 7864

    @pytest.fixture(scope="class")
    def logged_server(self, tmp_path_factory):
        """Start xil-gui with --output on a separate port, yield (workspace, client, log_path)."""
        workspace = tmp_path_factory.mktemp("log_ws")
        log_path = workspace / "activity.log"
        env = {**os.environ, "XIL_PROJECTROOT": str(workspace)}
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "xil_pipeline.xil_gui",
                "--port", str(self._LOG_PORT),
                "--output", str(log_path),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + _STARTUP_TIMEOUT
        client = None
        while time.time() < deadline:
            try:
                client = Client(f"http://127.0.0.1:{self._LOG_PORT}", verbose=False)
                break
            except Exception:
                time.sleep(1)
        if client is None:
            proc.terminate()
            pytest.fail(f"xil-gui (--output) did not start within {_STARTUP_TIMEOUT}s")
        yield workspace, client, log_path
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def test_log_file_created(self, logged_server):
        """The activity log file is created when --output is given."""
        _workspace, _client, log_path = logged_server
        assert log_path.exists(), f"Activity log not found at {log_path}"

    def test_create_show_writes_cmd_to_log(self, logged_server):
        """Running 'Create show' writes a CMD: line to the activity log."""
        workspace, client, log_path = logged_server
        job = client.submit("Log Test Show", "podcast", "", "",
                            api_name="/setup_create_show")
        for _ in job:
            pass
        content = log_path.read_text(encoding="utf-8")
        assert "CMD:" in content
        assert "xil_init" in content

    def test_create_show_writes_exit_to_log(self, logged_server):
        """Running 'Create show' writes an [exit 0] line after completion."""
        _workspace, _client, log_path = logged_server
        content = log_path.read_text(encoding="utf-8")
        assert "[exit 0]" in content

    def test_use_show_writes_use_to_log(self, logged_server):
        """Selecting a show writes a USE show line to the activity log."""
        workspace, client, log_path = logged_server
        pj = workspace / "configs" / "logtestshow" / "project.json"
        if not pj.exists():
            job = client.submit("Log Test Show", "podcast", "", "",
                                api_name="/setup_create_show")
            for _ in job:
                pass
        client.predict("Log Test Show", api_name="/setup_use_show")
        content = log_path.read_text(encoding="utf-8")
        assert "USE show" in content
        assert "Log Test Show" in content

    def test_log_has_timestamps(self, logged_server):
        """Every line in the activity log starts with an ISO timestamp."""
        _workspace, _client, log_path = logged_server
        lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert lines, "Activity log is empty"
        import re
        ts_re = re.compile(r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\]")
        for line in lines:
            assert ts_re.match(line), f"Missing timestamp on line: {line!r}"
