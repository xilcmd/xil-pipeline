"""Shared SFX library utilities.

Provides common functions for managing a shared SFX asset library and
generating episode-specific stems.  Both ``XILU002_generate_SFX.py`` and
``XILP002_producer.py`` delegate to this module to avoid code
duplication and to ensure that each unique sound effect is generated only
once into the shared ``SFX/`` directory.

Module Attributes:
    SFX_DIR: Default path for the shared SFX asset library.
"""

import contextlib
import datetime
import json
import os
import re
import shutil
import sys
import time

from mutagen.id3 import ID3, TALB, TCON, TDRC, TIT2, TPE1, USLT
from mutagen.wave import WAVE
from pydub import AudioSegment

from xil_pipeline.models import SfxConfiguration, SfxEntry

SFX_DIR = "SFX"

_BAR = "=" * 70


@contextlib.contextmanager
def run_banner(script_name: str | None = None):
    """Context manager that prints a start header and end trailer.

    Usage::

        def main():
            with run_banner():
                ...  # all application logic

    Args:
        script_name: Override the script name shown in the banner.
                     Defaults to ``os.path.basename(sys.argv[0])``.
    """
    name = script_name or os.path.basename(sys.argv[0])
    start = datetime.datetime.now()
    print(f"\n{_BAR}")
    print(f"  {name}  |  started {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{_BAR}\n")
    try:
        yield
    finally:
        end = datetime.datetime.now()
        elapsed = end - start
        print(f"\n{_BAR}")
        print(f"  {name}  |  finished {end.strftime('%Y-%m-%d %H:%M:%S')}  ({elapsed.total_seconds():.1f}s)")
        print(f"{_BAR}\n")


def slugify_effect_key(text: str) -> str:
    """Convert direction text to a filesystem-safe slug.

    Rules:
        1. Lowercase the entire string.
        2. Replace ``': '`` (colon-space) with ``'_'`` (category separator).
        3. Replace remaining non-alphanumeric characters with ``'-'``.
        4. Collapse multiple consecutive hyphens.
        5. Strip leading/trailing hyphens.

    Examples:
        >>> slugify_effect_key("BEAT")
        'beat'
        >>> slugify_effect_key("SFX: DOOR OPENS, BELL CHIMES")
        'sfx_door-opens-bell-chimes'
    """
    if not text:
        return ""
    slug = text.lower()
    slug = slug.replace(": ", "_")
    slug = re.sub(r"[^a-z0-9_]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    slug = slug.strip("-")
    return slug


def shared_sfx_path(sfx_dir: str, effect_key: str) -> str:
    """Return the shared library file path for an effect key.

    Args:
        sfx_dir: Base directory for shared SFX assets.
        effect_key: Direction text key (e.g. ``'BEAT'``).

    Returns:
        Full path like ``SFX/beat.mp3``.
    """
    return os.path.join(sfx_dir, f"{slugify_effect_key(effect_key)}.mp3")


def tag_mp3(
    path: str,
    show: str = "THE 413",
    title: str | None = None,
    artist: str | None = None,
    lyrics: str | None = None,
) -> None:
    """Write ID3 metadata tags to an MP3 file.

    Sets Album, Genre, and Year.  Optionally sets Title, Artist, and
    Lyrics tags.

    Args:
        path: Path to the MP3 file.
        show: Album name (default ``"THE 413"``).
        title: Optional TIT2 title tag (e.g. the effect key or dialogue
            song label).
        artist: Optional TPE1 artist tag (e.g. the speaker's full name).
        lyrics: Optional USLT unsynchronised lyrics tag (full dialogue
            text).
    """
    try:
        tags = ID3(path)
    except Exception:
        tags = ID3()

    tags.add(TALB(encoding=3, text=show))
    tags.add(TCON(encoding=3, text="Podcast"))
    tags.add(TDRC(encoding=3, text=str(datetime.date.today().year)))
    if title:
        tags.add(TIT2(encoding=3, text=title))
    if artist:
        tags.add(TPE1(encoding=3, text=artist))
    if lyrics:
        tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
    tags.save(path)


def tag_wav(
    path: str,
    show: str = "THE 413",
    title: str | None = None,
    artist: str | None = None,
) -> None:
    """Write ID3 metadata tags to a WAV file.

    Sets Album, Genre, and Year.  Optionally sets Title and Artist.

    Args:
        path: Path to the WAV file.
        show: Album name (default ``"THE 413"``).
        title: Optional TIT2 title tag (e.g. the layer name).
        artist: Optional TPE1 artist tag.
    """
    wav = WAVE(path)
    if wav.tags is None:
        wav.add_tags()
    wav.tags.add(TALB(encoding=3, text=show))
    wav.tags.add(TCON(encoding=3, text="Podcast"))
    wav.tags.add(TDRC(encoding=3, text=str(datetime.date.today().year)))
    if title:
        wav.tags.add(TIT2(encoding=3, text=title))
    if artist:
        wav.tags.add(TPE1(encoding=3, text=artist))
    wav.save()


def ensure_shared_sfx(
    effect_key: str,
    effect: SfxEntry,
    sfx_dir: str,
    defaults: dict,
    client=None,
    show: str = "THE 413",
) -> str:
    """Ensure the shared SFX asset exists, generating if needed.

    For ``type='silence'`` effects, generates silent audio locally via
    pydub.  For ``type='sfx'`` effects, calls the ElevenLabs Sound
    Effects API via the provided *client*.  In both cases, ID3 metadata
    tags (Album, Genre, Year, Title) are written to the resulting MP3.

    Args:
        effect_key: Direction text key.
        effect: The ``SfxEntry`` model instance.
        sfx_dir: Shared SFX library directory.
        defaults: Config-level defaults (e.g. ``prompt_influence``).
        client: ElevenLabs client instance.  Required for ``type='sfx'``
            effects; may be ``None`` for silence-only generation.
        show: Show name for the Album ID3 tag.

    Returns:
        The path to the shared asset file.

    Raises:
        ValueError: If *client* is ``None`` and the effect requires API
            generation.
    """
    path = shared_sfx_path(sfx_dir, effect_key)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path

    os.makedirs(sfx_dir, exist_ok=True)

    if effect.source is not None:
        shutil.copy2(effect.source, path)
    elif effect.type == "silence":
        duration_ms = int(effect.duration_seconds * 1000)
        silence = AudioSegment.silent(duration=duration_ms)
        silence.export(path, format="mp3")
    else:
        if client is None:
            raise ValueError(
                f"client is required to generate SFX for '{effect_key}'"
            )
        prompt_influence = effect.prompt_influence
        if prompt_influence is None:
            prompt_influence = defaults.get("prompt_influence", 0.3)

        tmp_path = path + ".tmp"
        max_retries, delay = 5, 10
        for attempt in range(1, max_retries + 1):
            try:
                audio_stream = client.text_to_sound_effects.convert(
                    text=effect.prompt,
                    duration_seconds=effect.duration_seconds,
                    prompt_influence=prompt_influence,
                )
                with open(tmp_path, "wb") as f:
                    for chunk in audio_stream:
                        if chunk:
                            f.write(chunk)
                os.rename(tmp_path, path)
                break
            except Exception as exc:
                is_rate_limit = (
                    hasattr(exc, "status_code") and exc.status_code == 429
                )
                if is_rate_limit and attempt < max_retries:
                    wait = delay * attempt
                    print(f"   [429] Rate limited — retrying in {wait}s "
                          f"(attempt {attempt}/{max_retries})")
                    time.sleep(wait)
                else:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                    raise

    tag_mp3(path, show=show, title=effect_key)

    return path


def place_episode_stem(shared_path: str, stem_path: str) -> bool:
    """Copy a shared SFX asset to an episode stem location.

    Args:
        shared_path: Path to the shared asset in ``SFX/``.
        stem_path: Destination path in ``stems/<TAG>/``.

    Returns:
        ``True`` if the file was copied, ``False`` if the stem already
        existed on disk.
    """
    if os.path.exists(stem_path) and os.path.getsize(stem_path) > 0:
        return False
    os.makedirs(os.path.dirname(stem_path), exist_ok=True)
    shutil.copy2(shared_path, stem_path)
    return True


def load_sfx_entries(
    script_json_path: str,
    sfx_json_path: str,
    max_duration: float | None = None,
    direction_types: set[str] | None = None,
) -> list[dict]:
    """Load direction entries matched against an SFX configuration.

    Reads the parsed script and SFX config, returning only direction
    entries whose ``text`` field has a matching key in the SFX effects
    mapping.

    Args:
        script_json_path: Path to the parsed script JSON.
        sfx_json_path: Path to the SFX configuration JSON.
        max_duration: If set, exclude effects with ``duration_seconds``
            exceeding this value.
        direction_types: If set, only include entries whose
            ``direction_type`` is in this set (e.g. ``{"SFX", "BEAT"}``).
            ``None`` includes all categories.

    Returns:
        A list of SFX entry dicts with ``seq``, ``text``, ``direction_type``,
        ``stem_name``, ``sfx_type``, ``section``, and ``scene``.
    """
    with open(script_json_path, encoding="utf-8") as f:
        script_data = json.load(f)
    with open(sfx_json_path, encoding="utf-8") as f:
        sfx_data = json.load(f)

    sfx_cfg = SfxConfiguration(**sfx_data)

    sfx_entries: list[dict] = []
    for entry in script_data["entries"]:
        if entry["type"] != "direction":
            continue
        if direction_types is not None and entry.get("direction_type") not in direction_types:
            continue
        effect = sfx_cfg.effects.get(entry["text"])
        if effect is None:
            continue
        if effect.duration_seconds == 0.0:
            continue  # stop markers (FADES OUT / AMBIENCE: STOP) — no stem needed
        if max_duration is not None and effect.duration_seconds > max_duration:
            continue

        seq = entry["seq"]
        if seq < 0:
            stem_name = f"n{abs(seq):03d}_{entry['section']}_sfx"
        else:
            stem_name = f"{seq:03d}_{entry['section']}"
            if entry.get("scene"):
                stem_name += f"-{entry['scene']}"
            stem_name += "_sfx"

        sfx_entries.append({
            "seq": entry["seq"],
            "text": entry["text"],
            "direction_type": entry.get("direction_type"),
            "stem_name": stem_name,
            "sfx_type": effect.type,
            "section": entry["section"],
            "scene": entry.get("scene"),
        })

    return sfx_entries


def generate_sfx(
    sfx_entries: list[dict],
    sfx_config: dict,
    stems_dir: str,
    sfx_dir: str = SFX_DIR,
    client=None,
    start_from: int = 1,
) -> None:
    """Generate SFX stems via a two-phase shared-library workflow.

    **Phase 1** — For each unique effect key, ensure the shared asset
    exists in *sfx_dir* (generate via API or silence if missing).

    **Phase 2** — For each script entry, copy the shared asset to the
    episode stems directory with the sequence-numbered filename.

    Args:
        sfx_entries: SFX entry dicts from :func:`load_sfx_entries`.
        sfx_config: Raw SFX config dict.
        stems_dir: Episode stems output directory.
        sfx_dir: Shared SFX library directory.
        client: ElevenLabs client (needed for API effects).
        start_from: Only process entries with ``seq >= start_from``.
    """
    os.makedirs(stems_dir, exist_ok=True)
    sfx_cfg = SfxConfiguration(**sfx_config)
    defaults = sfx_cfg.defaults

    entries_to_process = [e for e in sfx_entries if e["seq"] >= start_from]
    print(f"--- SFX: Processing {len(entries_to_process)} entries ---")

    # Phase 1: ensure shared assets for unique effect keys
    unique_keys = dict.fromkeys(e["text"] for e in entries_to_process)
    shared_paths: dict[str, str] = {}
    for key in unique_keys:
        effect = sfx_cfg.effects[key]
        path = ensure_shared_sfx(key, effect, sfx_dir, defaults, client,
                                show=sfx_cfg.show)
        shared_paths[key] = path
        print(f"   Shared: {path}")

    # Phase 2: place episode stems
    copied_count = 0
    skipped_count = 0
    for entry in entries_to_process:
        stem_file = os.path.join(stems_dir, f"{entry['stem_name']}.mp3")
        shared_path = shared_paths[entry["text"]]
        if place_episode_stem(shared_path, stem_file):
            print(f"   Placed: {stem_file}")
            copied_count += 1
        else:
            print(f"   Exists: {stem_file} — skipping")
            skipped_count += 1

    print(
        f"--- SFX Complete: {len(unique_keys)} shared assets, "
        f"{copied_count} placed, {skipped_count} skipped ---"
    )


def dry_run_sfx(
    sfx_entries: list[dict],
    sfx_config: dict,
    stems_dir: str,
    sfx_dir: str = SFX_DIR,
) -> None:
    """Preview SFX generation showing status and credit estimates.

    Each entry is classified as one of:
    - **EXISTS** — episode stem already in ``stems/<TAG>/``
    - **CACHED** — shared asset in ``SFX/``, will be copied (no API)
    - **NEW** — needs API generation to ``SFX/``, then copy

    Args:
        sfx_entries: SFX entry dicts from :func:`load_sfx_entries`.
        sfx_config: Raw SFX config dict.
        stems_dir: Episode stems directory.
        sfx_dir: Shared SFX library directory.
    """
    sfx_cfg = SfxConfiguration(**sfx_config)

    print(f"\n{'='*70}")
    print(f"SFX DRY RUN — {len(sfx_entries)} entries")
    print(f"  stems dir: {stems_dir}")
    print(f"  shared dir: {sfx_dir}")
    print(f"{'='*70}\n")

    # Per-category accumulators: keys are direction_type buckets + "silence"
    buckets: dict[str, dict] = {
        "MUSIC":    {"new": 0, "dur": 0.0},
        "AMBIENCE": {"new": 0, "dur": 0.0},
        "SFX":      {"new": 0, "dur": 0.0},
        "silence":  {"new": 0, "dur": 0.0},
    }
    new_count = 0
    cached_count = 0
    exists_count = 0

    for entry in sfx_entries:
        effect = sfx_cfg.effects.get(entry["text"])
        if effect is None:
            continue

        stem_file = os.path.join(stems_dir, f"{entry['stem_name']}.mp3")
        is_source = effect.source is not None
        shared_file = effect.source if is_source else shared_sfx_path(sfx_dir, entry["text"])

        if os.path.exists(stem_file):
            status = "EXISTS"
            exists_count += 1
        elif os.path.exists(shared_file):
            status = "CACHED"
            cached_count += 1
        else:
            status = "   NEW"
            new_count += 1

        seq_label = f"n{abs(entry['seq']):03d}" if entry["seq"] < 0 else f"{entry['seq']:03d}"
        if effect.type == "silence":
            print(
                f" [{status}] {seq_label} | silence "
                f"| {effect.duration_seconds:>5.1f}s | {entry['text']}"
            )
            if status == "   NEW":
                buckets["silence"]["new"] += 1
                buckets["silence"]["dur"] += effect.duration_seconds
        elif is_source:
            print(
                f" [{status}] {seq_label} | copy    "
                f"|         | ~    0 credits "
                f"| {entry['text']}"
            )
            print(f"            source: {shared_file}")
        else:
            credits = int(effect.duration_seconds * 40)
            bucket_key = entry.get("direction_type") or "SFX"
            if bucket_key not in buckets:
                bucket_key = "SFX"
            if status == "   NEW":
                buckets[bucket_key]["new"] += 1
                buckets[bucket_key]["dur"] += effect.duration_seconds
            print(
                f" [{status}] {seq_label} | sfx     "
                f"| {effect.duration_seconds:>5.1f}s | ~{credits:>5} credits "
                f"| {entry['text']}"
            )
            print(f"            prompt: {effect.prompt}")

        print(f"            stem: {entry['stem_name']}.mp3")
        if not is_source:
            print(f"            shared: {os.path.basename(shared_file)}")
        print()

    total_new_dur = sum(b["dur"] for b in buckets.values())
    total_credits = int(total_new_dur * 40)
    print(f"{'='*70}")
    print(
        f"SUMMARY: {len(sfx_entries)} total — "
        f"{new_count} new, {cached_count} cached, {exists_count} on disk"
    )
    for cat in ("MUSIC", "AMBIENCE", "SFX"):
        b = buckets[cat]
        if b["new"] or any(
            (entry.get("direction_type") or "SFX") == cat
            for entry in sfx_entries
        ):
            cred = int(b["dur"] * 40)
            print(f"  {cat:<9}: {b['new']:>3} new, {b['dur']:>6.1f}s, ~{cred:>6} credits")
    if buckets["silence"]["new"]:
        print(f"  {'silence':<9}: {buckets['silence']['new']:>3} new  (free)")
    print(
        f"  {'TOTAL NEW':<9}: {new_count:>3},  {total_new_dur:.1f}s, "
        f"~{total_credits} credits  (silence & cached are free)"
    )
    print(f"{'='*70}\n")
