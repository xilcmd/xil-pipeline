# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared pytest fixtures.

Autouse fixture: unset XIL_PROJECTROOT for every test so that
get_workspace_root() falls back to Path.cwd() — the behaviour all
existing tests assume.  Tests that deliberately need XIL_PROJECTROOT
can re-set it via monkeypatch.setenv("XIL_PROJECTROOT", ...).
"""

import pytest


@pytest.fixture(autouse=True)
def _clear_xil_projectroot(monkeypatch):
    monkeypatch.delenv("XIL_PROJECTROOT", raising=False)
