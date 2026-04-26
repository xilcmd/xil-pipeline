# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XIL Pipeline — show-agnostic audio production pipeline."""

__version__ = "0.1.8"

from xil_pipeline.models import (
    TYPE_DEFAULTS,
    CastConfiguration,
    ParsedScript,
    ProjectConfig,
    ScriptEntry,
    SfxConfiguration,
    derive_paths,
    derive_paths_legacy,
    get_workspace_root,
    load_project_config,
    resolve_project_type,
    resolve_slug,
    show_slug,
)

__all__ = [
    "CastConfiguration",
    "ParsedScript",
    "ProjectConfig",
    "ScriptEntry",
    "SfxConfiguration",
    "TYPE_DEFAULTS",
    "derive_paths",
    "derive_paths_legacy",
    "get_workspace_root",
    "load_project_config",
    "resolve_project_type",
    "resolve_slug",
    "show_slug",
]
