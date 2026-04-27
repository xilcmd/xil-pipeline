# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

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
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time

import httpx
from elevenlabs.core.api_error import ApiError
from mutagen.id3 import COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, USLT, ID3NoHeaderError
from mutagen.wave import WAVE
from pydub import AudioSegment

from xil_pipeline.log_config import get_logger
from xil_pipeline.models import SfxConfiguration, SfxEntry

logger = get_logger(__name__)

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


_MAX_SLUG_LEN = 180  # filesystem max is 255 bytes; leave room for .mp3 + collision suffix


def slugify_effect_key(text: str) -> str:
    """Convert direction text to a filesystem-safe slug.

    Rules:
        1. Lowercase the entire string.
        2. Replace ``': '`` (colon-space) with ``'_'`` (category separator).
        3. Replace remaining non-alphanumeric characters with ``'-'``.
        4. Collapse multiple consecutive hyphens.
        5. Strip leading/trailing hyphens.
        6. Truncate to ``_MAX_SLUG_LEN`` chars; append an 8-char SHA-256 suffix
           when truncated to avoid collisions between long similar keys.

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
    if len(slug) > _MAX_SLUG_LEN:
        h = hashlib.sha256(slug.encode()).hexdigest()[:8]
        slug = slug[:_MAX_SLUG_LEN].rstrip("-") + "_" + h
    return slug


def _sha256_file(path: str) -> str:
    """Return the hex-encoded SHA-256 digest of *path*, read in 64 KiB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def file_nonempty(path: str) -> bool:
    """Return True if *path* exists and has a non-zero size.

    Uses a single ``os.stat()`` call to avoid a TOCTOU race between
    an existence check and a separate size check.

    Args:
        path: Filesystem path to test.

    Returns:
        ``True`` if the file exists and ``st_size > 0``, ``False`` otherwise.
    """
    try:
        return os.stat(path).st_size > 0
    except OSError:
        return False


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
    show: str = "Sample Show",
    title: str | None = None,
    artist: str | None = None,
    lyrics: str | None = None,
    comments: str | None = None,
) -> None:
    """Write ID3 metadata tags to an MP3 file.

    Sets Album, Genre, and Year.  Optionally sets Title, Artist, and
    Lyrics tags.

    Args:
        path: Path to the MP3 file.
        show: Album name (default ``"Sample Show"``).
        title: Optional TIT2 title tag (e.g. the effect key or dialogue
            song label).
        artist: Optional TPE1 artist tag (e.g. the speaker's full name).
        lyrics: Optional USLT unsynchronised lyrics tag (full dialogue
            text).
    """
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
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
    if comments:
        tags.add(COMM(encoding=3, lang="eng", desc="", text=comments))
    tags.save(path)


def tag_wav(
    path: str,
    show: str = "Sample Show",
    title: str | None = None,
    artist: str | None = None,
) -> None:
    """Write ID3 metadata tags to a WAV file.

    Sets Album, Genre, and Year.  Optionally sets Title and Artist.

    Args:
        path: Path to the WAV file.
        show: Album name (default ``"Sample Show"``).
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
    show: str = "Sample Show",
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
    if file_nonempty(path):
        return path

    os.makedirs(sfx_dir, exist_ok=True)

    if effect.source is not None:
        # Resolve to a real path to prevent path-traversal via sfx config.
        src_real = os.path.realpath(effect.source)
        if os.path.isfile(src_real):
            shutil.copy2(src_real, path)
        else:
            # Source file declared but missing — fall back to API generation if a
            # prompt is available, otherwise raise an actionable error.
            if effect.prompt is None:
                raise FileNotFoundError(
                    f"Source file not found: '{effect.source}' "
                    f"(key: '{effect_key}'). "
                    "Add the file or add a 'prompt' field to generate it via the API."
                )
            print(
                f"   [warn] source '{effect.source}' not found — "
                f"generating via API for '{effect_key}'"
            )
            # fall through to API generation branch below
            if client is None:
                raise ValueError(
                    f"client is required to generate SFX for '{effect_key}'"
                )
            prompt_influence = effect.prompt_influence
            if prompt_influence is None:
                prompt_influence = defaults.get("prompt_influence", 0.3)
            tmp_path = None
            try:
                max_retries, delay = 5, 10
                for attempt in range(1, max_retries + 1):
                    try:
                        audio_stream = client.text_to_sound_effects.convert(
                            text=effect.prompt,
                            duration_seconds=effect.duration_seconds,
                            prompt_influence=prompt_influence,
                        )
                        tmp_fd, tmp_path = tempfile.mkstemp(
                            dir=os.path.dirname(path) or ".", suffix=".tmp"
                        )
                        with os.fdopen(tmp_fd, "wb") as f:
                            for chunk in audio_stream:
                                if chunk:
                                    f.write(chunk)
                        os.rename(tmp_path, path)
                        tmp_path = None
                        break
                    except (ApiError, httpx.TransportError) as exc:
                        if tmp_path is not None:
                            with contextlib.suppress(FileNotFoundError):
                                os.unlink(tmp_path)
                            tmp_path = None
                        is_rate_limit = isinstance(exc, ApiError) and exc.status_code == 429
                        if is_rate_limit and attempt < max_retries:
                            wait = delay * attempt
                            print(f"   [429] rate limited — retrying in {wait}s (attempt {attempt}/{max_retries})")
                            time.sleep(wait)
                        else:
                            raise
            finally:
                if tmp_path is not None and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            tag_mp3(path, show=show, title=effect_key)
            return path
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

        tmp_path = None
        try:
            max_retries, delay = 5, 10
            for attempt in range(1, max_retries + 1):
                try:
                    audio_stream = client.text_to_sound_effects.convert(
                        text=effect.prompt,
                        duration_seconds=effect.duration_seconds,
                        prompt_influence=prompt_influence,
                    )
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        dir=os.path.dirname(path) or ".", suffix=".tmp"
                    )
                    with os.fdopen(tmp_fd, "wb") as f:
                        for chunk in audio_stream:
                            if chunk:
                                f.write(chunk)
                    os.rename(tmp_path, path)
                    tmp_path = None
                    break
                except (ApiError, httpx.TransportError) as exc:
                    if tmp_path is not None:
                        with contextlib.suppress(FileNotFoundError):
                            os.unlink(tmp_path)
                        tmp_path = None
                    is_rate_limit = isinstance(exc, ApiError) and exc.status_code == 429
                    is_server_error = (
                        isinstance(exc, ApiError)
                        and exc.status_code is not None
                        and exc.status_code >= 500
                    )
                    is_network_error = isinstance(exc, httpx.TransportError)
                    is_retryable = is_rate_limit or is_server_error or is_network_error
                    if is_retryable and attempt < max_retries:
                        wait = delay * attempt
                        if is_rate_limit:
                            reason = "429 rate limited"
                        elif is_server_error:
                            reason = f"{exc.status_code} server error"
                        else:
                            reason = f"network error ({type(exc).__name__})"
                        logger.warning("[%s] — retrying in %ds (attempt %d/%d)",
                                       reason, wait, attempt, max_retries)
                        time.sleep(wait)
                    else:
                        raise
        finally:
            if tmp_path is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_path)

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
    if file_nonempty(stem_path):
        return False
    os.makedirs(os.path.dirname(stem_path), exist_ok=True)
    shutil.copy2(shared_path, stem_path)
    return True


def load_sfx_entries(
    script_json_path: str,
    sfx_json_path: str,
    max_duration: float | None = None,
    direction_types: set[str] | None = None,
    local_only: bool = False,
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
        local_only: If ``True``, skip effects that would require an API
            call — i.e. ``type == "sfx"``, no ``source`` file, and not
            already present in the shared ``SFX/`` directory.  Silence
            entries and source-backed entries are always included.

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
        if local_only and effect.type == "sfx" and effect.source is None:
            if not file_nonempty(shared_sfx_path(SFX_DIR, entry["text"])):
                logger.debug("--local-only: skipping %r (not in SFX/)", entry["text"])
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
    logger.info("--- SFX: Processing %d entries ---", len(entries_to_process))

    # Phase 1: ensure shared assets for unique effect keys
    unique_keys = dict.fromkeys(e["text"] for e in entries_to_process)
    shared_paths: dict[str, str] = {}
    for key in unique_keys:
        effect = sfx_cfg.effects[key]
        path = ensure_shared_sfx(key, effect, sfx_dir, defaults, client,
                                show=sfx_cfg.show)
        shared_paths[key] = path
        logger.info("   Shared: %s", path)

    # Phase 2: place episode stems
    copied_count = 0
    skipped_count = 0
    for entry in entries_to_process:
        stem_file = os.path.join(stems_dir, f"{entry['stem_name']}.mp3")
        shared_path = shared_paths[entry["text"]]
        if place_episode_stem(shared_path, stem_file):
            logger.info("   Placed: %s", stem_file)
            logger.info("   SHA256: %s", _sha256_file(stem_file))
            copied_count += 1
        else:
            logger.info("   Exists: %s — skipping", stem_file)
            skipped_count += 1

    logger.info(
        "--- SFX Complete: %d shared assets, %d placed, %d skipped ---",
        len(unique_keys), copied_count, skipped_count,
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

    logger.info("\n%s", "=" * 70)
    logger.info("SFX DRY RUN — %d entries", len(sfx_entries))
    logger.info("  stems dir: %s", stems_dir)
    logger.info("  shared dir: %s", sfx_dir)
    logger.info("%s\n", "=" * 70)

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
    missing_count = 0
    missing_sources: list[str] = []

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
        elif is_source and not os.path.exists(shared_file):
            status = "MISSING"
            missing_count += 1
            missing_sources.append(f"  '{entry['text']}' → {shared_file}")
        elif os.path.exists(shared_file):
            status = "CACHED"
            cached_count += 1
        else:
            status = "   NEW"
            new_count += 1

        seq_label = f"n{abs(entry['seq']):03d}" if entry["seq"] < 0 else f"{entry['seq']:03d}"
        if effect.type == "silence":
            logger.info(
                " [%s] %s | silence | %5.1fs | %s",
                status, seq_label, effect.duration_seconds, entry["text"],
            )
            if status == "   NEW":
                buckets["silence"]["new"] += 1
                buckets["silence"]["dur"] += effect.duration_seconds
        elif is_source:
            logger.info(
                " [%s] %s | copy    |         | ~    0 credits | %s",
                status, seq_label, entry["text"],
            )
            logger.info("            source: %s", shared_file)
        else:
            credits = int(effect.duration_seconds * 40)
            bucket_key = entry.get("direction_type") or "SFX"
            if bucket_key not in buckets:
                bucket_key = "SFX"
            if status == "   NEW":
                buckets[bucket_key]["new"] += 1
                buckets[bucket_key]["dur"] += effect.duration_seconds
            logger.info(
                " [%s] %s | sfx     | %5.1fs | ~%5d credits | %s",
                status, seq_label, effect.duration_seconds, credits, entry["text"],
            )
            logger.info("            prompt: %s", effect.prompt)

        logger.info("            stem: %s.mp3", entry["stem_name"])
        if not is_source:
            logger.info("            shared: %s", os.path.basename(shared_file))
        logger.info("")

    total_new_dur = sum(b["dur"] for b in buckets.values())
    total_credits = int(total_new_dur * 40)
    logger.info("%s", "=" * 70)
    logger.info(
        "SUMMARY: %d total — %d new, %d cached, %d on disk, %d MISSING",
        len(sfx_entries), new_count, cached_count, exists_count, missing_count,
    )
    for cat in ("MUSIC", "AMBIENCE", "SFX"):
        b = buckets[cat]
        if b["new"] or any(
            (entry.get("direction_type") or "SFX") == cat
            for entry in sfx_entries
        ):
            cred = int(b["dur"] * 40)
            logger.info("  %-9s: %3d new, %6.1fs, ~%6d credits", cat, b["new"], b["dur"], cred)
    if buckets["silence"]["new"]:
        logger.info("  %-9s: %3d new  (free)", "silence", buckets["silence"]["new"])
    logger.info(
        "  %-9s: %3d,  %.1fs, ~%d credits  (silence & cached are free)",
        "TOTAL NEW", new_count, total_new_dur, total_credits,
    )
    if missing_sources:
        logger.error("%d source file(s) declared but not found:", len(missing_sources))
        for msg in missing_sources:
            logger.error(msg)
    logger.info("%s\n", "=" * 70)
