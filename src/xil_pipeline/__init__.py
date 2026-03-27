# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""XIL Pipeline — show-agnostic audio production pipeline."""

__version__ = "0.1.0"

from xil_pipeline.models import (
    CastConfiguration,
    ParsedScript,
    ScriptEntry,
    SfxConfiguration,
    derive_paths,
    resolve_slug,
    show_slug,
)

__all__ = [
    "CastConfiguration",
    "ParsedScript",
    "ScriptEntry",
    "SfxConfiguration",
    "derive_paths",
    "resolve_slug",
    "show_slug",
]
