# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Audition cast voices by generating a short sample MP3 per voice.

Reads a cast configuration file and calls the ElevenLabs TTS API to produce
one sample MP3 per assigned voice. Each sample says:

    "I am <full_name> not yo momma"

Outputs to ``voice_samples/<TAG>/<actor>.mp3``.

Usage::

    python XILU004_sample_voices_T2S.py --episode S02E03 --dry-run
    python XILU004_sample_voices_T2S.py --episode S02E03
    python XILU004_sample_voices_T2S.py --episode S02E03 --force
"""

import argparse
import os
import sys

from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import CastConfiguration, derive_paths, resolve_slug
from xil_pipeline.sfx_common import run_banner, tag_mp3

logger = get_logger(__name__)

# Setup ElevenLabs Client
client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))

VOICE_SAMPLES_DIR = "voice_samples"


def check_elevenlabs_quota() -> int | None:
    """Display current ElevenLabs API character usage and return remaining quota.

    Returns:
        Remaining character count, or ``None`` if the API call fails.
    """
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
    """Check if the ElevenLabs quota can cover the next sample.

    Args:
        text_to_generate: The sample text about to be synthesized.

    Returns:
        ``True`` if remaining characters are sufficient (or if the
        API check fails, as a permissive fallback).
    """
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
    """Select the best ElevenLabs TTS model based on remaining quota.

    Returns:
        Model ID string: ``"eleven_v3"`` for healthy balance,
        ``"eleven_flash_v2_5"`` when low, or ``"eleven_v3"``
        as API-error fallback.
    """
    SAFE_THRESHOLD = 5000

    try:
        user_info = client.user.get()
        remaining = user_info.subscription.character_limit - user_info.subscription.character_count

        if remaining > SAFE_THRESHOLD:
            logger.info(f" [Budget] Healthy Balance: {remaining:,} left. Using 'eleven_v3'.")
            return "eleven_v3"
        else:
            logger.info(f" [Budget] LOW BALANCE: {remaining:,} left. Switching to 'eleven_flash_v2_5' (50% cheaper).")
            return "eleven_flash_v2_5"

    except ApiError:
        logger.info(" [Budget] API Check Failed. Defaulting to 'eleven_v3'.")
        return "eleven_v3"


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-sample",
        description="Generate a voice sample MP3 for each assigned cast member.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--episode",
        metavar="TAG",
        help="Episode tag (e.g. S02E03); derives cast config path",
    )
    group.add_argument(
        "--cast",
        metavar="PATH",
        help="Explicit path to cast JSON file",
    )
    parser.add_argument("--show", default=None, help="Show name override (default: from project.json)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be generated without calling the API",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate samples even if files already exist on disk",
    )
    return parser


def main() -> None:
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        if not args.dry_run and not os.environ.get("ELEVENLABS_API_KEY"):
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

        import json
        with open(cast_path, encoding="utf-8") as f:
            cast_data = json.load(f)
        cast_cfg = CastConfiguration(**cast_data)
        tag = cast_cfg.tag

        out_dir = os.path.join(VOICE_SAMPLES_DIR, tag)

        logger.info(f"Cast config : {cast_path}")
        logger.info(f"Episode tag : {tag}")
        logger.info(f"Output dir  : {out_dir}")
        logger.info(f"Cast members: {len(cast_cfg.cast)}")
        logger.info("")

        if not args.dry_run:
            check_elevenlabs_quota()
            os.makedirs(out_dir, exist_ok=True)

        generated = 0
        skipped_tbd = 0
        skipped_exists = 0

        for key, member in cast_cfg.cast.items():
            if member.voice_id == "TBD":
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
                logger.info(f"  [DRY RUN] {key:12s}  ({member.full_name})  →  {out_path}  ({len(text)} chars)")
                generated += 1
                continue

            if not has_enough_characters(text):
                logger.info(f"  [ STOP] {key:12s}  insufficient quota")
                break

            current_model = get_best_model_for_budget()
            logger.info(f"  [   GEN] {key:12s}  {member.full_name}  …")

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

            tag_mp3(
                out_path,
                title=f"Sample: {member.full_name}",
                artist=member.full_name,
                lyrics=text,
            )
            logger.info(f"  saved → {out_path}")
            generated += 1

        logger.info("")
        if args.dry_run:
            logger.info(f"Dry run: {generated} would be generated, {skipped_tbd} TBD skipped.")
        else:
            logger.info(f"Done: {generated} generated, {skipped_exists} already existed, {skipped_tbd} TBD skipped.")


if __name__ == "__main__":
    main()
