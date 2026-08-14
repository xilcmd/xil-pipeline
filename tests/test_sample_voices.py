# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU004_sample_voices_T2S.py — voice sample generator (non-API functions)."""

import os
import unittest.mock

# Patch out ElevenLabs client before loading module (no API key needed for these tests)
with unittest.mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key"}):
    with unittest.mock.patch("elevenlabs.client.ElevenLabs"):
        from xil_pipeline import XILU004_sample_voices_T2S as sampler


class TestChatterboxDeviceFlag:
    """--device lets a user force cpu even when cuda would work (e.g. GPU busy
    with another job); the worker itself already auto-falls back to cpu when
    cuda is unavailable, so this flag is an override, not the fallback path."""

    def test_device_flag_defaults_to_cuda(self):
        parser = sampler.get_parser()
        action = next(a for a in parser._actions if "--device" in a.option_strings)
        assert action.default == "cuda"
        assert set(action.choices) == {"cuda", "cpu"}

    def test_client_default_device_is_cuda(self):
        client = sampler._ChatterboxClient(python_path="/x/python3", voice_refs_dir="voice_refs")
        assert client._device == "cuda"

    def test_client_accepts_explicit_cpu(self):
        client = sampler._ChatterboxClient(
            python_path="/x/python3", voice_refs_dir="voice_refs", device="cpu",
        )
        assert client._device == "cpu"
