"""Generate individual voice stems via the ElevenLabs TTS API.

Reads parsed script JSON and cast configuration to produce one MP3 stem
per dialogue line. Audio assembly is handled separately by XILP003.

Module Attributes:
    STEMS_DIR: Directory for generated voice stem MP3 files.
"""

import os
import json
import shutil
import argparse
from pydub import AudioSegment
from elevenlabs import VoiceSettings
from elevenlabs.client import ElevenLabs

from models import VoiceConfig, DialogueEntry, CastConfiguration, SfxConfiguration, episode_tag
from sfx_common import (
    load_sfx_entries,
    generate_sfx as generate_sfx_stems,
    dry_run_sfx,
    tag_mp3,
    run_banner,
)

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

        print(f"\n" + "="*40)
        print(f"ELEVENLABS API STATUS:")
        print(f"  Tier:      {sub.tier.upper()}")
        print(f"  Usage:     {used:,} / {limit:,} characters")
        print(f"  Remaining: {remaining:,}")
        print("="*40 + "\n")

        return remaining
    except Exception as e:
        print(f"\n[!] API Error: Unable to fetch user subscription data.")
        print(f"    Details: {e}")
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
            print(f" [Guard] Quota OK: {required} required, {remaining:,} left.")
            return True
        else:
            print(f" [Guard] STOP: Line requires {required} chars, but only {remaining:,} remain.")
            return False
    except Exception as e:
        print(f" [Guard] Warning: Permission 'user_read' missing. Skipping quota check.")
        return True


def get_best_model_for_budget() -> str:
    """Select the best ElevenLabs TTS model based on remaining quota.

    Returns:
        Model ID string: ``"eleven_multilingual_v2"`` for healthy balance,
        ``"eleven_flash_v2_5"`` when low, or ``"eleven_multilingual_v2"``
        as API-error fallback.
    """
    SAFE_THRESHOLD = 5000

    try:
        user_info = client.user.get()
        remaining = user_info.subscription.character_limit - user_info.subscription.character_count

        if remaining > SAFE_THRESHOLD:
            print(f" [Budget] Healthy Balance: {remaining:,} left. Using 'eleven_multilingual_v2'.")
            return "eleven_multilingual_v2"
        else:
            print(f" [Budget] LOW BALANCE: {remaining:,} left. Switching to 'eleven_flash_v2_5' (50% cheaper).")
            return "eleven_flash_v2_5"

    except Exception:
        print(" [Budget] API Check Failed. Defaulting to 'eleven_multilingual_v2'.")
        return "eleven_multilingual_v2"


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
    with open(cast_json_path, "r", encoding="utf-8") as f:
        cast_data = json.load(f)

    with open(script_json_path, "r", encoding="utf-8") as f:
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
    print(f"\n{'='*70}")
    print(f"DRY RUN — {len(dialogue_entries)} dialogue lines")
    print(f"{'='*70}")
    print(f" [.] {'seq':<3} | {'speaker':<14} | {'chars':>10} | voice check [lang]")
    print(f" {'-'*67}")

    total_chars = 0
    lines_to_generate = 0

    for entry in dialogue_entries:
        char_count = len(entry["text"])
        total_chars += char_count
        in_range = entry["seq"] >= start_from and (stop_at is None or entry["seq"] <= stop_at)
        marker = " " if in_range else "x"
        if in_range:
            lines_to_generate += 1

        direction_label = f" ({entry['direction']})" if entry["direction"] else ""
        text_preview = entry["text"][:75] + "..." if len(entry["text"]) > 75 else entry["text"]

        cfg = config.get(entry["speaker"], {})
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

        print(f" [{marker}] {entry['seq']:03d} | {entry['speaker']:<14} | {char_count:>4} chars | voice: {voice_status}{vs_note}{direction_label}")
        print(f"          {text_preview}")
        print(f"          stem: {entry['stem_name']}.mp3")
        print()

    # SFX entries — delegate to sfx_common.dry_run_sfx
    if sfx_entries and sfx_config:
        dry_run_sfx(sfx_entries, sfx_config, stems_dir)

    # Summary
    chars_in_range = sum(
        len(e["text"]) for e in dialogue_entries
        if e["seq"] >= start_from and (stop_at is None or e["seq"] <= stop_at)
    )
    tbd_voices = [sp for sp, cfg in config.items() if cfg["id"] == "TBD"]

    print(f"{'='*70}")
    print(f"TOTAL:  {len(dialogue_entries)} lines, {total_chars:,} TTS characters")
    if start_from > 1 or stop_at is not None:
        if stop_at is not None and start_from > 1:
            range_label = f"FROM {start_from}–{stop_at}"
        elif stop_at is not None:
            range_label = f"THRU {stop_at}"
        else:
            range_label = f"FROM {start_from}"
        print(f"{range_label}: {lines_to_generate} lines, {chars_in_range:,} TTS characters")
    if tbd_voices:
        print(f"\n  WARNING: {len(tbd_voices)} voices still need voice_id assignment: {', '.join(tbd_voices)}")
        print(f"  Use XILU001_discover_voices_T2S.py to browse voices, then update cast_the413.json")
    print(f"{'='*70}\n")


def generate_voices(
    config: dict[str, dict], dialogue_entries: list[dict],
    stems_dir: str, start_from: int = 1, stop_at: int | None = None,
) -> None:
    """Generate individual voice stem MP3s via the ElevenLabs TTS API.

    Iterates through dialogue entries, skipping stems that already exist
    on disk or have unassigned voice IDs. Halts if the character quota
    is exhausted.

    Args:
        config: Speaker-to-voice mapping from ``load_production()``.
        dialogue_entries: Dialogue entry dicts from ``load_production()``.
        stems_dir: Directory to write stem MP3 files into.
        start_from: Sequence number to resume generation from.
        stop_at: Sequence number to stop at, inclusive. ``None`` means
            process all entries from ``start_from`` onward.
    """
    os.makedirs(stems_dir, exist_ok=True)

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
    print(f"--- Phase 1: Generating {len(entries_to_process)} voice stems{range_note} ---")
    current_model = get_best_model_for_budget()
    generated_count = 0

    for entry in entries_to_process:
        speaker = entry["speaker"]
        text = entry["text"]
        stem_name = entry["stem_name"]

        # Skip if stem already exists
        stem_file = os.path.join(stems_dir, f"{stem_name}.mp3")
        if os.path.exists(stem_file):
            print(f"   Exists: {stem_file} — skipping")
            continue

        # Check voice_id is assigned
        if config.get(speaker, {}).get("id") == "TBD":
            print(f" [!] No voice_id for {speaker} — skipping {stem_name}")
            continue

        # Check quota
        if not has_enough_characters(text):
            print(f" !!! Production halted at seq {entry['seq']} to save credits.")
            break

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
        if cfg.get("language_code"):
            extra_kwargs["language_code"] = cfg["language_code"]
        if prev_text:
            extra_kwargs["previous_text"] = prev_text
        if next_text:
            extra_kwargs["next_text"] = next_text

        print(f" > [{entry['seq']:03d}] {speaker} with {current_model} ({len(text)} chars)...")
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
            title=f"{full_name}: {first_five}",
            artist=full_name,
            lyrics=text,
        )
        print(f"   Saved: {stem_file}")
        generated_count += 1

    stem_count = len([f for f in os.listdir(stems_dir) if f.endswith(".mp3")])
    print(f"--- Phase 1 Complete: {generated_count} new, {stem_count} total stems in {stems_dir}/ ---")



def inject_preamble_entries(parsed_path: str, preamble_text: str, speaker: str) -> None:
    """Prepend seq -2 (voice) and -1 (INTRO MUSIC) entries into the parsed JSON.

    Idempotent: strips any existing seq <= 0 entries before prepending so
    re-running XILP002 replaces rather than duplicates preamble entries.

    Args:
        parsed_path: Path to the parsed script JSON file (modified in place).
        preamble_text: Resolved preamble text (placeholders already substituted).
        speaker: Cast key for the TTS speaker (e.g. "tina").
    """
    with open(parsed_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Strip any existing preamble entries (seq <= 0)
    data["entries"] = [e for e in data["entries"] if e["seq"] > 0]
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
    print(f"   Injected preamble entries (seq -2, -1) into {parsed_path}")


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
    with open(parsed_path, "r", encoding="utf-8") as f:
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
    print(f"   Injected postamble entries (seq {music_seq} OUTRO MUSIC, {voice_seq} voice) into {parsed_path}")
    return music_seq, voice_seq


# ---------------------------------------------------------------------------
# Preamble / postamble helpers
# ---------------------------------------------------------------------------

def _episode_kwargs(cast_cfg) -> dict:
    return dict(
        season_title=cast_cfg.season_title or "",
        episode=cast_cfg.episode,
        title=cast_cfg.title or "",
    )


def _resolve_voice_block_text(block, cast_cfg) -> str:
    """Resolve a Preamble/postamble block to its full spoken text string.

    Joins segments (no separator) or formats the legacy single-string form.
    """
    kwargs = _episode_kwargs(cast_cfg)
    if block.segments:
        return "".join(seg.text.format(**kwargs) for seg in block.segments)
    return block.text.format(**kwargs)


def _resolve_preamble_text(cast_cfg) -> str:
    return _resolve_voice_block_text(cast_cfg.preamble, cast_cfg)


def _resolve_postamble_text(cast_cfg) -> str:
    return _resolve_voice_block_text(cast_cfg.postamble, cast_cfg)


def _tts_segment(text: str, out_path: str, voice_id: str, speed: float | None) -> None:
    """Call ElevenLabs TTS and write the result to *out_path*.

    Uses a ``.tmp`` staging file so a partial write is never mistaken for a
    complete asset.
    """
    tmp = out_path + ".tmp"
    current_model = get_best_model_for_budget()
    print(f"   > TTS ({len(text)} chars) → {os.path.basename(out_path)} [{current_model}]")
    voice_settings = VoiceSettings(speed=speed) if speed is not None else None
    audio_stream = client.text_to_speech.convert(
        text=text,
        voice_id=voice_id,
        model_id=current_model,
        output_format="mp3_44100_128",
        voice_settings=voice_settings,
    )
    with open(tmp, "wb") as f:
        for chunk in audio_stream:
            if chunk:
                f.write(chunk)
    os.replace(tmp, out_path)


def _dry_run_voice_block(block, cast_cfg, stem_path: str, label: str) -> None:
    """Print dry-run summary for a preamble or postamble voice stem."""
    spk = block.speaker
    stem_exists = os.path.exists(stem_path) and os.path.getsize(stem_path) > 0
    if stem_exists:
        print(f" [{label}] {spk} — stem exists, will skip\n")
        return

    kwargs = _episode_kwargs(cast_cfg)
    if block.segments:
        print(f" [{label}] {spk} | {len(block.segments)} segments")
        total_new = 0
        for seg in block.segments:
            resolved = seg.text.format(**kwargs)
            if seg.shared_key:
                cached_path = os.path.join("SFX", f"{seg.shared_key}.mp3")
                status = "CACHED" if (
                    os.path.exists(cached_path) and os.path.getsize(cached_path) > 0
                ) else "NEW   "
                print(f"   {status}  SFX/{seg.shared_key}.mp3  ({len(resolved)} chars)")
                if status.strip() == "NEW":
                    total_new += len(resolved)
            else:
                print(f"   NEW     [episode variable]  ({len(resolved)} chars)")
                total_new += len(resolved)
        print(f"   Total NEW chars for TTS: {total_new}\n")
    else:
        resolved = block.text.format(**kwargs)
        print(f" [{label}] {spk} | {len(resolved)} chars")
        print(f"   stem: {os.path.basename(stem_path)}\n")


def _dry_run_preamble(cast_cfg, preamble_voice_stem: str) -> None:
    _dry_run_voice_block(cast_cfg.preamble, cast_cfg, preamble_voice_stem, "PREAMBLE")


def _dry_run_postamble(cast_cfg, postamble_voice_stem: str) -> None:
    _dry_run_voice_block(cast_cfg.postamble, cast_cfg, postamble_voice_stem, "POSTAMBLE")


def _generate_voice_block(block, cast_cfg, config: dict, voice_stem: str,
                           label: str, sfx_dir: str = "SFX") -> None:
    """Generate a voice stem from a Preamble/postamble block.

    For segment configs, stock segments (``shared_key`` set) are cached in
    *sfx_dir* and reused across episodes.  Episode-specific segments are
    generated to a temp file and cleaned up after concatenation.  The legacy
    single-text form generates the stem directly.
    """
    if os.path.exists(voice_stem) and os.path.getsize(voice_stem) > 0:
        print(f"   Exists: {voice_stem} — skipping")
        return

    spk = block.speaker
    voice_id = config.get(spk, {}).get("id", "TBD")
    if voice_id == "TBD":
        print(f" [!] No voice_id for {spk} — skipping {os.path.basename(voice_stem)}")
        return

    kwargs = _episode_kwargs(cast_cfg)

    if block.segments:
        segment_paths: list[str] = []
        tmp_files: list[str] = []
        for i, seg in enumerate(block.segments):
            resolved = seg.text.format(**kwargs)
            if not has_enough_characters(resolved):
                print(f" [!] Insufficient quota for {label.lower()} segment {i} — aborting")
                for f in tmp_files:
                    if os.path.exists(f):
                        os.remove(f)
                return
            if seg.shared_key:
                cached = os.path.join(sfx_dir, f"{seg.shared_key}.mp3")
                if os.path.exists(cached) and os.path.getsize(cached) > 0:
                    print(f"   CACHED  SFX/{seg.shared_key}.mp3")
                else:
                    os.makedirs(sfx_dir, exist_ok=True)
                    _tts_segment(resolved, cached, voice_id, block.speed)
                    print(f"   Saved:  SFX/{seg.shared_key}.mp3")
                segment_paths.append(cached)
            else:
                tmp = voice_stem + f".seg{i}.tmp.mp3"
                _tts_segment(resolved, tmp, voice_id, block.speed)
                segment_paths.append(tmp)
                tmp_files.append(tmp)

        print(f"   Concatenating {len(segment_paths)} segment(s) → {os.path.basename(voice_stem)}")
        combined = AudioSegment.empty()
        for p in segment_paths:
            combined += AudioSegment.from_file(p)
        combined.export(voice_stem, format="mp3")
        for f in tmp_files:
            if os.path.exists(f):
                os.remove(f)
        print(f"   Saved: {voice_stem}")
    else:
        resolved = block.text.format(**kwargs)
        if not has_enough_characters(resolved):
            print(f" [!] Insufficient quota for {label.lower()} — skipping")
            return
        print(f" > [{label}] {spk} ({len(resolved)} chars)...")
        _tts_segment(resolved, voice_stem, voice_id, block.speed)
        print(f"   Saved: {voice_stem}")


def _generate_preamble_voice(cast_cfg, config: dict, preamble_voice_stem: str,
                              sfx_dir: str = "SFX") -> None:
    _generate_voice_block(cast_cfg.preamble, cast_cfg, config,
                          preamble_voice_stem, "PREAMBLE", sfx_dir)


def _generate_postamble_voice(cast_cfg, config: dict, postamble_voice_stem: str,
                               sfx_dir: str = "SFX") -> None:
    _generate_voice_block(cast_cfg.postamble, cast_cfg, config,
                          postamble_voice_stem, "POSTAMBLE", sfx_dir)


def main() -> None:
    """CLI entry point for voice stem generation.

    Loads the parsed script and cast config, then generates MP3 stems
    via the ElevenLabs TTS API. Use ``--dry-run`` to preview character
    costs before committing API quota. For audio assembly, run
    ``XILP003_the413_audio_assembly.py`` separately.
    """
    with run_banner():
        parser = argparse.ArgumentParser(
            description="THE 413 Voice Generation — generate voice stems via ElevenLabs"
        )
        parser.add_argument("--episode", required=True,
                            help="Episode tag (e.g. S01E01) — derives cast and SFX config paths")
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
        args = parser.parse_args()

        # Derive config paths from --episode
        cast_path = f"cast_the413_{args.episode}.json"
        sfx_path = f"sfx_the413_{args.episode}.json"

        # Always load cast_cfg for metadata (preamble, season_title, tag)
        with open(cast_path, "r", encoding="utf-8") as f:
            cast_data = json.load(f)
        cast_cfg = CastConfiguration(**cast_data)

        # Derive default --script path from cast config metadata
        if args.script is None:
            args.script = f"parsed/parsed_the413_{cast_cfg.tag}.json"

        config, dialogue_entries, tag = load_production(args.script, cast_path)
        stems_dir = os.path.join(STEMS_DIR, tag)

        if args.terse:
            dialogue_entries = [
                {**e, "text": truncate_to_words(e["text"])} for e in dialogue_entries
            ]

        # Load SFX config (always, for preamble music lookup)
        sfx_config_model = None
        sfx_config_data = None
        if os.path.exists(sfx_path):
            with open(sfx_path, "r", encoding="utf-8") as f:
                sfx_config_data = json.load(f)
            sfx_config_model = SfxConfiguration(**sfx_config_data)

        # Build direction_types filter from gen flags (--sfx-music is deprecated all-in-one)
        gen_sfx      = args.gen_sfx      or args.sfx_music
        gen_music    = args.gen_music    or args.sfx_music
        gen_ambience = args.gen_ambience or args.sfx_music
        sfx_entries = None
        if gen_sfx or gen_music or gen_ambience:
            direction_types: set[str] = set()
            if gen_sfx:      direction_types |= {"SFX", "BEAT"}
            if gen_music:    direction_types.add("MUSIC")
            if gen_ambience: direction_types.add("AMBIENCE")
            sfx_entries = load_sfx_entries(args.script, sfx_path,
                                           direction_types=direction_types)
            # Pre-filter SFX entries to the requested range
            if args.stop_at is not None:
                sfx_entries = [e for e in sfx_entries if e["seq"] <= args.stop_at]

        # --- Preamble ---
        speaker = cast_cfg.preamble.speaker if cast_cfg.preamble else "tina"
        preamble_voice_stem = os.path.join(stems_dir, f"n002_preamble_{speaker}.mp3")
        preamble_music_stem = os.path.join(stems_dir, "n001_preamble_sfx.mp3")

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
            with open(args.script, "r", encoding="utf-8") as f:
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
            check_elevenlabs_quota()
            if cast_cfg.preamble:
                os.makedirs(stems_dir, exist_ok=True)
                _generate_preamble_voice(cast_cfg, config, preamble_voice_stem)
                # Copy intro music from sfx config 'INTRO MUSIC' source
                if not os.path.exists(preamble_music_stem):
                    if sfx_config_model and "INTRO MUSIC" in sfx_config_model.effects:
                        intro_entry = sfx_config_model.effects["INTRO MUSIC"]
                        if intro_entry.source:
                            clip = AudioSegment.from_file(intro_entry.source)
                            if intro_entry.play_duration is not None:
                                trim_ms = int(len(clip) * intro_entry.play_duration / 100.0)
                                clip = clip[:trim_ms]
                                print(f"   Trimmed intro music to {trim_ms/1000:.1f}s ({intro_entry.play_duration}%)")
                            clip.export(preamble_music_stem, format="mp3")
                            print(f"   Saved: {preamble_music_stem}")
                        else:
                            print(" [!] INTRO MUSIC entry has no 'source' — skipping music stem")
                    else:
                        print(" [!] No 'INTRO MUSIC' entry in sfx config — skipping music stem")
            generate_voices(config, dialogue_entries, stems_dir,
                            start_from=args.start_from, stop_at=args.stop_at)
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
                # Copy outro music from sfx config 'OUTRO MUSIC' source (optional)
                if not os.path.exists(postamble_music_stem):
                    if sfx_config_model and "OUTRO MUSIC" in sfx_config_model.effects:
                        outro_entry = sfx_config_model.effects["OUTRO MUSIC"]
                        if outro_entry.source:
                            clip = AudioSegment.from_file(outro_entry.source)
                            if outro_entry.play_duration is not None:
                                trim_ms = int(len(clip) * outro_entry.play_duration / 100.0)
                                clip = clip[:trim_ms]
                                print(f"   Trimmed outro music to {trim_ms/1000:.1f}s ({outro_entry.play_duration}%)")
                            clip.export(postamble_music_stem, format="mp3")
                            print(f"   Saved: {postamble_music_stem}")
                        else:
                            print(" [!] OUTRO MUSIC entry has no 'source' — skipping outro music stem")
                _generate_postamble_voice(cast_cfg, config, postamble_voice_stem)


if __name__ == "__main__":
    main()
