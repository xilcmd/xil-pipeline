# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scan a folder of MP3 stem files and produce a JSON report with file attributes and transcribed text.

Reads all *.mp3 files in a stems directory and outputs a structured JSON report containing
file metadata (seq, scene, speaker, size, duration, bitrate, sha256) and optionally a
Faster-Whisper transcription of each file's audio content.

Whisper transcription requires venv-whisper to be set up (see whisper_worker.py).
Use --no-transcribe to produce a metadata-only report without the whisper dependency.

Usage::

    xil-stem-verify --episode S01E01
    xil-stem-verify --show the413 --episode S01E01 --no-transcribe
    xil-stem-verify --show the413 --episode S01E01 --model small --language en
    xil-stem-verify --stems-dir /path/to/stems --output /tmp/report.json --no-transcribe
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from mutagen.mp3 import MP3  # type: ignore[import]

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import get_workspace_root, resolve_slug, resolve_venv_python
from xil_pipeline.sfx_common import run_banner
from xil_pipeline.XILU007_mp3_hash import hash_file

logger = get_logger(__name__)

_WORKER = Path(__file__).parent / "whisper_worker.py"


class _WhisperClient:
    """Subprocess bridge to the Faster-Whisper worker process.

    The worker (``whisper_worker.py``) runs under the whisper venv Python and
    communicates via newline-delimited JSON on stdin/stdout. The model is loaded
    once at startup and reused for all transcription requests.
    """

    def __init__(self, python_path: str, device: str = "cuda", model: str = "large-v3-turbo") -> None:
        self._python = python_path
        self._device = device
        self._model = model
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "_WhisperClient":
        self._proc = subprocess.Popen(
            [self._python, str(_WORKER), self._device, self._model],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        startup = json.loads(self._proc.stdout.readline())
        if not startup.get("ready"):
            raise RuntimeError(f"Whisper worker failed to start: {startup}")
        logger.info("Whisper worker ready — model=%s device=%s", startup.get("model"), startup.get("device"))
        return self

    def transcribe(self, audio_path: str, language: str | None, beam_size: int) -> dict:
        assert self._proc is not None
        req = json.dumps({"audio_path": audio_path, "language": language, "beam_size": beam_size})
        self._proc.stdin.write((req + "\n").encode())
        self._proc.stdin.flush()
        resp = json.loads(self._proc.stdout.readline())
        if "error" in resp:
            raise RuntimeError(f"Whisper error on {os.path.basename(audio_path)}: {resp['error']}")
        return resp

    def __exit__(self, *_) -> None:
        if self._proc:
            self._proc.stdin.close()
            self._proc.wait()
            self._proc = None


def _parse_stem_filename(filename: str) -> tuple[int | None, str | None, str | None]:
    """Extract (seq, scene, speaker) from a stem filename like '003_cold-open_adam.mp3'.

    Falls back to seq-only for ElevenLabs export names like '001_Chapter 1.mp3'.
    """
    name = filename[:-4] if filename.lower().endswith(".mp3") else filename
    parts = name.split("_", 2)
    if len(parts) == 3:
        seq_str, scene, speaker = parts
        try:
            return int(seq_str), scene, speaker
        except ValueError:
            pass
    if parts:
        try:
            return int(parts[0]), None, None
        except ValueError:
            pass
    return None, None, None


def _mp3_metadata(path: str) -> tuple[float | None, int | None]:
    """Return (duration_seconds, bitrate_kbps) via mutagen. Returns (None, None) on failure."""
    try:
        audio = MP3(path)
        duration = round(audio.info.length, 3)
        bitrate = audio.info.bitrate // 1000
        return duration, bitrate
    except Exception:  # noqa: BLE001
        return None, None


def _process_files(
    mp3_files: list[Path],
    whisper: "_WhisperClient | None",
    language_arg: str | None,
    beam_size: int,
) -> list[dict]:
    total = len(mp3_files)
    records = []
    for i, mp3 in enumerate(mp3_files, 1):
        logger.info("[%d/%d] %s", i, total, mp3.name)
        seq, scene, speaker = _parse_stem_filename(mp3.name)
        if seq is None:
            logger.warning("Unexpected filename format (skipping seq/scene/speaker parse): %s", mp3.name)
        duration, bitrate = _mp3_metadata(str(mp3))
        digest = hash_file(str(mp3))
        transcript = None
        if whisper is not None:
            try:
                resp = whisper.transcribe(str(mp3), language=language_arg, beam_size=beam_size)
                transcript = {
                    "text": resp["text"],
                    "language": resp["language"],
                    "language_probability": resp["language_probability"],
                    "segments": resp["segments"],
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning("Transcription failed for %s: %s", mp3.name, exc)
        records.append({
            "filename": mp3.name,
            "path": str(mp3.resolve()),
            "seq": seq,
            "scene": scene,
            "speaker": speaker,
            "size_bytes": mp3.stat().st_size,
            "duration_seconds": duration,
            "bitrate_kbps": bitrate,
            "sha256": digest,
            "transcript": transcript,
        })
    return records


def _run(args: argparse.Namespace) -> None:
    if args.episode is None and args.stems_dir is None:
        logger.error("--episode or --stems-dir is required")
        sys.exit(1)

    workspace = get_workspace_root()
    slug = resolve_slug(args.show)

    stems_dir = Path(args.stems_dir) if args.stems_dir else workspace / "stems" / slug / args.episode
    if not stems_dir.is_dir():
        logger.error("Stems directory not found: %s", stems_dir)
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    elif args.episode:
        output_path = workspace / "parsed" / slug / f"stem_verify_{args.episode}.json"
    else:
        output_path = stems_dir / "stem_verify_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    mp3_files = sorted(p for p in stems_dir.iterdir() if p.suffix.lower() == ".mp3")
    if not mp3_files:
        logger.error("No MP3 files found in %s", stems_dir)
        sys.exit(1)
    logger.info("Found %d MP3 files in %s", len(mp3_files), stems_dir)

    transcribe = not args.no_transcribe
    whisper_python = None
    if transcribe:
        whisper_python = resolve_venv_python("venv-whisper", args.whisper_python)
        if whisper_python is None:
            logger.error(
                "Cannot find venv-whisper Python. Pass --whisper-python PATH, "
                "set XIL_CODEROOT to the directory containing venv-whisper/, "
                "or create venv-whisper/ at the workspace or repo root. "
                "Use --no-transcribe to skip transcription."
            )
            sys.exit(1)

    language_arg = None if args.language == "auto" else args.language

    if transcribe:
        with _WhisperClient(whisper_python, device=args.device, model=args.model) as whisper:
            records = _process_files(mp3_files, whisper, language_arg, args.beam_size)
    else:
        records = _process_files(mp3_files, None, language_arg, args.beam_size)

    total_duration = sum(r["duration_seconds"] or 0.0 for r in records)
    report = {
        "show": slug,
        "episode": args.episode or stems_dir.name,
        "generated": datetime.now().replace(microsecond=0).isoformat(),
        "stems_dir": str(stems_dir.resolve()),
        "whisper_model": args.model if transcribe else None,
        "file_count": len(mp3_files),
        "total_duration_seconds": round(total_duration, 3),
        "files": records,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    logger.info("Written: %s", output_path)
    logger.info("Total stems: %d  Total duration: %.1fs", len(mp3_files), total_duration)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-stem-verify",
        description="Scan a stems folder and produce a JSON report with file attributes and Whisper transcriptions.",
    )
    parser.add_argument("--show", "-s", default=None, metavar="SLUG",
                        help="Show slug (default: resolved from project.json or XIL_PROJECTROOT)")
    parser.add_argument("--episode", "-e", default=None, metavar="TAG",
                        help="Episode tag, e.g. S01E01 (required unless --stems-dir is set)")
    parser.add_argument("--stems-dir", default=None, metavar="DIR",
                        help="Override stems directory (default: <workspace>/stems/<slug>/<episode>/)")
    parser.add_argument("--output", "-o", default=None, metavar="FILE",
                        help="Output JSON path (default: <workspace>/parsed/<slug>/stem_verify_<episode>.json)")
    parser.add_argument("--whisper-python", default=None, metavar="PATH",
                        help="Path to venv-whisper Python executable (auto-detected if omitted)")
    parser.add_argument("--model", default="large-v3-turbo", metavar="SIZE",
                        help="Whisper model size: tiny|base|small|medium|large-v3|large-v3-turbo (default: large-v3-turbo)")
    parser.add_argument("--language", default="en", metavar="LANG",
                        help="Language hint for Whisper. Use 'auto' for automatic detection (default: en)")
    parser.add_argument("--beam-size", type=int, default=5, metavar="N",
                        help="Whisper beam size (default: 5)")
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"],
                        help="Compute device for Whisper (default: cuda)")
    parser.add_argument("--no-transcribe", action="store_true",
                        help="Skip Whisper transcription; output file attributes only")
    return parser


def main() -> None:
    """CLI entry point for MP3 stem attribute extraction and Whisper transcription."""
    configure_logging()
    args = get_parser().parse_args()
    with run_banner():
        _run(args)


if __name__ == "__main__":
    main()
