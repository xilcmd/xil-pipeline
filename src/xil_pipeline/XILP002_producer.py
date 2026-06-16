# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
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
import datetime
import json
import os
import re
import sys
import tempfile

from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs
from elevenlabs.core.api_error import ApiError

try:
    from gtts import gTTS as _gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False

import subprocess

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.models import (
    CastConfiguration,
    DialogueEntry,
    SfxConfiguration,
    VoiceConfig,
    derive_paths,
    get_workspace_root,
    resolve_slug,
    resolve_venv_python,
)
from xil_pipeline.sfx_backends import make_sfx_backend
from xil_pipeline.sfx_common import (
    dry_run_sfx,
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


# ── Stem manifest helpers ─────────────────────────────────────────────────────

def _manifest_path(stems_dir: str) -> str:
    """Return the stem manifest JSON path inside *stems_dir*."""
    tag = os.path.basename(stems_dir.rstrip(os.sep))
    return os.path.join(stems_dir, f"{tag}_stem_manifest.json")


def _load_manifest(path: str) -> dict:
    """Load the stem manifest from *path*, returning an empty manifest when absent or corrupt."""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "entries": []}


def _save_manifest(path: str, manifest: dict) -> None:
    """Atomically write *manifest* to *path* via a temp-file rename."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    os.replace(tmp, path)


def _manifest_content_key(text: str, voice_id: str, speed: float,
                           stability: float, similarity_boost: float,
                           backend: str) -> tuple:
    """Build a deduplication key from TTS parameters for manifest upsert."""
    return (text, voice_id or "", round(speed or 1.0, 4), round(stability or 0.5, 4),
            round(similarity_boost or 0.75, 4), backend)


def _manifest_upsert(manifest: dict, by_key: dict, entry: dict) -> None:
    """Insert or update *entry* in *manifest*, deduplicating by content key."""
    key = _manifest_content_key(
        entry["text"], entry["voice_id"], entry["speed"],
        entry["stability"], entry["similarity_boost"], entry["backend"],
    )
    if key in by_key:
        old = by_key[key]
        old.update(entry)
    else:
        manifest["entries"].append(entry)
        by_key[key] = entry


# Setup ElevenLabs Client
client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))

STEMS_DIR = str(get_workspace_root() / "stems")


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
            "speed": member.speed,
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
            section=entry.get("section"),
            direction=entry.get("direction"),
        )
        dialogue_entries.append(de.model_dump())

    return config, dialogue_entries, tag


def dry_run(
    config: dict[str, dict], dialogue_entries: list[dict], start_from: int = 1,
    stop_at: int | None = None,
    sfx_entries: list[dict] | None = None, sfx_config: dict | None = None,
    stems_dir: str = "", force: bool = False,
    sfx_backend_name: str = "elevenlabs",
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

        stem_exists = (not force) and bool(
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
        dry_run_sfx(sfx_entries, sfx_config, stems_dir, backend_name=sfx_backend_name)

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
    force: bool = False,
    manifest_path: str | None = None,
    section_speed_overrides: dict[str, float] | None = None,
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
    run_started_at = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

    # Load (or create) the stem manifest for this episode
    mf_path = manifest_path or _manifest_path(stems_dir)
    manifest = _load_manifest(mf_path)
    by_key: dict[tuple, dict] = {}
    for mentry in manifest["entries"]:
        k = _manifest_content_key(
            mentry["text"], mentry["voice_id"], mentry["speed"],
            mentry["stability"], mentry["similarity_boost"], mentry["backend"],
        )
        by_key[k] = mentry

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
        tts_comment = current_model if backend == "elevenlabs" else backend

        # Skip if stem already exists (unless --force)
        stem_file = os.path.join(stems_dir, f"{stem_name}.mp3")
        cfg = config.get(speaker, {})
        if os.path.exists(stem_file):
            if not force:
                logger.info("   Exists: %s — skipping", stem_file)
                mkey = _manifest_content_key(
                    text, cfg.get("id", ""), cfg.get("speed", 1.0),
                    cfg.get("stability", 0.5), cfg.get("similarity_boost", 0.75), backend,
                )
                if mkey not in by_key:
                    try:
                        sha256_hex = _hash_file(stem_file)
                        _manifest_upsert(manifest, by_key, {
                            "text": text, "speaker": speaker,
                            "voice_id": cfg.get("id", ""),
                            "speed": cfg.get("speed", 1.0),
                            "stability": cfg.get("stability", 0.5),
                            "similarity_boost": cfg.get("similarity_boost", 0.75),
                            "backend": backend, "model": tts_comment, "sha256": sha256_hex,
                            "seq_at_generation": entry["seq"],
                            "stem_filename": os.path.basename(stem_file),
                            "generated_at": "",
                        })
                    except Exception:
                        pass
                continue
            logger.warning("   Force: overwriting %s", os.path.basename(stem_file))

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
        section = entry.get("section")
        if section and section_speed_overrides and section in section_speed_overrides:
            cfg = dict(cfg)
            cfg["speed"] = section_speed_overrides[section]
        vs_fields = {
            k: cfg[k] for k in ("stability", "similarity_boost", "style", "use_speaker_boost", "speed")
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
            comments=tts_comment,
        )
        logger.info("   Saved: %s", stem_file)
        _log_stem_hash(stem_file)
        try:
            sha256_hex = _hash_file(stem_file)
            _manifest_upsert(manifest, by_key, {
                "text": text, "speaker": speaker,
                "voice_id": cfg.get("id", ""),
                "speed": cfg.get("speed", 1.0),
                "stability": cfg.get("stability", 0.5),
                "similarity_boost": cfg.get("similarity_boost", 0.75),
                "backend": backend, "model": tts_comment, "sha256": sha256_hex,
                "seq_at_generation": entry["seq"],
                "stem_filename": os.path.basename(stem_file),
                "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            })
        except Exception:
            pass
        generated_count += 1

    stem_count = len([f for f in os.listdir(stems_dir) if f.endswith(".mp3")])
    logger.info("--- Phase 1 Complete: %d new, %d total stems in %s/ ---", generated_count, stem_count, stems_dir)
    try:
        _save_manifest(mf_path, manifest)
        logger.info("   Manifest: %s (%d entries)", os.path.basename(mf_path), len(manifest["entries"]))
        snap_path = mf_path.replace(".json", f"_{run_started_at}.json")
        _save_manifest(snap_path, manifest)
        logger.info("   Snapshot: %s", os.path.basename(snap_path))
    except Exception as exc:
        logger.warning("Could not write stem manifest: %s", exc)



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
        """Spawn the Chatterbox worker subprocess and wait for its ready signal."""
        logger.info("Starting Chatterbox worker (%s, %s)…", self._python, self._device)
        self._proc = subprocess.Popen(
            [self._python, self._WORKER, self._device],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            text=True,
            bufsize=1,
        )
        # Chatterbox prints non-JSON startup noise to stdout (e.g. "loaded PerthNet …")
        # before emitting the ready signal. Loop until we find a valid JSON ready line.
        while True:
            raw = self._proc.stdout.readline()
            if not raw:
                raise RuntimeError(
                    "Chatterbox worker exited before sending ready signal. "
                    "Check that venv-chatterbox is correctly set up and the model is downloaded."
                )
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
        """Return the voice reference file path for *speaker_key*, or None if absent."""
        for ext in (".wav", ".mp3"):
            p = os.path.join(self._voice_refs_dir, f"{speaker_key}{ext}")
            if os.path.exists(p):
                return p
        return None

    def _cond_for(self, speaker_key: str) -> str:
        """Return the Chatterbox conditioning tensor path for *speaker_key*."""
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


def _print_voice_refs_table(config: dict[str, dict], voice_refs_dir: str) -> None:
    """Print a voice_refs coverage table for the chatterbox backend."""
    speakers = sorted(config.keys())
    has_ref = {}
    for spk in speakers:
        wav = os.path.join(voice_refs_dir, f"{spk}.wav")
        conds = os.path.join(voice_refs_dir, f"{spk}.conds.pt")
        has_ref[spk] = os.path.exists(wav) or os.path.exists(conds)

    missing = [s for s in speakers if not has_ref[s]]
    found   = [s for s in speakers if has_ref[s]]

    logger.info("VOICE REFS  (%s)", voice_refs_dir)
    logger.info("  %-18s  %s", "Speaker", "Ref")
    logger.info("  %s", "-" * 30)
    for spk in speakers:
        mark = "✓" if has_ref[spk] else "✗  (fallback to default voice)"
        logger.info("  %-18s  %s", spk, mark)
    logger.info("  %s", "-" * 30)
    logger.info("  %d / %d have voice refs", len(found), len(speakers))
    if missing:
        logger.warning("  Missing refs: %s", ", ".join(missing))
        logger.warning("  Add <key>.wav to %s to use a reference voice.", voice_refs_dir)
    logger.info("")


def reconcile(
    config: dict[str, dict],
    dialogue_entries: list[dict],
    stems_dir: str,
    backend: str = "elevenlabs",
    apply: bool = False,
) -> None:
    """Re-link existing stems to new seq-numbered filenames after a re-parse.

    Reads the stem manifest and the current dialogue entries (from the new
    parsed JSON), matches each entry by content key (text + voice settings),
    verifies SHA-256 integrity, and renames the file if needed.  Prints a
    plan by default; pass ``apply=True`` to execute.

    Args:
        config: Speaker-to-voice mapping from ``load_production()``.
        dialogue_entries: Dialogue entry dicts from the new parsed JSON.
        stems_dir: Episode stems directory.
        backend: TTS backend label used as part of the content key.
        apply: If True, execute the renames and update the manifest.
    """
    mf_path = _manifest_path(stems_dir)
    if not os.path.exists(mf_path):
        logger.error(
            "No stem manifest at %s — run 'xil produce' first to build it.",
            mf_path,
        )
        return

    manifest = _load_manifest(mf_path)
    by_key: dict[tuple, dict] = {}
    for mentry in manifest["entries"]:
        k = _manifest_content_key(
            mentry["text"], mentry["voice_id"], mentry["speed"],
            mentry["stability"], mentry["similarity_boost"], mentry["backend"],
        )
        by_key[k] = mentry

    to_rename: list[tuple] = []   # (src_path, dst_path, mentry, de)
    to_generate: list[tuple] = [] # (de, reason)
    already_correct = 0

    for de in dialogue_entries:
        speaker = de["speaker"]
        cfg = config.get(speaker, {})
        text = de["text"]
        key = _manifest_content_key(
            text, cfg.get("id", ""), cfg.get("speed", 1.0),
            cfg.get("stability", 0.5), cfg.get("similarity_boost", 0.75), backend,
        )
        expected_filename = de["stem_name"] + ".mp3"
        expected_path = os.path.join(stems_dir, expected_filename)

        if os.path.exists(expected_path):
            already_correct += 1
            continue

        if key not in by_key:
            to_generate.append((de, "not in manifest"))
            continue

        mentry = by_key[key]
        current_path = os.path.join(stems_dir, mentry["stem_filename"])

        if not os.path.exists(current_path):
            to_generate.append((de, f"manifest file missing: {mentry['stem_filename']}"))
            continue

        try:
            actual = _hash_file(current_path)
        except Exception:
            actual = None
        if actual != mentry["sha256"]:
            to_generate.append((de, f"SHA-256 mismatch for {mentry['stem_filename']}"))
            continue

        to_rename.append((current_path, expected_path, mentry, de))

    logger.info(
        "--- Reconcile: %d correct, %d to re-link, %d need new TTS ---",
        already_correct, len(to_rename), len(to_generate),
    )
    for src, dst, _, _ in to_rename:
        logger.info("  RELINK  %s → %s", os.path.basename(src), os.path.basename(dst))
    for de, reason in to_generate:
        logger.info("  MISSING seq %03d %s: %s", de["seq"], de["speaker"], reason)

    if not apply:
        logger.info("  (dry-run — pass --apply to execute renames)")
        return

    for src, dst, mentry, de in to_rename:
        os.rename(src, dst)
        mentry["stem_filename"] = os.path.basename(dst)
        mentry["seq_at_generation"] = de["seq"]
        logger.info("  Relinked: %s", os.path.basename(dst))

    if to_rename:
        _save_manifest(mf_path, manifest)
        logger.info("  Manifest updated: %s", os.path.basename(mf_path))


def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-produce."""
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
    parser.add_argument("--reconcile", action="store_true",
                        help="Re-link existing stems to new seq filenames after a re-parse "
                             "(reads stem manifest, proposes renames, no TTS calls). "
                             "Add --apply to execute.")
    parser.add_argument("--apply", action="store_true",
                        help="With --reconcile: execute the renames instead of dry-run preview.")
    parser.add_argument("--force", action="store_true", default=False,
                        help="Overwrite existing stem files instead of skipping them. "
                             "Use with --range to regenerate specific lines. "
                             "WARNING: incurs ElevenLabs API cost for every stem in range.")
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
    parser.add_argument("--voice-refs", default=None, metavar="DIR",
                        help=(
                            "Directory of <speaker_key>.wav reference clips for Chatterbox "
                            "zero-shot voice cloning (default: <workspace>/voice_refs/). "
                            "Missing refs fall back to Chatterbox's default voice."
                        ))
    parser.add_argument("--exaggeration", type=float, default=0.5, metavar="FLOAT",
                        help=(
                            "Chatterbox emotion exaggeration level: 0.0 = flat/monotone, "
                            "1.0 = dramatically expressive (default: 0.5). "
                            "Used only with --backend chatterbox."
                        ))
    parser.add_argument("--cfg-weight", type=float, default=0.5, metavar="FLOAT",
                        help=(
                            "Chatterbox CFG weight controlling pacing/delivery: lower values "
                            "(e.g. 0.3) produce slower, more deliberate speech and help "
                            "compensate for acceleration at high exaggeration (default: 0.5). "
                            "Used only with --backend chatterbox."
                        ))
    parser.add_argument("--sfx-backend", choices=["elevenlabs", "audioldm2"],
                        default="elevenlabs", metavar="BACKEND",
                        help=(
                            "Backend for SFX/music/ambience generation, independent of the "
                            "dialogue --backend. 'elevenlabs' (default) calls the ElevenLabs "
                            "Sound Effects API. 'audioldm2' uses a local AudioLDM 2 Large "
                            "diffusion model (venv-audioldm2) — free, GPU-accelerated, writes "
                            "backend-tagged assets to SFX/<slug>.audioldm2.mp3."
                        ))
    parser.add_argument("--audioldm2-python", default=None, metavar="PATH",
                        help=(
                            "Path to the Python executable in the AudioLDM 2 venv "
                            "(default: auto-detect ./venv-audioldm2/bin/python3). "
                            "Used only with --sfx-backend audioldm2."
                        ))
    parser.add_argument("--audioldm2-guidance", type=float, default=3.5, metavar="FLOAT",
                        help=(
                            "AudioLDM 2 guidance scale — how closely generation follows the "
                            "prompt (default: 3.5). Used only with --sfx-backend audioldm2."
                        ))
    parser.add_argument("--audioldm2-steps", type=int, default=200, metavar="INT",
                        help=(
                            "AudioLDM 2 diffusion inference steps — higher is slower but "
                            "cleaner (default: 200). Used only with --sfx-backend audioldm2."
                        ))
    parser.add_argument("--audioldm2-negative-prompt", default="low quality, noise",
                        metavar="STR",
                        help=(
                            "AudioLDM 2 negative prompt (default: 'low quality, noise'). "
                            "Used only with --sfx-backend audioldm2."
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

        # ElevenLabs key is needed when dialogue uses elevenlabs, or when SFX
        # generation is requested with the elevenlabs sfx backend (audioldm2 is local).
        _sfx_gen_requested = args.gen_sfx or args.gen_music or args.gen_ambience or args.sfx_music
        _needs_el_key = args.backend == "elevenlabs" or (
            args.sfx_backend == "elevenlabs" and _sfx_gen_requested
        )
        if not args.dry_run and _needs_el_key and not os.environ.get("ELEVENLABS_API_KEY"):
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

        sfx_config_data = None
        if os.path.exists(sfx_path):
            with open(sfx_path, encoding="utf-8") as f:
                sfx_config_data = json.load(f)

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
                direction_types.add("VINTAGE FILTER")
            sfx_entries = load_sfx_entries(args.script, sfx_path,
                                           direction_types=direction_types,
                                           local_only=args.local_only)
            # Pre-filter SFX entries to the requested range
            if args.stop_at is not None:
                sfx_entries = [e for e in sfx_entries if e["seq"] <= args.stop_at]

        # --- Section-level speed overrides (preamble/postamble read at a different rate) ---
        section_speed_overrides: dict[str, float] = {}
        if cast_cfg.preamble and cast_cfg.preamble.speed is not None:
            section_speed_overrides["preamble"] = cast_cfg.preamble.speed
        if cast_cfg.postamble and cast_cfg.postamble.speed is not None:
            section_speed_overrides["postamble"] = cast_cfg.postamble.speed

        if args.voice_refs is None:
            args.voice_refs = str(get_workspace_root() / "voice_refs")

        if args.backend == "chatterbox":
            _print_voice_refs_table(config, args.voice_refs)

        if args.reconcile:
            reconcile(config, dialogue_entries, stems_dir,
                      backend=args.backend, apply=args.apply)
        elif args.dry_run:
            dry_run(config, dialogue_entries, start_from=args.start_from,
                    stop_at=args.stop_at,
                    sfx_entries=sfx_entries, sfx_config=sfx_config_data,
                    stems_dir=stems_dir, force=args.force,
                    sfx_backend_name=args.sfx_backend)
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
                # Resolution: --chatterbox-python → $XIL_CODEROOT/venv-chatterbox
                # (exclusive when set) → auto-detect (workspace root, then repo root).
                cb_python = resolve_venv_python("venv-chatterbox", args.chatterbox_python)
                if cb_python is None:
                    logger.error(
                        "Cannot find chatterbox venv Python. Pass --chatterbox-python PATH, "
                        "set XIL_CODEROOT to the directory containing venv-chatterbox/, "
                        "or create venv-chatterbox/ in the workspace or repo root."
                    )
                    sys.exit(1)
                chatterbox_client = _ChatterboxClient(
                    python_path=cb_python,
                    voice_refs_dir=args.voice_refs,
                    exaggeration=args.exaggeration,
                    cfg_weight=args.cfg_weight,
                )

            # --- SFX backend (built only when SFX generation is requested) ---
            sfx_backend = None
            if sfx_entries and sfx_config_data:
                sfx_backend = make_sfx_backend(
                    args.sfx_backend,
                    client=client,
                    audioldm2_python=args.audioldm2_python,
                    guidance=args.audioldm2_guidance,
                    steps=args.audioldm2_steps,
                    negative_prompt=args.audioldm2_negative_prompt,
                )

            try:
                generate_voices(config, dialogue_entries, stems_dir,
                                start_from=args.start_from, stop_at=args.stop_at,
                                show=cast_cfg.show, backend=args.backend,
                                chatterbox_client=chatterbox_client,
                                force=args.force,
                                section_speed_overrides=section_speed_overrides or None)
                if sfx_entries and sfx_config_data:
                    generate_sfx_stems(sfx_entries, sfx_config_data, stems_dir,
                                       client=client, start_from=args.start_from,
                                       backend=sfx_backend)
            finally:
                if chatterbox_client is not None:
                    chatterbox_client.close()
                if sfx_backend is not None:
                    sfx_backend.close()


if __name__ == "__main__":
    main()
