# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU023_setup.py — ``xil setup chatterbox``.

Mirrors the Rust ``cmd::setup`` tests; command strings must match the Rust
output exactly (parity).
"""

import os
import sys
from pathlib import Path

import pytest

from xil_pipeline import XILU023_setup as setup

# venv-chatterbox (bin/python3) is POSIX-only, and the fakes are sh scripts.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX venv layout")


def _script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _opts(venv: Path, **kw) -> setup.Opts:
    base = dict(
        venv=str(venv),
        python_ver="3.13",
        cuda=False,
        cuda_index="cu124",
        force=False,
        dry_run=False,
        coderoot_set=True,
    )
    base.update(kw)
    return setup.Opts(**base)


def test_device_choice():
    assert setup.use_cuda("auto", True)
    assert not setup.use_cuda("auto", False)
    assert setup.use_cuda("cuda", False)
    assert not setup.use_cuda("cpu", True)


def test_dir_precedence():
    ws = Path("/ws")
    assert setup.target_dir("/x", Path("/code"), ws) == "/x"
    assert setup.target_dir(None, Path("/code"), ws) == "/code"
    assert setup.target_dir(None, None, ws) == "/ws"


def test_uv_plan_uses_cuda_or_cpu_index():
    uv = setup.Installer("uv", "/bin/uv")
    cuda = setup.plan(uv, "/c/venv-chatterbox", "3.13", True, "cu124")
    assert [s.display() for s in cuda] == [
        "/bin/uv venv /c/venv-chatterbox --python 3.13",
        "/bin/uv pip install --python /c/venv-chatterbox/bin/python3 torch==2.6.0 "
        "torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124",
        "/bin/uv pip install --python /c/venv-chatterbox/bin/python3 chatterbox-tts pydub",
    ]
    cpu = setup.plan(uv, "/c/venv-chatterbox", "3.13", False, "cu124")
    assert cpu[1].display().endswith("/whl/cpu")


def test_pip_plan_runs_inside_the_venv():
    pip = setup.Installer("pip", "/usr/bin/python3")
    steps = setup.plan(pip, "/c/venv-chatterbox", "3.13", False, "cu124")
    assert len(steps) == 4
    assert steps[0].display() == "/usr/bin/python3 -m venv /c/venv-chatterbox"
    assert all(s.program == "/c/venv-chatterbox/bin/python3" for s in steps[1:])
    assert steps[2].display().endswith("--index-url https://download.pytorch.org/whl/cpu")


def test_dry_run_runs_nothing(tmp_path, capsys):
    log = tmp_path / "calls"
    uv = tmp_path / "uv"
    _script(uv, f'echo "$@" >> {log}')
    code = setup.execute(_opts(tmp_path / setup.VENV, dry_run=True), setup.Installer("uv", str(uv)))
    assert code == 0
    assert not log.exists()
    assert "pip install" in capsys.readouterr().out
    assert not (tmp_path / setup.VENV).exists()


def test_working_venv_is_left_alone(tmp_path, capsys):
    venv = tmp_path / setup.VENV
    _script(Path(setup.venv_python(str(venv))), "echo True")
    assert setup.execute(_opts(venv), None) == 0
    assert "already set up (CUDA)" in capsys.readouterr().out


def test_builds_with_fake_uv_then_verifies(tmp_path, capsys):
    venv = tmp_path / setup.VENV
    log = tmp_path / "calls"
    uv = tmp_path / "uv"
    # `uv venv <dir>` drops a python3 that passes the import check.
    _script(
        uv,
        f'echo "$@" >> {log}\n'
        'if [ "$1" = venv ]; then mkdir -p "$2/bin"; '
        "printf '#!/bin/sh\\necho False\\n' > \"$2/bin/python3\"; chmod +x \"$2/bin/python3\"; fi",
    )
    code = setup.execute(_opts(venv), setup.Installer("uv", str(uv)))
    out = capsys.readouterr().out
    assert code == 0, out
    calls = log.read_text()
    assert len(calls.splitlines()) == 3
    assert "chatterbox-tts pydub" in calls
    assert "Verified" in out


def test_broken_venv_needs_force(tmp_path):
    venv = tmp_path / setup.VENV
    _script(Path(setup.venv_python(str(venv))), "exit 1")
    assert setup.execute(_opts(venv), setup.Installer("uv", "/nonexistent/uv")) == 1


def test_main_defaults_to_code_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XIL_CODEROOT", str(tmp_path))
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path / "ws"))
    monkeypatch.setattr("sys.argv", ["xil-setup", "chatterbox", "--dry-run", "--device", "cpu"])
    assert setup.main() == 0
    assert f"Setting up {tmp_path / setup.VENV} with CPU" in capsys.readouterr().out


@pytest.mark.parametrize("target", ["bogus", ""])
def test_rejects_unknown_target(target, monkeypatch):
    monkeypatch.setattr("sys.argv", ["xil-setup", target])
    with pytest.raises(SystemExit) as e:
        setup.main()
    assert e.value.code == 2


def test_sample_finds_venv_under_code_root(tmp_path, monkeypatch):
    """xil sample resolves venv-chatterbox like produce: $XIL_CODEROOT first."""
    import json
    import unittest.mock

    with unittest.mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key"}):
        with unittest.mock.patch("elevenlabs.client.ElevenLabs"):
            from xil_pipeline import XILU004_sample_voices_T2S as sampler

    code = tmp_path / "code"
    py = code / "venv-chatterbox" / "bin" / "python3"
    _script(py, "exit 0")
    cast = tmp_path / "cast.json"
    cast.write_text(json.dumps({"show": "x", "season": 1, "episode": 1, "cast": {}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XIL_CODEROOT", str(code))
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv", ["xil-sample", "--cast", str(cast), "--backend", "chatterbox-turbo"]
    )

    class Found(Exception):
        pass

    def client(python_path, **_kw):
        raise Found(python_path)

    monkeypatch.setattr(sampler, "_ChatterboxClient", client)
    with pytest.raises(Found) as e:
        sampler.main()
    assert e.value.args[0] == str(py)


def test_gui_default_honours_code_root(tmp_path, monkeypatch):
    xil_gui = pytest.importorskip("xil_pipeline.xil_gui")
    py = tmp_path / "venv-chatterbox" / "bin" / "python3"
    _script(py, "exit 0")
    monkeypatch.setenv("XIL_CODEROOT", str(tmp_path))
    assert xil_gui._default_chatterbox_python() == str(py)
    monkeypatch.setenv("XIL_CODEROOT", str(tmp_path / "empty"))
    assert xil_gui._default_chatterbox_python() == ""
