# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the pluggable SFX backend layer (sfx_backends + sfx_common wiring).

These tests never load the real AudioLDM 2 model or call the ElevenLabs API.
The local-model path is exercised with a fake backend and a stub worker script,
mirroring how test_pipeline_lifecycle.py uses gtts to avoid heavy dependencies.
"""

import json
import os
import sys
import textwrap

import pytest
from pydub import AudioSegment

from xil_pipeline.sfx_backends import (
    AudioLDM2SfxBackend,
    ElevenLabsSfxBackend,
    SfxBackend,
    StableAudioSfxBackend,
    _AudioLDM2Client,
    _StableAudioClient,
    make_sfx_backend,
)
from xil_pipeline.sfx_common import generate_sfx, shared_sfx_path

# ── shared_sfx_path backend tagging ──────────────────────────────────────────


class TestSharedSfxPath:
    def test_elevenlabs_keeps_plain_name(self):
        p = shared_sfx_path("SFX", "SFX: DOOR OPENS")
        assert os.path.basename(p) == "sfx_door-opens.mp3"

    def test_elevenlabs_explicit_keeps_plain_name(self):
        p = shared_sfx_path("SFX", "SFX: DOOR OPENS", backend="elevenlabs")
        assert os.path.basename(p) == "sfx_door-opens.mp3"

    def test_audioldm2_is_backend_tagged(self):
        p = shared_sfx_path("SFX", "SFX: DOOR OPENS", backend="audioldm2")
        assert os.path.basename(p) == "sfx_door-opens.audioldm2.mp3"

    def test_stableaudio_is_backend_tagged(self):
        p = shared_sfx_path("SFX", "SFX: DOOR OPENS", backend="stableaudio")
        assert os.path.basename(p) == "sfx_door-opens.stableaudio.mp3"

    def test_beat_plain_for_elevenlabs(self):
        assert os.path.basename(shared_sfx_path("SFX", "BEAT")) == "beat.mp3"


# ── make_sfx_backend factory ─────────────────────────────────────────────────


class TestMakeSfxBackend:
    def test_elevenlabs_backend(self):
        backend = make_sfx_backend("elevenlabs", client=object())
        assert isinstance(backend, ElevenLabsSfxBackend)
        assert backend.name == "elevenlabs"

    def test_audioldm2_backend_explicit_python(self):
        # Explicit python path skips venv auto-detection (no sys.exit).
        backend = make_sfx_backend("audioldm2", audioldm2_python=sys.executable)
        assert isinstance(backend, AudioLDM2SfxBackend)
        assert backend.name == "audioldm2"

    def test_audioldm2_forwards_params(self):
        backend = make_sfx_backend(
            "audioldm2", audioldm2_python=sys.executable,
            guidance=5.0, steps=42, negative_prompt="hiss",
        )
        client = backend._client
        assert client._guidance == 5.0
        assert client._steps == 42
        assert client._negative_prompt == "hiss"

    def test_stableaudio_backend_explicit_python(self):
        backend = make_sfx_backend("stableaudio", stableaudio_python=sys.executable)
        assert isinstance(backend, StableAudioSfxBackend)
        assert backend.name == "stableaudio"

    def test_stableaudio_forwards_params(self):
        backend = make_sfx_backend(
            "stableaudio", stableaudio_python=sys.executable,
            guidance=6.5, steps=80, negative_prompt="hum", seed=42,
        )
        client = backend._client
        assert client._python == sys.executable
        assert client._guidance == 6.5
        assert client._steps == 80
        assert client._negative_prompt == "hum"
        assert client._seed == 42

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown sfx backend"):
            make_sfx_backend("nope")

    def test_backends_satisfy_protocol(self):
        el = make_sfx_backend("elevenlabs", client=object())
        a2 = make_sfx_backend("audioldm2", audioldm2_python=sys.executable)
        sa = make_sfx_backend("stableaudio", stableaudio_python=sys.executable)
        assert isinstance(el, SfxBackend)
        assert isinstance(a2, SfxBackend)
        assert isinstance(sa, SfxBackend)


# ── ElevenLabsSfxBackend ─────────────────────────────────────────────────────


class _FakeConvertClient:
    """Mimics the ElevenLabs client's text_to_sound_effects.convert streaming."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []

        class _T2S:
            def convert(_self, **kwargs):
                self.calls.append(kwargs)
                return iter(self._chunks)

        self.text_to_sound_effects = _T2S()


class TestElevenLabsSfxBackend:
    def test_none_client_raises(self):
        backend = ElevenLabsSfxBackend(None)
        with pytest.raises(ValueError, match="ElevenLabs client is required"):
            backend.generate_to("out.mp3", "door", 2.0, 0.3)

    def test_streams_chunks_to_out_path(self, tmp_path):
        client = _FakeConvertClient([b"AB", b"", b"CD"])
        backend = ElevenLabsSfxBackend(client)
        out = tmp_path / "door.mp3"
        backend.generate_to(str(out), "a door opening", 2.0, 0.3)
        assert out.read_bytes() == b"ABCD"
        # Verify the prompt/duration/influence were forwarded.
        assert client.calls[0]["text"] == "a door opening"
        assert client.calls[0]["duration_seconds"] == 2.0
        assert client.calls[0]["prompt_influence"] == 0.3


# ── AudioLDM2 client subprocess framing (stub worker) ────────────────────────


_STUB_WORKER_OK = textwrap.dedent(
    """
    import json, os, sys
    print(json.dumps({"ready": True, "sr": 16000, "device": "cpu"}), flush=True)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        req = json.loads(raw)
        out = req["out_path"]
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(req, f)
        print(json.dumps({"done": True}), flush=True)
    """
)

_STUB_WORKER_ERR = textwrap.dedent(
    """
    import json, sys
    print(json.dumps({"ready": True, "sr": 16000}), flush=True)
    for raw in sys.stdin:
        if raw.strip():
            print(json.dumps({"error": "boom"}), flush=True)
    """
)


def _write_stub(tmp_path, body):
    stub = tmp_path / "stub_worker.py"
    stub.write_text(body, encoding="utf-8")
    return str(stub)


class TestAudioLDM2Client:
    def test_request_framing_and_response(self, tmp_path):
        client = _AudioLDM2Client(
            python_path=sys.executable, device="cpu",
            guidance=4.0, steps=10, negative_prompt="hiss",
        )
        client._WORKER = _write_stub(tmp_path, _STUB_WORKER_OK)
        out = tmp_path / "out" / "asset.mp3"
        client.generate("dog barking", str(out), 3.0)
        try:
            assert out.exists()
            sent = json.loads(out.read_text(encoding="utf-8"))
            assert sent["prompt"] == "dog barking"
            assert sent["duration_seconds"] == 3.0
            assert sent["guidance_scale"] == 4.0
            assert sent["num_inference_steps"] == 10
            assert sent["negative_prompt"] == "hiss"
        finally:
            client.close()

    def test_worker_error_raises(self, tmp_path):
        client = _AudioLDM2Client(python_path=sys.executable, device="cpu")
        client._WORKER = _write_stub(tmp_path, _STUB_WORKER_ERR)
        try:
            with pytest.raises(RuntimeError, match="AudioLDM 2: boom"):
                client.generate("anything", str(tmp_path / "x.mp3"), 1.0)
        finally:
            client.close()

    def test_close_is_idempotent(self, tmp_path):
        client = _AudioLDM2Client(python_path=sys.executable, device="cpu")
        client.close()  # never started — no-op
        client.close()


# ── StableAudio client subprocess framing (stub worker) ──────────────────────


class TestStableAudioClient:
    def test_request_framing_includes_seed(self, tmp_path):
        client = _StableAudioClient(
            python_path=sys.executable, device="cpu",
            guidance=6.0, steps=50, negative_prompt="hum", seed=7,
        )
        client._WORKER = _write_stub(tmp_path, _STUB_WORKER_OK)
        out = tmp_path / "out" / "asset.mp3"
        client.generate("rain on a tin roof", str(out), 4.0)
        try:
            assert out.exists()
            sent = json.loads(out.read_text(encoding="utf-8"))
            assert sent["prompt"] == "rain on a tin roof"
            assert sent["duration_seconds"] == 4.0
            assert sent["guidance_scale"] == 6.0
            assert sent["num_inference_steps"] == 50
            assert sent["negative_prompt"] == "hum"
            assert sent["seed"] == 7
        finally:
            client.close()

    def test_worker_error_raises_with_label(self, tmp_path):
        client = _StableAudioClient(python_path=sys.executable, device="cpu")
        client._WORKER = _write_stub(tmp_path, _STUB_WORKER_ERR)
        try:
            with pytest.raises(RuntimeError, match="Stable Audio: boom"):
                client.generate("anything", str(tmp_path / "x.mp3"), 1.0)
        finally:
            client.close()

    def test_close_is_idempotent(self, tmp_path):
        client = _StableAudioClient(python_path=sys.executable, device="cpu")
        client.close()  # never started — no-op
        client.close()

    def test_worker_override_does_not_leak_to_audioldm2(self, tmp_path, monkeypatch):
        # Regression for the _DiffusionWorkerClient base-class refactor:
        # patching the stableaudio worker path must not redirect audioldm2.
        monkeypatch.setattr(
            _StableAudioClient, "_WORKER", _write_stub(tmp_path, _STUB_WORKER_OK)
        )
        assert _AudioLDM2Client._WORKER.endswith("audioldm2_worker.py")


# ── generate_sfx end-to-end with a fake local backend ────────────────────────


class _FakeLocalBackend:
    """A stand-in 'audioldm2' backend that writes a tiny valid MP3 locally."""

    name = "audioldm2"

    def __init__(self):
        self.generated = []

    def generate_to(self, out_path, prompt, duration_seconds, prompt_influence):
        self.generated.append((out_path, prompt))
        AudioSegment.silent(duration=100).export(out_path, format="mp3")

    def close(self):
        pass


@pytest.fixture
def sfx_config():
    return {
        "show": "TEST SHOW", "season": 1, "episode": 1,
        "defaults": {"prompt_influence": 0.3},
        "effects": {
            "SFX: TEST TONE": {"prompt": "a short test tone", "duration_seconds": 2.0},
        },
    }


class TestGenerateSfxWithBackend:
    def test_audioldm2_writes_backend_tagged_asset_and_stem(self, tmp_path, sfx_config):
        sfx_dir = str(tmp_path / "SFX")
        stems_dir = str(tmp_path / "stems" / "testshow" / "S01E01")
        entries = [{
            "seq": 5, "text": "SFX: TEST TONE",
            "stem_name": "005_cold-open_sfx",
            "direction_type": "SFX", "sfx_type": "sfx",
            "section": "cold-open", "scene": None,
        }]
        backend = _FakeLocalBackend()

        generate_sfx(entries, sfx_config, stems_dir, sfx_dir=sfx_dir, backend=backend)

        # Shared asset is backend-tagged; episode stem is placed.
        shared = tmp_path / "SFX" / "sfx_test-tone.audioldm2.mp3"
        stem = tmp_path / "stems" / "testshow" / "S01E01" / "005_cold-open_sfx.mp3"
        assert shared.exists(), "backend-tagged shared asset not created"
        assert stem.exists(), "episode stem not placed"
        assert backend.generated and backend.generated[0][1] == "a short test tone"

    def test_stableaudio_writes_backend_tagged_asset_and_stem(self, tmp_path, sfx_config):
        sfx_dir = str(tmp_path / "SFX")
        stems_dir = str(tmp_path / "stems" / "testshow" / "S01E01")
        entries = [{
            "seq": 5, "text": "SFX: TEST TONE",
            "stem_name": "005_cold-open_sfx",
            "direction_type": "SFX", "sfx_type": "sfx",
            "section": "cold-open", "scene": None,
        }]
        backend = _FakeLocalBackend()
        backend.name = "stableaudio"

        generate_sfx(entries, sfx_config, stems_dir, sfx_dir=sfx_dir, backend=backend)

        shared = tmp_path / "SFX" / "sfx_test-tone.stableaudio.mp3"
        stem = tmp_path / "stems" / "testshow" / "S01E01" / "005_cold-open_sfx.mp3"
        assert shared.exists(), "backend-tagged shared asset not created"
        assert stem.exists(), "episode stem not placed"

    def test_elevenlabs_default_keeps_plain_name(self, tmp_path, sfx_config):
        sfx_dir = str(tmp_path / "SFX")
        stems_dir = str(tmp_path / "stems" / "testshow" / "S01E01")
        entries = [{
            "seq": 5, "text": "SFX: TEST TONE",
            "stem_name": "005_cold-open_sfx",
            "direction_type": "SFX", "sfx_type": "sfx",
            "section": "cold-open", "scene": None,
        }]
        # Fake backend that reports the elevenlabs name → plain filename.
        backend = _FakeLocalBackend()
        backend.name = "elevenlabs"

        generate_sfx(entries, sfx_config, stems_dir, sfx_dir=sfx_dir, backend=backend)

        assert (tmp_path / "SFX" / "sfx_test-tone.mp3").exists()
        assert not (tmp_path / "SFX" / "sfx_test-tone.audioldm2.mp3").exists()
