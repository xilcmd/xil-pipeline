# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import sys

from xil_pipeline import xil


def test_main_prints_help_when_no_args(capsys):
    code = xil.main([])
    out = capsys.readouterr().out

    assert code == 0
    assert "Usage: xil <command> [args...]" in out
    assert "scan" in out
    assert "parse" in out


def test_main_unknown_command_returns_2(capsys):
    code = xil.main(["does-not-exist"])
    captured = capsys.readouterr()

    assert code == 2
    assert "Unknown command: does-not-exist" in captured.err


def test_main_delegates_to_run_subcommand(monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(command: str, args: list[str]) -> int:
        seen["command"] = command
        seen["args"] = args
        return 7

    monkeypatch.setattr(xil, "run_subcommand", fake_run)

    code = xil.main(["scan", "script.md", "--json"])

    assert code == 7
    assert seen["command"] == "scan"
    assert seen["args"] == ["script.md", "--json"]


def test_run_subcommand_forwards_args_and_restores_argv(monkeypatch):
    observed: dict[str, object] = {}

    class FakeModule:
        @staticmethod
        def main() -> int:
            observed["argv"] = list(sys.argv)
            return 0

    monkeypatch.setattr(xil, "XIL_SCRIPT_COMMANDS", {"scan": xil.CommandSpec("fake.module", "desc", "pipeline")})
    monkeypatch.setattr(xil.importlib, "import_module", lambda _name: FakeModule)
    monkeypatch.setattr(sys, "argv", ["outer", "value"])

    code = xil.run_subcommand("scan", ["script.md", "--json"])

    assert code == 0
    assert observed["argv"] == ["xil scan", "script.md", "--json"]
    assert sys.argv == ["outer", "value"]


def test_run_subcommand_propagates_systemexit_code(monkeypatch):
    class FakeModule:
        @staticmethod
        def main() -> None:
            raise SystemExit(3)

    monkeypatch.setattr(xil, "XIL_SCRIPT_COMMANDS", {"scan": xil.CommandSpec("fake.module", "desc", "pipeline")})
    monkeypatch.setattr(xil.importlib, "import_module", lambda _name: FakeModule)

    code = xil.run_subcommand("scan", ["script.md"])

    assert code == 3


def test_all_commands_have_valid_group():
    """Every CommandSpec must declare group as 'pipeline' or 'utility'.
    This catches the case where a new command is added without a group."""
    valid_groups = {xil._PIPELINE, xil._UTILITY}
    invalid = {name: spec.group for name, spec in xil.XIL_SCRIPT_COMMANDS.items() if spec.group not in valid_groups}
    assert not invalid, f"Commands with invalid group: {invalid}"
