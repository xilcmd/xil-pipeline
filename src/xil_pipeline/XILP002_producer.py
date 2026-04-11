# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Generate individual voice stems via the ElevenLabs TTS API.

Reads parsed script JSON and cast configuration to produce one MP3 stem
per dialogue line. Audio assembly is handled separately by XILP003.

Module Attributes:
    STEMS_DIR: Directory for generated voice stem MP3 files.
"""

import argparse
import contextlib
import json
import os
import re
import sys
import tempfile

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError
from pydub import AudioSegment

try:
    from gtts import gTTS as _gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False

import subprocess
import threading

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import (
    CastConfiguration,
    DialogueEntry,
    SfxConfiguration,
    VoiceConfig,
    derive_paths,
    resolve_slug,
)
from xil_pipeline.sfx_common import (
    dry_run_sfx,
    file_nonempty,
    load_sfx_entries,
    run_banner,
    tag_mp3,
)
from xil_pipeline.sfx_common import (
    generate_sfx as generate_sfx_stems,
)
from xil_pipeline.XILU007_mp3_hash import hash_file as _hash_file

logger = get_logger(__name__)


def _log_stem_hash(path: str) -> None:
    """Log the SHA-256 hash of a freshly written stem file."""
    try:
        logger.info("   SHA256: %s", _hash_file(path))
    except Exception as exc:
        logger.debug("Could not hash %s: %s", path, exc)

# Setup ElevenLabs Client
client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))

STEMS_DIR = "stems"


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
        logger.info("  Tier:      %s", sub.tier.upper())
        logger.info("  Usage:     %s / %s characters", f"{used:,}", f"{limit:,}")
        logger.info("  Remaining: %s", f"{remaining:,}")
        logger.info("="*40 + "\n")

        return remaining
    except ApiError as e:
        logger.warning("API Error: Unable to fetch user subscription data.")
        logger.warning("    Details: %s", e)
        return None


def has_enough_characters(text_to_generate: str) -> bool:
    """Check if the ElevenLabs quota can cover the next line of text.

    Args:
        text_to_generate: The dialogue text about to be synthesized.

    Returns:
        ``True`` if remaining characters are sufficient (or if the
        API check fails, as a permissive fallback).
    """
    try:
        user_info = client.user.get()
        remaining = user_info.subscription.character_limit - user_info.subscription.character_count

        required = len(text_to_generate)
        if remaining >= required:
            logger.info(" [Guard] Quota OK: %d required, %s left.", required, f"{remaining:,}")
            return True
        else:
            logger.info(" [Guard] STOP: Line requires %d chars, but only %s remain.", required, f"{remaining:,}")
            return False
    except ApiError:
        logger.warning(" [Guard] Permission 'user_read' missing. Skipping quota check.")
        return True


def get_best_model_for_budget() -> str:
    """Return the TTS model to use, always ``eleven_v3``.

    Previously this function fell back to ``eleven_flash_v2_5`` when the
    account balance was low.  That fallback is removed because flash-v2.5 does
    not honour native audio tags like ``[pause]``, causing them to be spoken
    aloud as text.  A low-balance warning is logged instead so the operator
    can top up before continuing.

    Returns:
        ``"eleven_v3"`` unconditionally (API-error fallback also returns v3).
    """
    SAFE_THRESHOLD = 5000

    try:
        user_info = client.user.get()
        remaining = user_info.subscription.character_limit - user_info.subscription.character_count

        if remaining > SAFE_THRESHOLD:
            logger.info(" [Budget] Healthy Balance: %s left. Using 'eleven_v3'.", f"{remaining:,}")
        else:
            logger.warning(
                " [Budget] LOW BALANCE: %s left. Continuing with 'eleven_v3' — "
                "audio tags like [pause] require v3 and cannot fall back to flash.",
                f"{remaining:,}",
            )
        return "eleven_v3"

    except ApiError:
        logger.info(" [Budget] API Check Failed. Defaulting to 'eleven_v3'.")
        return "eleven_v3"


_SSML_TAG_RE = re.compile(r"<(?:break|emphasis|prosody|say-as|phoneme|sub|speak|p|s)\b", re.IGNORECASE)


def _select_model(text: str) -> str:
    """Return the TTS model for this text segment, always ``eleven_v3``.

    Previously this function fell back to ``eleven_multilingual_v2`` when SSML
    tags (``<break>``, ``<emphasis>``, etc.) were detected, because ``eleven_v3``
    does not honour SSML.  That fallback is removed: ``eleven_multilingual_v2``
    does not honour native audio tags like ``[pause]``, causing them to be
    spoken aloud as text.

    If SSML tags are found a warning is logged prompting the operator to replace
    them with native v3 audio tags (e.g. replace ``<break time="1s"/>`` with
    ``[pause]``).

    Args:
        text: The TTS input text, possibly containing SSML markup.

    Returns:
        A model ID string safe to pass to ``client.text_to_speech.convert()``.
    """
    if _SSML_TAG_RE.search(text):
        logger.warning(
            "   [!] SSML tag detected in TTS text — eleven_v3 does not honour SSML. "
            "Replace <break> / <emphasis> etc. with native audio tags like [pause]. "
            "Text: %.60s", text,
        )
    return get_best_model_for_budget()


def truncate_to_words(text: str, n: int = 3) -> str:
    """Return the first n words of text.

    Used by ``--terse`` mode to reduce TTS character cost during
    test runs. Punctuation attached to words is preserved.

    Args:
        text: Input dialogue text.
        n: Maximum number of words to keep (default: 3).

    Returns:
        The first ``n`` whitespace-delimited words joined by spaces,
        or the full text if it contains fewer than ``n`` words.
    """
    words = text.split()
    return " ".join(words[:n])


def load_production(
    script_json_path: str, cast_json_path: str
) -> tuple[dict[str, dict], list[dict], str]:
    """Load parsed script JSON and cast config for production.

    Reads the cast configuration and parsed script, then builds a
    simplified voice config per speaker and a list of dialogue entries
    enriched with stem filenames.

    Args:
        script_json_path: Path to the parsed script JSON (XILP001 output).
        cast_json_path: Path to the cast configuration JSON.

    Returns:
        A tuple of ``(config, dialogue_entries, tag)`` where ``config`` maps
        speaker keys to ``VoiceConfig`` dicts, ``dialogue_entries``
        is a list of ``DialogueEntry`` dicts, and ``tag`` is the
        episode tag (e.g. ``"S01E01"``).

    Raises:
        FileNotFoundError: If either JSON file does not exist.
    """
    if not os.path.exists(cast_json_path):
        raise FileNotFoundError(
            f"Cast config not found: {cast_json_path}\n"
            "Run XILP001 first or check your --episode flag."
        )
    with open(cast_json_path, encoding="utf-8") as f:
        cast_data = json.load(f)

    if not os.path.exists(script_json_path):
        raise FileNotFoundError(
            f"Parsed script not found: {script_json_path}\n"
            "Run XILP001 first or check your --script flag."
        )
    with open(script_json_path, encoding="utf-8") as f:
        script_data = json.load(f)

    # Build config: speaker_key -> {id, pan, filter}
    cast_cfg = CastConfiguration(**cast_data)
    tag = cast_cfg.tag
    config = {}
    for key, member in cast_cfg.cast.items():
        vc = VoiceConfig(id=member.voice_id, pan=member.pan, filter=member.filter)
        config[key] = {
            **vc.model_dump(),
            "full_name": member.full_name,
            "stability": member.stability,
            "similarity_boost": member.similarity_boost,
            "style": member.style,
            "use_speaker_boost": member.use_speaker_boost,
            "language_code": member.language_code,
        }

    # Extract dialogue entries with stem naming info
    dialogue_entries = []
    for entry in script_data["entries"]:
        if entry["type"] != "dialogue":
            continue
        # Build stem name: {seq:03d}_{section}[-{scene}]_{speaker}
        stem_name = f"{entry['seq']:03d}_{entry['section']}"
        if entry.get("scene"):
            stem_name += f"-{entry['scene']}"
        stem_name += f"_{entry['speaker']}"

        de = DialogueEntry(
            speaker=entry["speaker"],
            text=entry["text"],
            stem_name=stem_name,
            seq=entry["seq"],
            direction=entry.get("direction"),
        )
        dialogue_entries.append(de.model_dump())

    return config, dialogue_entries, tag


def dry_run(
    config: dict[str, dict], dialogue_entries: list[dict], start_from: int = 1,
    stop_at: int | None = None,
    sfx_entries: list[dict] | None = None, sfx_config: dict | None = None,
    stems_dir: str = "",
) -> None:
    """Preview all dialogue lines and TTS cost without making API calls.

    Args:
        config: Speaker-to-voice mapping from ``load_production()``.
        dialogue_entries: Dialogue entry dicts from ``load_production()``.
        start_from: Sequence number to start from (lines before this
            are shown but marked as skipped).
        stop_at: Sequence number to stop at, inclusive (lines after this
            are shown but marked as skipped). ``None`` means no upper limit.
        sfx_entries: Optional SFX entry dicts from ``load_sfx_entries()``.
        sfx_config: Optional raw SFX config dict.
        stems_dir: Episode stems directory (for SFX shared-library status).
    """
    logger.info("\n%s", "="*70)
    logger.info("DRY RUN — %d dialogue lines", len(dialogue_entries))
    logger.info("%s", "="*70)
    logger.info(" [.] %-3s | %-14s | %10s | voice check [lang]", "seq", "speaker", "chars")
    logger.info(" %s", "-"*67)

    total_chars = 0
    lines_to_generate = 0

    # Per-speaker accumulators for the cost breakdown table
    per_speaker_generate: dict[str, dict] = {}   # in-range, stem absent
    per_speaker_skip: dict[str, dict] = {}        # stem already on disk

    for entry in dialogue_entries:
        char_count = len(entry["text"])
        total_chars += char_count
        speaker = entry["speaker"]
        in_range = entry["seq"] >= start_from and (stop_at is None or entry["seq"] <= stop_at)

        stem_exists = bool(
            stems_dir and os.path.exists(os.path.join(stems_dir, entry["stem_name"] + ".mp3"))
        )

        if stem_exists:
            bucket = per_speaker_skip.setdefault(speaker, {"lines": 0, "chars": 0})
            bucket["lines"] += 1
            bucket["chars"] += char_count
            marker = "="
        elif in_range:
            bucket = per_speaker_generate.setdefault(speaker, {"lines": 0, "chars": 0})
            bucket["lines"] += 1
            bucket["chars"] += char_count
            lines_to_generate += 1
            marker = " "
        else:
            marker = "x"

        direction_label = f" ({entry['direction']})" if entry["direction"] else ""
        text_preview = entry["text"][:75] + "..." if len(entry["text"]) > 75 else entry["text"]

        cfg = config.get(speaker, {})
        voice_id = cfg.get("id", "???")
        voice_status = "TBD" if voice_id == "TBD" else "OK"

        # Summarise any non-default voice settings
        vs_parts = []
        for k in ("stability", "similarity_boost", "style"):
            if cfg.get(k) is not None:
                vs_parts.append(f"{k}={cfg[k]}")
        if cfg.get("use_speaker_boost"):
            vs_parts.append("speaker_boost")
        if cfg.get("language_code"):
            vs_parts.append(f"lang={cfg['language_code']}")
        vs_note = f" [{', '.join(vs_parts)}]" if vs_parts else ""

        logger.info(" [%s] %03d | %-14s | %4d chars | voice: %s%s%s", marker, entry['seq'], speaker, char_count, voice_status, vs_note, direction_label)
        logger.info("          %s", text_preview)
        logger.info("          stem: %s.mp3", entry['stem_name'])
        logger.info("")

    # SFX entries — delegate to sfx_common.dry_run_sfx
    if sfx_entries and sfx_config:
        dry_run_sfx(sfx_entries, sfx_config, stems_dir)

    # Summary
    chars_in_range = sum(
        len(e["text"]) for e in dialogue_entries
        if e["seq"] >= start_from and (stop_at is None or e["seq"] <= stop_at)
    )
    tbd_voices = [sp for sp, cfg in config.items() if cfg["id"] == "TBD"]

    logger.info("%s", "="*70)
    logger.info("TOTAL:  %d lines, %s TTS characters", len(dialogue_entries), f"{total_chars:,}")
    if start_from > 1 or stop_at is not None:
        if stop_at is not None and start_from > 1:
            range_label = f"FROM {start_from}–{stop_at}"
        elif stop_at is not None:
            range_label = f"THRU {stop_at}"
        else:
            range_label = f"FROM {start_from}"
        logger.info("%s: %d lines, %s TTS characters", range_label, lines_to_generate, f"{chars_in_range:,}")
    if tbd_voices:
        logger.warning("\n  %d voices still need voice_id assignment: %s", len(tbd_voices), ', '.join(tbd_voices))
        logger.info("  Use XILU001_discover_voices_T2S.py to browse voices, then update the cast config")
    logger.info("%s\n", "="*70)

    # Per-speaker cost breakdown table (only when there are stems to generate)
    if per_speaker_generate:
        all_speakers = sorted(
            per_speaker_generate.keys(),
            key=lambda s: per_speaker_generate[s]["chars"],
            reverse=True,
        )
        # Include skip-only speakers in the table too (at the bottom, sorted by chars)
        skip_only = sorted(
            [s for s in per_speaker_skip if s not in per_speaker_generate],
            key=lambda s: per_speaker_skip[s]["chars"],
            reverse=True,
        )
        all_rows = all_speakers + skip_only

        sep = "-" * 16 + "  " + "-" * 5 + "  " + "-" * 8 + "      " + "-" * 5 + "  " + "-" * 8
        logger.info("SPEAKER COST BREAKDOWN  ([ ]=generate  [=]=skip  [x]=out of range)")
        logger.info("%-16s  %5s  %8s      %5s  %8s", "Speaker", "Lines", "Chars", "Lines", "Chars")
        logger.info("%-16s  %5s  %8s      %5s  %8s", "", "gen", "gen", "skip", "skip")
        logger.info("%s", sep)
        for spk in all_rows:
            g = per_speaker_generate.get(spk, {"lines": 0, "chars": 0})
            sk = per_speaker_skip.get(spk, {"lines": 0, "chars": 0})
            logger.info("%-16s  %5d  %8s      %5d  %8s",
                        spk, g["lines"], f"{g['chars']:,}", sk["lines"], f"{sk['chars']:,}")
        logger.info("%s", sep)
        total_gen_lines = sum(v["lines"] for v in per_speaker_generate.values())
        total_gen_chars = sum(v["chars"] for v in per_speaker_generate.values())
        total_skip_lines = sum(v["lines"] for v in per_speaker_skip.values())
        total_skip_chars = sum(v["chars"] for v in per_speaker_skip.values())
        logger.info("%-16s  %5d  %8s      %5d  %8s",
                    "TOTAL", total_gen_lines, f"{total_gen_chars:,}",
                    total_skip_lines, f"{total_skip_chars:,}")
        logger.info("")


def generate_voices(
    config: dict[str, dict], dialogue_entries: list[dict],
    stems_dir: str, start_from: int = 1, stop_at: int | None = None,
    show: str = "Sample Show", backend: str = "elevenlabs",
    chatterbox_client: "_ChatterboxClient | None" = None,
) -> None:
    """Generate individual voice stem MP3s via the configured TTS backend.

    Iterates through dialogue entries, skipping stems that already exist
    on disk or have unassigned voice IDs. Halts if the character quota
    is exhausted (ElevenLabs backend only).

    Args:
        config: Speaker-to-voice mapping from ``load_production()``.
        dialogue_entries: Dialogue entry dicts from ``load_production()``.
        stems_dir: Directory to write stem MP3 files into.
        start_from: Sequence number to resume generation from.
        stop_at: Sequence number to stop at, inclusive. ``None`` means
            process all entries from ``start_from`` onward.
        backend: TTS backend — ``"elevenlabs"`` (default) or ``"gtts"`` for
            a free flat-voice draft pass.
    """
    os.makedirs(stems_dir, exist_ok=True)

    # Block if any cast member in the range has an unassigned voice_id (ElevenLabs only)
    if backend == "elevenlabs":
        speakers_needed = {
            e["speaker"] for e in dialogue_entries
            if e["seq"] >= start_from and (stop_at is None or e["seq"] <= stop_at)
        }
        tbd_needed = [sp for sp in speakers_needed if config.get(sp, {}).get("id") == "TBD"]
        if tbd_needed:
            logger.error(
                "Cannot generate: %d speaker(s) in range have no voice_id: %s\n"
                "  Assign voice IDs in the cast config, then re-run.",
                len(tbd_needed), ", ".join(sorted(tbd_needed)),
            )
            return

    # Filter to entries in the requested range
    entries_to_process = [
        e for e in dialogue_entries
        if e["seq"] >= start_from and (stop_at is None or e["seq"] <= stop_at)
    ]

    # Build seq-ordered index over the full dialogue list for prev/next continuity
    all_seqs = sorted(e["seq"] for e in dialogue_entries)
    seq_position = {seq: i for i, seq in enumerate(all_seqs)}
    entries_by_seq = {e["seq"]: e for e in dialogue_entries}

    range_note = ""
    if stop_at is not None:
        range_note = f" (seq {start_from}–{stop_at})"
    elif start_from > 1:
        range_note = f" (from seq {start_from})"
    logger.info("--- Phase 1: Generating %d voice stems%s ---", len(entries_to_process), range_note)
    current_model = get_best_model_for_budget()
    generated_count = 0

    for entry in entries_to_process:
        speaker = entry["speaker"]
        text = entry["text"]
        stem_name = entry["stem_name"]

        # Skip if stem already exists
        stem_file = os.path.join(stems_dir, f"{stem_name}.mp3")
        if os.path.exists(stem_file):
            logger.info("   Exists: %s — skipping", stem_file)
            continue

        # Check voice_id is assigned (ElevenLabs only — gTTS uses a single flat voice)
        if backend == "elevenlabs" and config.get(speaker, {}).get("id") == "TBD":
            logger.warning("No voice_id for %s — skipping %s", speaker, stem_name)
            continue

        # Check quota (ElevenLabs only)
        if backend == "elevenlabs" and not has_enough_characters(text):
            logger.info(" !!! Production halted at seq %d to save credits.", entry['seq'])
            break

        # Guard: skip entries whose text would be empty after ElevenLabs strips speaker
        # tags (e.g. [sighs]) and emojis — the API returns 400 "input_text_empty".
        stripped = re.sub(r'\[[^\]]*\]', '', text)   # remove [tag] patterns
        stripped = re.sub(r'[^\w\s]', '', stripped)   # remove punctuation / emojis
        if not stripped.strip():
            logger.warning(
                "   SKIP seq %d (%s): text %r is empty after stripping speaker tags/emojis — "
                "convert this entry to a direction or replace the text in the script.",
                entry['seq'], speaker, text,
            )
            continue

        # Build VoiceSettings from per-speaker cast config (None fields are omitted)
        cfg = config.get(speaker, {})
        vs_fields = {
            k: cfg[k] for k in ("stability", "similarity_boost", "style", "use_speaker_boost")
            if cfg.get(k) is not None
        }
        voice_settings = VoiceSettings(**vs_fields) if vs_fields else None

        # Resolve prev/next text for prosody continuity
        pos = seq_position.get(entry["seq"])
        prev_text = entries_by_seq[all_seqs[pos - 1]]["text"] if pos and pos > 0 else None
        next_text = entries_by_seq[all_seqs[pos + 1]]["text"] if pos is not None and pos < len(all_seqs) - 1 else None

        # Collect optional top-level kwargs
        extra_kwargs = {}
        if cfg.get("language_code") and current_model != "eleven_v3":
            extra_kwargs["language_code"] = cfg["language_code"]
        if prev_text and current_model != "eleven_v3":
            extra_kwargs["previous_text"] = prev_text
        if next_text and current_model != "eleven_v3":
            extra_kwargs["next_text"] = next_text

        if backend == "gtts":
            logger.info(" > [%03d] %s via gTTS (%d chars)...", entry['seq'], speaker, len(text))
            _gtts_generate(text, stem_file)
        elif backend == "chatterbox":
            logger.info(" > [%03d] %s via Chatterbox (%d chars)...", entry['seq'], speaker, len(text))
            assert chatterbox_client is not None
            chatterbox_client.generate(text, stem_file, speaker)
        else:
            logger.info(" > [%03d] %s with %s (%d chars)...", entry['seq'], speaker, current_model, len(text))
            audio_stream = client.text_to_speech.convert(
                text=text,
                voice_id=config[speaker]["id"],
                model_id=current_model,
                output_format="mp3_44100_128",
                voice_settings=voice_settings,
                **extra_kwargs,
            )
            with open(stem_file, "wb") as f:
                for chunk in audio_stream:
                    if chunk:
                        f.write(chunk)

        full_name = config.get(speaker, {}).get("full_name", speaker.title())
        first_five = " ".join(text.split()[:5])
        tag_mp3(
            stem_file,
            show=show,
            title=f"{full_name}: {first_five}",
            artist=full_name,
            lyrics=text,
        )
        logger.info("   Saved: %s", stem_file)
        _log_stem_hash(stem_file)
        generated_count += 1

    stem_count = len([f for f in os.listdir(stems_dir) if f.endswith(".mp3")])
    logger.info("--- Phase 1 Complete: %d new, %d total stems in %s/ ---", generated_count, stem_count, stems_dir)



def inject_preamble_entries(parsed_path: str, preamble_text: str, speaker: str) -> None:
    """Prepend seq -2 (voice) and -1 (INTRO MUSIC) entries into the parsed JSON.

    Idempotent: strips any existing ``section="preamble"`` entries before
    prepending so re-running XILP002 replaces rather than duplicates preamble
    entries.

    Args:
        parsed_path: Path to the parsed script JSON file (modified in place).
        preamble_text: Resolved preamble text (placeholders already substituted).
        speaker: Cast key for the TTS speaker (e.g. "tina").
    """
    with open(parsed_path, encoding="utf-8") as f:
        data = json.load(f)
    # Strip any existing preamble entries
    data["entries"] = [e for e in data["entries"] if e.get("section") != "preamble"]
    # Prepend seq -2 (voice) and seq -1 (INTRO MUSIC direction)
    preamble_entries = [
        {
            "seq": -2,
            "type": "dialogue",
            "section": "preamble",
            "scene": None,
            "speaker": speaker,
            "direction": None,
            "text": preamble_text,
            "direction_type": None,
        },
        {
            "seq": -1,
            "type": "direction",
            "section": "preamble",
            "scene": None,
            "speaker": None,
            "direction": None,
            "text": "INTRO MUSIC",
            "direction_type": "MUSIC",
        },
    ]
    data["entries"] = preamble_entries + data["entries"]
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("   Injected preamble entries (seq -2, -1) into %s", parsed_path)


def inject_postamble_entries(parsed_path: str, postamble_text: str, speaker: str) -> tuple[int, int]:
    """Append postamble OUTRO MUSIC + voice entries at the end of the parsed JSON.

    Order: music (max+1) precedes voice (max+2) so the outro sting plays
    before Tina's sign-off.  Idempotent: removes any existing
    ``section="postamble"`` entries before appending.

    Args:
        parsed_path: Path to the parsed script JSON file (modified in place).
        postamble_text: Resolved postamble text (placeholders already substituted).
        speaker: Cast key for the TTS speaker (e.g. "tina").

    Returns:
        Tuple of (music_seq, voice_seq) — the assigned sequence numbers.
    """
    with open(parsed_path, encoding="utf-8") as f:
        data = json.load(f)
    # Strip any existing postamble entries
    data["entries"] = [e for e in data["entries"] if e.get("section") != "postamble"]
    # Determine max episode seq (positive, non-postamble)
    episode_seqs = [e["seq"] for e in data["entries"] if e["seq"] > 0]
    max_seq = max(episode_seqs) if episode_seqs else 0
    music_seq = max_seq + 1
    voice_seq = max_seq + 2
    data["entries"] += [
        {
            "seq": music_seq,
            "type": "direction",
            "section": "postamble",
            "scene": None,
            "speaker": None,
            "direction": None,
            "text": "OUTRO MUSIC",
            "direction_type": "MUSIC",
        },
        {
            "seq": voice_seq,
            "type": "dialogue",
            "section": "postamble",
            "scene": None,
            "speaker": speaker,
            "direction": None,
            "text": postamble_text,
            "direction_type": None,
        },
    ]
    with open(parsed_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("   Injected postamble entries (seq %d OUTRO MUSIC, %d voice) into %s", music_seq, voice_seq, parsed_path)
    return music_seq, voice_seq


# ---------------------------------------------------------------------------
# Preamble / postamble helpers
# ---------------------------------------------------------------------------

def _episode_kwargs(cast_cfg) -> dict:
    return dict(
        show=cast_cfg.show or "",
        season=cast_cfg.season or "",
        season_title=cast_cfg.season_title or "",
        episode=cast_cfg.episode,
        title=cast_cfg.title or "",
    )


def _format_block_text(template: str, kwargs: dict, context: str) -> str:
    """Format *template* with *kwargs*, raising a clear error on unknown placeholders."""
    try:
        return template.format(**kwargs)
    except KeyError as e:
        raise ValueError(
            f"Unknown placeholder {e} in {context}: {template!r}. "
            f"Valid keys: {sorted(kwargs)}"
        ) from e


def _resolve_voice_block_text(block, cast_cfg) -> str:
    """Resolve a Preamble/postamble block to its full spoken text string.

    Joins segments (no separator) or formats the legacy single-string form.
    """
    kwargs = _episode_kwargs(cast_cfg)
    if block.segments:
        return "".join(
            _format_block_text(seg.text, kwargs, "preamble/postamble segment")
            for seg in block.segments
        )
    return _format_block_text(block.text, kwargs, "preamble/postamble text")


def _resolve_preamble_text(cast_cfg) -> str:
    return _resolve_voice_block_text(cast_cfg.preamble, cast_cfg)


def _resolve_postamble_text(cast_cfg) -> str:
    return _resolve_voice_block_text(cast_cfg.postamble, cast_cfg)


def _gtts_generate(text: str, out_path: str) -> None:
    """Draft TTS via gTTS (Google Translate TTS). Strips eleven_v3 tags; flat single voice.

    Useful for free timing/pacing passes before committing ElevenLabs credits.
    Requires the ``tts-alt`` optional dependency: ``pip install xil-pipeline[tts-alt]``.
    """
    if not HAS_GTTS:
        raise RuntimeError(
            "gTTS not installed. Run: pip install xil-pipeline[tts-alt]"
        )
    cleaned = re.sub(r'\[[^\]]*\]', '', text).strip()
    if not cleaned:
        return
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(out_path) or ".", suffix=".tmp"
    )
    os.close(tmp_fd)
    try:
        logger.info("   > gTTS (%d chars) → %s", len(cleaned), os.path.basename(out_path))
        _gTTS(text=cleaned, lang="en").save(tmp_path)
        os.replace(tmp_path, out_path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)


class _ChatterboxClient:
    """Persistent subprocess bridge to the Chatterbox TTS worker.

    The worker script (``chatterbox_worker.py``) runs under the chatterbox
    venv Python and keeps the model loaded across all generation requests.
    Communication uses newline-delimited JSON on stdin/stdout.

    Args:
        python_path: Path to the chatterbox venv Python executable.
        voice_refs_dir: Directory containing ``<speaker_key>.wav`` reference
            clips for zero-shot voice cloning.  Missing refs fall back to
            Chatterbox's default voice.
        device: ``"cuda"`` (default) or ``"cpu"``.
        exaggeration: Emotion exaggeration level (0.0 = flat, 1.0 = dramatic).
        cfg_weight: CFG weight controlling pacing/delivery (~0.3–0.5 typical).
    """

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
        # Drain startup noise on stderr in background thread
        raw = self._proc.stdout.readline()
        if not raw:
            raise RuntimeError("Chatterbox worker exited before sending ready signal.")
        msg = json.loads(raw)
        if not msg.get("ready"):
            raise RuntimeError(f"Chatterbox worker start failed: {msg}")
        logger.info("Chatterbox worker ready (sample_rate=%d)", msg["sr"])

    def _ref_for(self, speaker_key: str) -> str | None:
        for ext in (".wav", ".mp3"):
            p = os.path.join(self._voice_refs_dir, f"{speaker_key}{ext}")
            if os.path.exists(p):
                return p
        return None

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
            self._proc = None


def _tts_segment(text: str, out_path: str, voice_id: str, speed: float | None,
                 backend: str = "elevenlabs",
                 chatterbox_client: "_ChatterboxClient | None" = None,
                 speaker_key: str | None = None) -> None:
    """Call TTS backend and write the result to *out_path*.

    Uses a unique ``.tmp`` staging file so a partial write is never mistaken
    for a complete asset and concurrent runs cannot collide on the same path.
    """
    if backend == "gtts":
        _gtts_generate(text, out_path)
        return
    if backend == "chatterbox":
        assert chatterbox_client is not None, "chatterbox_client required for backend='chatterbox'"
        chatterbox_client.generate(text, out_path, speaker_key or "")
        return
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=os.path.dirname(out_path) or ".", suffix=".tmp"
    )
    os.close(tmp_fd)
    try:
        current_model = _select_model(text)
        logger.info("   > TTS (%d chars) → %s [%s]", len(text), os.path.basename(out_path), current_model)
        voice_settings = VoiceSettings(speed=speed) if speed is not None else None
        audio_stream = client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id=current_model,
            output_format="mp3_44100_128",
            voice_settings=voice_settings,
        )
        with open(tmp_path, "wb") as f:
            for chunk in audio_stream:
                if chunk:
                    f.write(chunk)
        os.replace(tmp_path, out_path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)


def _dry_run_voice_block(block, cast_cfg, stem_path: str, label: str) -> None:
    """Print dry-run summary for a preamble or postamble voice stem."""
    spk = block.speaker
    stem_exists = file_nonempty(stem_path)
    if stem_exists:
        logger.info(" [%s] %s — stem exists, will skip\n", label, spk)
        return

    if block.segments:
        resolved = _resolve_voice_block_text(block, cast_cfg)
        logger.info(" [%s] %s | %d chars (single call)", label, spk, len(resolved))
        logger.info("   stem: %s\n", os.path.basename(stem_path))
    else:
        kwargs = _episode_kwargs(cast_cfg)
        resolved = _format_block_text(block.text, kwargs, "preamble/postamble text")
        logger.info(" [%s] %s | %d chars", label, spk, len(resolved))
        logger.info("   stem: %s\n", os.path.basename(stem_path))


def _dry_run_preamble(cast_cfg, preamble_voice_stem: str) -> None:
    _dry_run_voice_block(cast_cfg.preamble, cast_cfg, preamble_voice_stem, "PREAMBLE")


def _dry_run_postamble(cast_cfg, postamble_voice_stem: str) -> None:
    _dry_run_voice_block(cast_cfg.postamble, cast_cfg, postamble_voice_stem, "POSTAMBLE")


def _generate_voice_block(block, cast_cfg, config: dict, voice_stem: str,
                           label: str, sfx_dir: str = "SFX",
                           backend: str = "elevenlabs",
                           chatterbox_client: "_ChatterboxClient | None" = None) -> None:
    """Generate a voice stem from a preamble or postamble block.

    All segment texts (when ``block.segments`` is present) are resolved and
    joined into a single string, then sent to the TTS backend as one call.
    This produces natural prosody across the whole block without audible seams.
    The legacy single ``text`` field is also supported.
    """
    if file_nonempty(voice_stem):
        logger.info("   Exists: %s — skipping", voice_stem)
        return

    spk = block.speaker
    voice_id = config.get(spk, {}).get("id", "TBD")
    if backend == "elevenlabs" and voice_id == "TBD":
        logger.warning("No voice_id for %s — skipping %s", spk, os.path.basename(voice_stem))
        return

    if block.segments:
        resolved = _resolve_voice_block_text(block, cast_cfg)
    else:
        kwargs = _episode_kwargs(cast_cfg)
        resolved = _format_block_text(block.text, kwargs, "preamble/postamble text")

    if backend == "elevenlabs" and not has_enough_characters(resolved):
        logger.warning("Insufficient quota for %s — skipping", label.lower())
        return
    logger.info(" > [%s] %s (%d chars)...", label, spk, len(resolved))
    _tts_segment(resolved, voice_stem, voice_id, block.speed, backend=backend,
                 chatterbox_client=chatterbox_client, speaker_key=spk)
    logger.info("   Saved: %s", voice_stem)
    _log_stem_hash(voice_stem)


def _generate_preamble_voice(cast_cfg, config: dict, preamble_voice_stem: str,
                              sfx_dir: str = "SFX", backend: str = "elevenlabs",
                              chatterbox_client: "_ChatterboxClient | None" = None) -> None:
    _generate_voice_block(cast_cfg.preamble, cast_cfg, config,
                          preamble_voice_stem, "PREAMBLE", sfx_dir, backend=backend,
                          chatterbox_client=chatterbox_client)


def _generate_postamble_voice(cast_cfg, config: dict, postamble_voice_stem: str,
                               sfx_dir: str = "SFX", backend: str = "elevenlabs",
                               chatterbox_client: "_ChatterboxClient | None" = None) -> None:
    _generate_voice_block(cast_cfg.postamble, cast_cfg, config,
                          postamble_voice_stem, "POSTAMBLE", sfx_dir, backend=backend,
                          chatterbox_client=chatterbox_client)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-produce",
        description="Voice Generation — generate voice stems via ElevenLabs",
    )
    tag_group = parser.add_mutually_exclusive_group(required=True)
    tag_group.add_argument("--episode",
                           help="Episode tag (e.g. S01E01) — derives cast and SFX config paths")
    tag_group.add_argument("--tag",
                           help="Raw tag for non-episodic content (e.g. V01C03, D01)")
    parser.add_argument("--show", default=None,
                        help="Show name override (default: from project.json)")
    parser.add_argument("--script", default=None,
                        help="Path to parsed script JSON (default: derived from cast config)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview all lines and TTS cost without API calls")
    parser.add_argument("--start-from", type=int, default=1,
                        help="Start generation from sequence number N (for resuming)")
    parser.add_argument("--stop-at", type=int, default=None,
                        help="Stop generation at sequence number N, inclusive (for previewing a section)")
    parser.add_argument("--terse", action="store_true",
                        help="Truncate each line to 3 words to minimize TTS character cost")
    parser.add_argument("--gen-sfx", action="store_true",
                        help="Generate SFX and BEAT stems")
    parser.add_argument("--gen-music", action="store_true",
                        help="Generate music stems")
    parser.add_argument("--gen-ambience", action="store_true",
                        help="Generate ambience stems")
    parser.add_argument("--sfx-music", action="store_true",
                        help="(deprecated) shorthand for --gen-sfx --gen-music --gen-ambience")
    parser.add_argument("--local-only", action="store_true",
                        help="Only place stems for effects already present in SFX/; skip API generation")
    parser.add_argument("--backend", choices=["elevenlabs", "gtts", "chatterbox"],
                        default="elevenlabs", metavar="BACKEND",
                        help=(
                            "TTS backend for dialogue voice stems. 'elevenlabs' (default) calls "
                            "the ElevenLabs API. 'gtts' generates a flat-voice draft via Google "
                            "Translate TTS at no cost — all characters sound the same, useful for "
                            "checking episode duration before spending API credits. "
                            "'chatterbox' uses local Chatterbox TTS with per-character voice "
                            "cloning from voice_refs/<key>.wav reference clips — near-production "
                            "quality, free, GPU-accelerated. "
                            "Requires: pip install xil-pipeline[tts-alt] (gtts) or "
                            "a configured venv-chatterbox (chatterbox)."
                        ))
    parser.add_argument("--chatterbox-python", default=None, metavar="PATH",
                        help=(
                            "Path to the Python executable in the chatterbox venv "
                            "(default: auto-detect ./venv-chatterbox/bin/python3). "
                            "Used only with --backend chatterbox."
                        ))
    parser.add_argument("--voice-refs", default="voice_refs", metavar="DIR",
                        help=(
                            "Directory of <speaker_key>.wav reference clips for Chatterbox "
                            "zero-shot voice cloning (default: voice_refs/). "
                            "Missing refs fall back to Chatterbox's default voice."
                        ))
    parser.add_argument("--exaggeration", type=float, default=0.5, metavar="FLOAT",
                        help=(
                            "Chatterbox emotion exaggeration level: 0.0 = flat/monotone, "
                            "1.0 = dramatically expressive (default: 0.5). "
                            "Used only with --backend chatterbox."
                        ))
    return parser


def main() -> None:
    """CLI entry point for voice stem generation.

    Loads the parsed script and cast config, then generates MP3 stems
    via the ElevenLabs TTS API. Use ``--dry-run`` to preview character
    costs before committing API quota. For audio assembly, run
    ``XILP003_audio_assembly.py`` separately.
    """
    configure_logging()
    with run_banner():
        args = get_parser().parse_args()

        if not args.dry_run and args.backend == "elevenlabs" and not os.environ.get("ELEVENLABS_API_KEY"):
            sys.exit("Error: ELEVENLABS_API_KEY environment variable is not set.")

        # Derive config paths from --episode / --tag
        tag = args.episode or args.tag
        slug = resolve_slug(args.show)
        paths = derive_paths(slug, tag)
        cast_path = paths["cast"]
        sfx_path = paths["sfx"]

        # Always load cast_cfg for metadata (preamble, season_title, tag)
        if not os.path.exists(cast_path):
            sys.exit(f"Error: Cast config not found: {cast_path}\nRun XILP001 first or check your --episode flag.")
        with open(cast_path, encoding="utf-8") as f:
            cast_data = json.load(f)
        cast_cfg = CastConfiguration(**cast_data)

        # Derive default --script path from cast config metadata
        if args.script is None:
            args.script = paths["parsed"]

        config, dialogue_entries, tag = load_production(args.script, cast_path)
        stems_dir = os.path.join(STEMS_DIR, slug, tag)

        if args.terse:
            dialogue_entries = [
                {**e, "text": truncate_to_words(e["text"])} for e in dialogue_entries
            ]

        # Load SFX config (always, for preamble music lookup)
        sfx_config_model = None
        sfx_config_data = None
        if os.path.exists(sfx_path):
            with open(sfx_path, encoding="utf-8") as f:
                sfx_config_data = json.load(f)
            sfx_config_model = SfxConfiguration(**sfx_config_data)

        # Build direction_types filter from gen flags (--sfx-music is deprecated all-in-one)
        gen_sfx      = args.gen_sfx      or args.sfx_music
        gen_music    = args.gen_music    or args.sfx_music
        gen_ambience = args.gen_ambience or args.sfx_music
        sfx_entries = None
        if gen_sfx or gen_music or gen_ambience:
            direction_types: set[str] = set()
            if gen_sfx:
                direction_types |= {"SFX", "BEAT"}
            if gen_music:
                direction_types.add("MUSIC")
            if gen_ambience:
                direction_types.add("AMBIENCE")
            sfx_entries = load_sfx_entries(args.script, sfx_path,
                                           direction_types=direction_types,
                                           local_only=args.local_only)
            # Pre-filter SFX entries to the requested range
            if args.stop_at is not None:
                sfx_entries = [e for e in sfx_entries if e["seq"] <= args.stop_at]

        # --- Preamble ---
        speaker = cast_cfg.preamble.speaker if cast_cfg.preamble else "tina"
        preamble_voice_stem = os.path.join(stems_dir, f"n002_preamble_{speaker}.mp3")
        preamble_music_stem = os.path.join(stems_dir, "n001_preamble_sfx.mp3")

        # Warn if preamble stems exist on disk but the cast config has no preamble block —
        # the stems will never be injected into the parsed JSON and will be invisible to xil-daw.
        if not cast_cfg.preamble:
            orphaned = [s for s in (preamble_voice_stem, preamble_music_stem) if os.path.exists(s)]
            if orphaned:
                for s in orphaned:
                    logger.warning(
                        "Preamble stem exists but cast config has no preamble block — "
                        "add a preamble block to your cast config so this stem is injected "
                        "into the parsed JSON: %s", s,
                    )

        # Resolve the full preamble text (used for dry-run char count + legacy path)
        preamble_text = None
        if cast_cfg.preamble:
            preamble_text = _resolve_preamble_text(cast_cfg)

        # --- Postamble stem names (seqs determined at inject time; derive here for dry-run) ---
        postamble_text = None
        postamble_voice_stem = None
        postamble_music_stem = None
        if cast_cfg.postamble and os.path.exists(args.script):
            postamble_text = _resolve_postamble_text(cast_cfg)
            with open(args.script, encoding="utf-8") as f:
                _parsed = json.load(f)
            _episode_seqs = [e["seq"] for e in _parsed["entries"]
                             if e["seq"] > 0 and e.get("section") != "postamble"]
            _max_seq = max(_episode_seqs) if _episode_seqs else 0
            spk_post = cast_cfg.postamble.speaker
            # music (max+1) precedes voice (max+2)
            postamble_music_stem = os.path.join(stems_dir, f"{_max_seq + 1:03d}_postamble_sfx.mp3")
            postamble_voice_stem = os.path.join(stems_dir, f"{_max_seq + 2:03d}_postamble_{spk_post}.mp3")

        if args.dry_run:
            if cast_cfg.preamble:
                _dry_run_preamble(cast_cfg, preamble_voice_stem)
            dry_run(config, dialogue_entries, start_from=args.start_from,
                    stop_at=args.stop_at,
                    sfx_entries=sfx_entries, sfx_config=sfx_config_data,
                    stems_dir=stems_dir)
            if cast_cfg.postamble and postamble_voice_stem:
                _dry_run_postamble(cast_cfg, postamble_voice_stem)
        else:
            if args.backend == "elevenlabs":
                check_elevenlabs_quota()
            # --- Pre-flight: validate all SFX source files exist before spending any credits ---
            if sfx_entries and sfx_config_data:
                _sfx_cfg_pf = SfxConfiguration(**sfx_config_data)
                _missing = []
                for _entry in sfx_entries:
                    _effect = _sfx_cfg_pf.effects.get(_entry["text"])
                    if _effect and _effect.source is not None and not os.path.exists(_effect.source):
                        _missing.append(f"  '{_entry['text']}' → {_effect.source}")
                if _missing:
                    logger.error(
                        "%d SFX source file(s) declared but missing — fix sfx config before generating:",
                        len(_missing),
                    )
                    for _msg in _missing:
                        logger.error(_msg)
                    sys.exit(1)

            # --- Chatterbox client (backend=chatterbox only) ---
            chatterbox_client: _ChatterboxClient | None = None
            if args.backend == "chatterbox":
                cb_python = args.chatterbox_python
                if cb_python is None:
                    # Auto-detect: look for venv-chatterbox adjacent to CWD
                    cb_default = os.path.join(os.getcwd(), "venv-chatterbox", "bin", "python3")
                    if os.path.exists(cb_default):
                        cb_python = cb_default
                    else:
                        logger.error(
                            "Cannot find chatterbox venv Python. "
                            "Pass --chatterbox-python PATH or create venv-chatterbox/ in the project root."
                        )
                        sys.exit(1)
                chatterbox_client = _ChatterboxClient(
                    python_path=cb_python,
                    voice_refs_dir=args.voice_refs,
                    exaggeration=args.exaggeration,
                )

            try:
                if cast_cfg.preamble:
                    os.makedirs(stems_dir, exist_ok=True)
                    _generate_preamble_voice(cast_cfg, config, preamble_voice_stem,
                                             backend=args.backend,
                                             chatterbox_client=chatterbox_client)
                    # Copy intro music from sfx config 'INTRO MUSIC' source (always regenerate — free local copy)
                    if sfx_config_model and "INTRO MUSIC" in sfx_config_model.effects:
                        intro_entry = sfx_config_model.effects["INTRO MUSIC"]
                        if intro_entry.source:
                            clip = AudioSegment.from_file(intro_entry.source)
                            if intro_entry.play_duration is not None:
                                trim_ms = int(len(clip) * intro_entry.play_duration / 100.0)
                                clip = clip[:trim_ms]
                                logger.info("   Trimmed intro music to %.1fs (%s%%)", trim_ms/1000, intro_entry.play_duration)
                            clip.export(preamble_music_stem, format="mp3")
                            logger.info("   Saved: %s", preamble_music_stem)
                            _log_stem_hash(preamble_music_stem)
                        else:
                            logger.warning("INTRO MUSIC entry has no 'source' — skipping music stem")
                    else:
                        logger.warning("No 'INTRO MUSIC' entry in sfx config — skipping music stem")
                generate_voices(config, dialogue_entries, stems_dir,
                                start_from=args.start_from, stop_at=args.stop_at,
                                show=cast_cfg.show, backend=args.backend,
                                chatterbox_client=chatterbox_client)
                if sfx_entries and sfx_config_data:
                    generate_sfx_stems(sfx_entries, sfx_config_data, stems_dir,
                                       client=client, start_from=args.start_from)
                # Inject preamble entries into parsed JSON (idempotent)
                if cast_cfg.preamble and preamble_text is not None and os.path.exists(args.script):
                    inject_preamble_entries(args.script, preamble_text, cast_cfg.preamble.speaker)
                # --- Postamble ---
                if cast_cfg.postamble and postamble_text is not None and os.path.exists(args.script):
                    os.makedirs(stems_dir, exist_ok=True)
                    music_seq, voice_seq = inject_postamble_entries(
                        args.script, postamble_text, cast_cfg.postamble.speaker
                    )
                    spk_post = cast_cfg.postamble.speaker
                    postamble_music_stem = os.path.join(stems_dir, f"{music_seq:03d}_postamble_sfx.mp3")
                    postamble_voice_stem = os.path.join(stems_dir, f"{voice_seq:03d}_postamble_{spk_post}.mp3")
                    # Copy outro music from sfx config 'OUTRO MUSIC' source (always regenerate — free local copy)
                    if sfx_config_model and "OUTRO MUSIC" in sfx_config_model.effects:
                        outro_entry = sfx_config_model.effects["OUTRO MUSIC"]
                        if outro_entry.source:
                            clip = AudioSegment.from_file(outro_entry.source)
                            if outro_entry.play_duration is not None:
                                trim_ms = int(len(clip) * outro_entry.play_duration / 100.0)
                                clip = clip[:trim_ms]
                                logger.info("   Trimmed outro music to %.1fs (%s%%)", trim_ms/1000, outro_entry.play_duration)
                            clip.export(postamble_music_stem, format="mp3")
                            logger.info("   Saved: %s", postamble_music_stem)
                            _log_stem_hash(postamble_music_stem)
                        else:
                            logger.warning("OUTRO MUSIC entry has no 'source' — skipping outro music stem")
                    _generate_postamble_voice(cast_cfg, config, postamble_voice_stem,
                                             backend=args.backend,
                                             chatterbox_client=chatterbox_client)
            finally:
                if chatterbox_client is not None:
                    chatterbox_client.close()


if __name__ == "__main__":
    main()
