# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Audition cast voices by generating a short sample MP3 per voice.

Reads a cast configuration file and calls the configured TTS backend to produce
one sample MP3 per assigned voice. Each sample says:

    "I am <full_name> not yo momma"

Outputs to ``voice_samples/<TAG>/<backend>/<actor>.mp3`` so samples from
different backends sit side-by-side for direct comparison.

Usage::

    xil-sample --episode S02E03 --dry-run
    xil-sample --episode S02E03
    xil-sample --episode S02E03 --backend gtts
    xil-sample --episode S02E03 --backend chatterbox
    xil-sample --episode S02E03 --force
"""

import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile

from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import CastConfiguration, derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner, tag_mp3

logger = get_logger(__name__)

# Setup ElevenLabs Client
client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))

VOICE_SAMPLES_DIR = "voice_samples"

try:
    from gtts import gTTS as _gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False


# ── ElevenLabs quota helpers ──────────────────────────────────────────────────

def check_elevenlabs_quota() -> int | None:
    try:
        user_info = client.user.get()
        sub = user_info.subscription
        used = sub.character_count
        limit = sub.character_limit
        remaining = limit - used
        logger.info("\n" + "="*40)
        logger.info("ELEVENLABS API STATUS:")
        logger.info(f"  Tier:      {sub.tier.upper()}")
        logger.info(f"  Usage:     {used:,} / {limit:,} characters")
        logger.info(f"  Remaining: {remaining:,}")
        logger.info("="*40 + "\n")
        return remaining
    except ApiError as e:
        logger.warning("API Error: Unable to fetch user subscription data.")
        logger.warning(f"    Details: {e}")
        return None


def has_enough_characters(text_to_generate: str) -> bool:
    try:
        user_info = client.user.get()
        remaining = user_info.subscription.character_limit - user_info.subscription.character_count
        required = len(text_to_generate)
        if remaining >= required:
            logger.info(f" [Guard] Quota OK: {required} required, {remaining:,} left.")
            return True
        else:
            logger.info(f" [Guard] STOP: Line requires {required} chars, but only {remaining:,} remain.")
            return False
    except ApiError:
        logger.info(" [Guard] Warning: Permission 'user_read' missing. Skipping quota check.")
        return True


def get_best_model_for_budget() -> str:
    SAFE_THRESHOLD = 5000
    try:
        user_info = client.user.get()
        remaining = user_info.subscription.character_limit - user_info.subscription.character_count
        if remaining > SAFE_THRESHOLD:
            logger.info(f" [Budget] Healthy Balance: {remaining:,} left. Using 'eleven_v3'.")
            return "eleven_v3"
        else:
            logger.info(f" [Budget] LOW BALANCE: {remaining:,} left. Switching to 'eleven_flash_v2_5'.")
            return "eleven_flash_v2_5"
    except ApiError:
        logger.info(" [Budget] API Check Failed. Defaulting to 'eleven_v3'.")
        return "eleven_v3"


# ── Free TTS backends ─────────────────────────────────────────────────────────

def _gtts_generate(text: str, out_path: str) -> None:
    if not HAS_GTTS:
        raise RuntimeError("gTTS not installed. Run: pip install xil-pipeline[tts-alt]")
    cleaned = re.sub(r'\[[^\]]*\]', '', text).strip()
    if not cleaned:
        return
    tmp_fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(out_path) or ".", suffix=".tmp")
    os.close(tmp_fd)
    try:
        _gTTS(text=cleaned, lang="en").save(tmp_path)
        os.replace(tmp_path, out_path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)


class _ChatterboxClient:
    """Persistent subprocess bridge to the Chatterbox TTS worker."""

    _WORKER = os.path.join(os.path.dirname(__file__), "chatterbox_worker.py")

    def __init__(
        self,
        python_path: str,
        voice_refs_dir: str = "voice_refs",
        device: str = "cuda",
        exaggeration: float = 0.5,
        cfg_weight: float = 0.5,
    ) -> None:
        self._python = python_path
        self._voice_refs_dir = voice_refs_dir
        self._device = device
        self._exaggeration = exaggeration
        self._cfg_weight = cfg_weight
        self._proc: subprocess.Popen | None = None

    def _start(self) -> None:
        logger.info("Starting Chatterbox worker (%s, %s)…", self._python, self._device)
        self._proc = subprocess.Popen(
            [self._python, self._WORKER, self._device],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            bufsize=1,
        )
        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                raise RuntimeError("Chatterbox worker exited before sending ready signal.")
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("Chatterbox worker startup: %s", raw)
                continue
            if msg.get("ready"):
                break
            logger.debug("Chatterbox worker startup: %s", raw)
        logger.info("Chatterbox worker ready (sample_rate=%d)", msg["sr"])

    def _ref_for(self, speaker_key: str) -> str | None:
        for ext in (".wav", ".mp3"):
            p = os.path.join(self._voice_refs_dir, f"{speaker_key}{ext}")
            if os.path.exists(p):
                return p
        return None

    def _cond_for(self, speaker_key: str) -> str:
        return os.path.join(self._voice_refs_dir, f"{speaker_key}.conds.pt")

    def generate(self, text: str, out_path: str, speaker_key: str) -> None:
        if self._proc is None:
            self._start()
        ref = self._ref_for(speaker_key)
        if ref:
            logger.info("   ref: %s", os.path.basename(ref))
        req = {
            "text": text,
            "out_path": out_path,
            "ref_audio": ref,
            "cond_path": self._cond_for(speaker_key),
            "exaggeration": self._exaggeration,
            "cfg_weight": self._cfg_weight,
        }
        assert self._proc is not None
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        raw = self._proc.stdout.readline()
        if not raw:
            raise RuntimeError("Chatterbox worker closed pipe unexpectedly.")
        resp = json.loads(raw)
        if "error" in resp:
            raise RuntimeError(f"Chatterbox: {resp['error']}")

    def close(self) -> None:
        if self._proc is not None:
            with contextlib.suppress(Exception):
                self._proc.stdin.close()
            with contextlib.suppress(Exception):
                self._proc.wait(timeout=15)


# ── CLI ───────────────────────────────────────────────────────────────────────

def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-sample",
        description="Generate a voice sample MP3 for each cast member via the chosen TTS backend.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--episode", metavar="TAG",
        help="Episode tag (e.g. S02E03); derives cast config path",
    )
    group.add_argument(
        "--cast", metavar="PATH",
        help="Explicit path to cast JSON file",
    )
    parser.add_argument("--show", default=None, help="Show name override (default: from project.json)")
    parser.add_argument(
        "--backend", choices=["elevenlabs", "gtts", "chatterbox"], default="elevenlabs",
        help=(
            "TTS backend for sample generation. 'elevenlabs' (default) calls the ElevenLabs API "
            "and uses the voice_id from the cast config. 'gtts' generates a flat-voice draft via "
            "Google Translate TTS at no cost (ignores voice_id). 'chatterbox' uses local GPU TTS "
            "with zero-shot voice cloning from voice_refs/<key>.wav clips. "
            "Output lands in voice_samples/<TAG>/<backend>/ for side-by-side comparison."
        ),
    )
    parser.add_argument(
        "--chatterbox-python", default=None, metavar="PATH",
        help="Path to the chatterbox venv Python (default: auto-detect ./venv-chatterbox/bin/python3). "
             "Used only with --backend chatterbox.",
    )
    parser.add_argument(
        "--voice-refs", default="voice_refs", metavar="DIR",
        help="Directory containing <speaker_key>.wav reference clips for Chatterbox "
             "zero-shot voice cloning (default: voice_refs/). "
             "Used only with --backend chatterbox.",
    )
    parser.add_argument(
        "--exaggeration", type=float, default=0.5, metavar="FLOAT",
        help="Chatterbox emotion exaggeration level: 0.0 = flat, 1.0 = dramatic (default: 0.5). "
             "Used only with --backend chatterbox.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be generated without calling any TTS API",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Regenerate samples even if files already exist on disk",
    )
    return parser


def main() -> None:
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        backend = args.backend

        if not args.dry_run and backend == "elevenlabs" and not os.environ.get("ELEVENLABS_API_KEY"):
            sys.exit("Error: ELEVENLABS_API_KEY environment variable is not set.")

        # Resolve cast config path
        if args.cast:
            cast_path = args.cast
        else:
            slug = resolve_slug(args.show)
            p = derive_paths(slug, args.episode)
            cast_path = p["cast"]

        if not os.path.exists(cast_path):
            logger.warning(f"Cast config not found: {cast_path}")
            raise SystemExit(1)

        with open(cast_path, encoding="utf-8") as f:
            cast_data = json.load(f)
        cast_cfg = CastConfiguration(**cast_data)
        tag = cast_cfg.tag

        out_dir = os.path.join(VOICE_SAMPLES_DIR, tag, backend)

        logger.info(f"Cast config : {cast_path}")
        logger.info(f"Episode tag : {tag}")
        logger.info(f"Backend     : {backend}")
        logger.info(f"Output dir  : {out_dir}")
        logger.info(f"Cast members: {len(cast_cfg.cast)}")
        logger.info("")

        if not args.dry_run:
            if backend == "elevenlabs":
                check_elevenlabs_quota()
            os.makedirs(out_dir, exist_ok=True)

        # Resolve chatterbox python path
        chatterbox_client: _ChatterboxClient | None = None
        if backend == "chatterbox" and not args.dry_run:
            python_path = args.chatterbox_python or os.path.join("venv-chatterbox", "bin", "python3")
            if not os.path.exists(python_path):
                sys.exit(f"Error: Chatterbox Python not found at {python_path}. "
                         "Use --chatterbox-python to specify the path.")
            chatterbox_client = _ChatterboxClient(
                python_path=python_path,
                voice_refs_dir=args.voice_refs,
                exaggeration=args.exaggeration,
            )

        generated = 0
        skipped_tbd = 0
        skipped_exists = 0

        try:
            for key, member in cast_cfg.cast.items():
                # ElevenLabs requires a real voice_id; free backends ignore it
                if backend == "elevenlabs" and member.voice_id == "TBD":
                    logger.info(f"  [ SKIP] {key:12s}  voice_id=TBD")
                    skipped_tbd += 1
                    continue

                out_path = os.path.join(out_dir, f"{key}.mp3")
                text = f"I am {member.full_name} not yo momma"

                if not args.force and os.path.exists(out_path):
                    logger.info(f"  [EXISTS] {key:12s}  {out_path}")
                    skipped_exists += 1
                    continue

                if args.dry_run:
                    ref_note = ""
                    if backend == "chatterbox":
                        ref = os.path.join(args.voice_refs, f"{key}.wav")
                        ref_note = f"  ref={'✓' if os.path.exists(ref) else '✗ (default voice)'}"
                    logger.info(f"  [DRY RUN] {key:12s}  ({member.full_name})  →  {out_path}{ref_note}")
                    generated += 1
                    continue

                if backend == "elevenlabs" and not has_enough_characters(text):
                    logger.info(f"  [ STOP] {key:12s}  insufficient quota")
                    break

                logger.info(f"  [   GEN] {key:12s}  {member.full_name}  …")

                if backend == "gtts":
                    _gtts_generate(text, out_path)
                    tts_comment = "gtts"

                elif backend == "chatterbox":
                    assert chatterbox_client is not None
                    chatterbox_client.generate(text, out_path, speaker_key=key)
                    tts_comment = "chatterbox"

                else:  # elevenlabs
                    current_model = get_best_model_for_budget()
                    audio_stream = client.text_to_speech.convert(
                        text=text,
                        voice_id=member.voice_id,
                        model_id=current_model,
                        output_format="mp3_44100_128",
                    )
                    with open(out_path, "wb") as f:
                        for chunk in audio_stream:
                            if chunk:
                                f.write(chunk)
                    tts_comment = current_model

                tag_mp3(
                    out_path,
                    title=f"Sample: {member.full_name}",
                    artist=member.full_name,
                    lyrics=text,
                    comments=tts_comment,
                )
                logger.info(f"  saved → {out_path}")
                generated += 1

        finally:
            if chatterbox_client is not None:
                chatterbox_client.close()

        logger.info("")
        if args.dry_run:
            logger.info(f"Dry run: {generated} would be generated, {skipped_tbd} TBD skipped.")
        else:
            logger.info(f"Done: {generated} generated, {skipped_exists} already existed, {skipped_tbd} TBD skipped.")


if __name__ == "__main__":
    main()
