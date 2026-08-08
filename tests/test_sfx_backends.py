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
import unittest.mock

import pytest
from pydub import AudioSegment

from xil_pipeline.sfx_backends import (
    ElevenLabsSfxBackend,
    MMAudioSfxBackend,
    SfxBackend,
    _MMAudioClient,
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

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown sfx backend"):
            make_sfx_backend("nope")

    @pytest.mark.parametrize("removed", ["audioldm2", "stableaudio"])
    def test_removed_backends_raise(self, removed):
        """The local diffusion backends were removed in #62.

        They must raise rather than silently falling back to ElevenLabs — a
        stale script asking for a free local model should not quietly start
        spending API credits.
        """
        with pytest.raises(ValueError, match="Unknown sfx backend"):
            make_sfx_backend(removed, client=object())

    def test_backend_satisfies_protocol(self):
        assert isinstance(make_sfx_backend("elevenlabs", client=object()), SfxBackend)


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


# ── MMAudio ──────────────────────────────────────────────────────────────────

_STUB_WORKER_OK = textwrap.dedent(
    """
    import json, os, sys
    print(json.dumps({"ready": True, "sr": 44100, "device": "cpu"}), flush=True)
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
    print(json.dumps({"ready": True, "sr": 44100}), flush=True)
    for raw in sys.stdin:
        if raw.strip():
            print(json.dumps({"error": "boom"}), flush=True)
    """
)


def _write_stub(tmp_path, body):
    stub = tmp_path / "stub_worker.py"
    stub.write_text(body, encoding="utf-8")
    return str(stub)


class TestMMAudioLicenceGate:
    """MMAudio's weights are CC BY-NC 4.0 — construction must be deliberate.

    Audio that cannot legally appear in a monetised episode must not be
    generatable by accident.
    """

    def test_refuses_without_acknowledgement(self):
        with pytest.raises(ValueError, match="CC BY-NC 4.0"):
            MMAudioSfxBackend(object())

    def test_builds_with_acknowledgement(self):
        backend = MMAudioSfxBackend(object(), accept_noncommercial=True)
        assert backend.name == "mmaudio"

    def test_factory_refuses_without_acknowledgement(self):
        with unittest.mock.patch(
            "xil_pipeline.sfx_backends._find_mmaudio_python", return_value=sys.executable
        ), pytest.raises(ValueError, match="CC BY-NC 4.0"):
            make_sfx_backend("mmaudio")

    def test_asset_comment_carries_the_licence(self):
        backend = MMAudioSfxBackend(object(), accept_noncommercial=True)
        assert "NON-COMMERCIAL" in backend.asset_comment.upper()
        assert "CC BY-NC" in backend.asset_comment

    def test_satisfies_the_backend_protocol(self):
        assert isinstance(MMAudioSfxBackend(object(), accept_noncommercial=True), SfxBackend)


class TestMMAudioDuration:
    """Generate at the training duration, then trim.

    MMAudio is trained at 8 s and warns that deviation degrades quality, but
    the trim cannot be left to the mixer: for a prompt-generated cue
    duration_seconds is the *generation length* and there is no mix-time clip
    (that only applies to source= cues). An untrimmed asset would play long.
    """

    class _RecordingClient:
        def __init__(self):
            self.requested = None

        def generate(self, prompt, out_path, duration_seconds):
            self.requested = duration_seconds
            AudioSegment.silent(duration=int(duration_seconds * 1000)).export(
                out_path, format="mp3"
            )

        def close(self):
            pass

    def test_requests_native_duration_not_the_cue_length(self, tmp_path):
        client = self._RecordingClient()
        backend = MMAudioSfxBackend(client, accept_noncommercial=True)
        out = str(tmp_path / "cue.mp3")

        backend.generate_to(out, "a door slam", 5.0, 0.3)

        assert client.requested == 8.0, "should generate at the training duration"

    def test_result_is_trimmed_to_the_cue_length(self, tmp_path):
        backend = MMAudioSfxBackend(self._RecordingClient(), accept_noncommercial=True)
        out = str(tmp_path / "cue.mp3")

        backend.generate_to(out, "a door slam", 5.0, 0.3)

        length_s = len(AudioSegment.from_file(out)) / 1000.0
        assert 4.5 < length_s < 5.5, f"expected ~5s after trim, got {length_s:.2f}s"

    def test_longer_cue_than_native_is_not_truncated(self, tmp_path):
        """A 12s cue must generate 12s, not be clipped back to 8s."""
        client = self._RecordingClient()
        backend = MMAudioSfxBackend(client, accept_noncommercial=True)
        out = str(tmp_path / "cue.mp3")

        backend.generate_to(out, "long rain", 12.0, 0.3)

        assert client.requested == 12.0
        assert len(AudioSegment.from_file(out)) / 1000.0 > 11.0


class TestMMAudioClientFraming:
    """Worker subprocess protocol, against a stub script."""

    def _client(self, tmp_path, body):
        client = _MMAudioClient(python_path=sys.executable, device="cpu", seed=7)
        client._WORKER = _write_stub(tmp_path, body)
        return client

    def test_request_reaches_the_worker(self, tmp_path):
        client = self._client(tmp_path, _STUB_WORKER_OK)
        out = tmp_path / "out.bin"
        try:
            client.generate("a bell", str(out), 8.0)
        finally:
            client.close()
        sent = json.loads(out.read_text(encoding="utf-8"))
        assert sent["prompt"] == "a bell"
        assert sent["duration_seconds"] == 8.0
        assert sent["seed"] == 7, "seed must ride along via _request_extras"

    def test_worker_error_is_raised(self, tmp_path):
        client = self._client(tmp_path, _STUB_WORKER_ERR)
        try:
            with pytest.raises(RuntimeError, match="boom"):
                client.generate("a bell", str(tmp_path / "x.bin"), 8.0)
        finally:
            client.close()

    def test_close_is_idempotent(self, tmp_path):
        client = self._client(tmp_path, _STUB_WORKER_OK)
        client.generate("a bell", str(tmp_path / "y.bin"), 8.0)
        client.close()
        client.close()

    def test_worker_script_ships_with_the_package(self):
        assert _MMAudioClient._WORKER.endswith("mmaudio_worker.py")
        assert os.path.exists(_MMAudioClient._WORKER)


class TestMMAudioAssetTagging:
    def test_shared_path_gets_the_backend_infix(self, tmp_path):
        """The filename infix is how an MMAudio asset stays identifiable."""
        path = shared_sfx_path(str(tmp_path), "SFX: DOOR SLAM", backend="mmaudio")
        assert path.endswith(".mmaudio.mp3")

    def test_licence_is_checked_before_venv_resolution(self):
        """Ordering matters for the error the user actually sees.

        Resolving venv-mmaudio first meant someone without the acknowledgement
        got "cannot find venv-mmaudio" and was sent off to install a multi-GB
        environment they might then be barred from using.
        """
        with unittest.mock.patch(
            "xil_pipeline.sfx_backends._find_mmaudio_python",
            side_effect=AssertionError("venv must not be resolved before the licence check"),
        ), pytest.raises(ValueError, match="CC BY-NC 4.0"):
            make_sfx_backend("mmaudio")
