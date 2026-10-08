# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for mmaudio_worker's weight-path anchoring (no MMAudio import needed)."""

import dataclasses
import sys
from pathlib import Path

from xil_pipeline import mmaudio_worker


@dataclasses.dataclass
class _Cfg:
    model_path: Path
    vae_path: Path
    bigvgan_16k_path: Path | None
    synchformer_ckpt: Path = Path("./ext_weights/synchformer_state_dict.pth")


def test_relative_paths_anchor_under_base(tmp_path):
    cfg = _Cfg(Path("./weights/m.pth"), Path("./ext_weights/v1-44.pth"), None)
    out = mmaudio_worker.anchor_paths(cfg, tmp_path)
    assert out.model_path == tmp_path / "weights" / "m.pth"
    assert out.vae_path == tmp_path / "ext_weights" / "v1-44.pth"
    assert out.bigvgan_16k_path is None
    assert out.synchformer_ckpt == tmp_path / "ext_weights" / "synchformer_state_dict.pth"
    assert cfg.model_path == Path("./weights/m.pth")  # the shared config is untouched


def test_absolute_paths_are_kept(tmp_path):
    cfg = _Cfg(tmp_path / "m.pth", Path("./ext_weights/v.pth"), Path("./ext_weights/b.pt"))
    out = mmaudio_worker.anchor_paths(cfg, Path("/elsewhere"))
    assert out.model_path == tmp_path / "m.pth"
    assert out.bigvgan_16k_path == Path("/elsewhere/ext_weights/b.pt")


def test_weights_base_default_and_override(tmp_path, monkeypatch):
    monkeypatch.delenv("XIL_MMAUDIO_WEIGHTS", raising=False)
    assert mmaudio_worker.weights_base() == Path(sys.prefix).parent
    monkeypatch.setenv("XIL_MMAUDIO_WEIGHTS", str(tmp_path))
    assert mmaudio_worker.weights_base() == tmp_path
