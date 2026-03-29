# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Discover and inspect personally generated Sound Effects.

Two data sources are supported:

1. **ElevenLabs account history** (``--api``, default when accessible):
   Calls ``GET /v1/sound-generation/history`` directly.  Requires the API
   key to have Sound Effects access.  If your key is missing it, go to
   ElevenLabs → Profile → API Keys → edit your key → Endpoints →
   **Sound Effects → Access**.

2. **Local SFX library** (``--local``, default fallback):
   Scans the ``SFX/`` directory and reads ID3/mutagen metadata — prompt text
   stored in the USLT lyrics tag, duration, file size, bit-rate.  Works
   with no API key required.

Usage::

    python XILU005_discover_SFX.py                  # local scan (default)
    python XILU005_discover_SFX.py --api             # attempt API (endpoint may not be public)
    python XILU005_discover_SFX.py --local           # explicit local scan
    python XILU005_discover_SFX.py --search "phone"
    python XILU005_discover_SFX.py --verbose
    python XILU005_discover_SFX.py --json
    python XILU005_discover_SFX.py --sfx-dir SFX/   # override local scan directory
"""

import argparse
import datetime
import json as _json
import os
import shutil

import httpx
from mutagen.id3 import ID3
from mutagen.mp3 import MP3

from xil_pipeline.sfx_common import run_banner

SFX_DIR = "SFX"
ELEVENLABS_BASE = "https://api.elevenlabs.io"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _fmt_unix(ts: int | None) -> str:
    """Format a Unix timestamp as YYYY-MM-DD HH:MM UTC, or '' if None."""
    if ts is None:
        return ""
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%d %H:%M")


def _fmt_duration(seconds: float | None) -> str:
    """Format a duration in seconds as 'N.Ns', or '' if None."""
    if seconds is None:
        return ""
    return f"{seconds:.1f}s"


def _fmt_size(bytes_: int) -> str:
    """Format a byte count as KB."""
    return f"{bytes_ / 1024:.0f} KB"


# ---------------------------------------------------------------------------
# Local SFX directory scan
# ---------------------------------------------------------------------------

def _read_local_record(path: str) -> dict:
    """Extract metadata from a locally generated SFX MP3 file.

    Reads ID3 tags written by ``tag_mp3()`` in ``sfx_common.py``:
    - TIT2  → title
    - TPE1  → artist
    - USLT  → generation prompt (stored as lyrics)
    Duration and bitrate come from mutagen audio headers.
    """
    filename = os.path.basename(path)
    size_bytes = os.path.getsize(path)

    prompt = ""
    title = ""
    artist = ""
    duration_s = None
    bitrate_kbps = None

    try:
        tags = ID3(path)
        if "TIT2" in tags:
            title = str(tags["TIT2"])
        if "TPE1" in tags:
            artist = str(tags["TPE1"])
        # Prompt is stored as unsynchronised lyrics
        for key in tags.keys():
            if key.startswith("USLT"):
                prompt = str(tags[key])
                break
    except Exception:
        pass

    try:
        audio = MP3(path)
        duration_s = audio.info.length
        bitrate_kbps = audio.info.bitrate // 1000
    except Exception:
        pass

    return {
        "source":       "local",
        "filename":     filename,
        "path":         path,
        "prompt":       prompt,
        "title":        title,
        "artist":       artist,
        "duration_seconds": round(duration_s, 1) if duration_s is not None else None,
        "bitrate_kbps": bitrate_kbps,
        "size_bytes":   size_bytes,
        "date":         "",
    }


def fetch_local_records(sfx_dir: str) -> list[dict]:
    """Scan *sfx_dir* and return one record per ``.mp3`` file."""
    if not os.path.isdir(sfx_dir):
        print(f"[!] SFX directory not found: {sfx_dir}")
        return []

    records = []
    for fname in sorted(os.listdir(sfx_dir)):
        if not fname.lower().endswith(".mp3"):
            continue
        records.append(_read_local_record(os.path.join(sfx_dir, fname)))
    return records


# ---------------------------------------------------------------------------
# ElevenLabs API: /v1/sound-generation/history
# ---------------------------------------------------------------------------

def fetch_api_records(api_key: str, max_items: int | None = None) -> list[dict]:
    """Fetch sound-generation history from the ElevenLabs API.

    Paginates until all items are retrieved or *max_items* is reached.

    Args:
        api_key: ElevenLabs API key with ``sound_generation`` permission.
        max_items: Cap on total records returned.  ``None`` = no limit.

    Returns:
        List of record dicts.

    Raises:
        SystemExit: When the API key is missing the required permission.
    """
    headers = {"xi-api-key": api_key, "accept": "application/json"}
    records: list[dict] = []
    start_after: str | None = None
    page_size = 100

    while True:
        params: dict = {"page_size": page_size}
        if start_after:
            params["start_after_history_item_id"] = start_after

        resp = httpx.get(
            f"{ELEVENLABS_BASE}/v1/sound-generation/history",
            headers=headers,
            params=params,
            timeout=15,
        )

        if resp.status_code == 401:
            body = resp.json()
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
            status = detail.get("status", "") if isinstance(detail, dict) else ""
            print("[!] ElevenLabs API: permission denied for sound-generation history.")
            print()
            if status in ("missing_permissions", "needs_authorization"):
                print("    Fix: ElevenLabs dashboard → Profile → API Keys")
                print("    Edit your key → Endpoints → Sound Effects → set to 'Access'")
                print("    then re-run without --api to fall back to local scan,")
                print("    or with --api once the permission is active.")
            else:
                print(f"    Response: {resp.text[:200]}")
            raise SystemExit(1)

        resp.raise_for_status()
        data = resp.json()

        # Normalise the response — ElevenLabs may use 'history' or 'generations'
        items = data.get("history") or data.get("generations") or data.get("items") or []

        for item in items:
            cfg = item.get("generation_config") or item.get("settings") or {}
            ts = item.get("date_unix") or item.get("created_at_unix")
            char_from = item.get("character_count_change_from", 0)
            char_to = item.get("character_count_change_to", 0)

            records.append({
                "source":       "api",
                "history_item_id": item.get("history_item_id") or item.get("id", ""),
                "prompt":       item.get("text") or item.get("prompt", ""),
                "model_id":     item.get("model_id", ""),
                "date":         _fmt_unix(ts),
                "date_unix":    ts or 0,
                "duration_seconds": cfg.get("duration_seconds"),
                "prompt_influence": cfg.get("prompt_influence"),
                "credits_used": char_to - char_from,
                "filename":     "",
                "path":         "",
            })

            if max_items is not None and len(records) >= max_items:
                return records

        has_more = data.get("has_more", False)
        if not has_more:
            break
        start_after = data.get("last_history_item_id") or (items[-1].get("history_item_id") if items else None)
        if not start_after:
            break

    return records


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def print_verbose_local(rec: dict) -> None:
    """Print all fields for a local SFX record."""
    print(f"  File           : {rec['filename']}")
    prompt_display = rec["prompt"] if rec["prompt"] else "— (no prompt tag)"
    print(f"  Prompt         : {prompt_display}")
    if rec["title"]:
        print(f"  Title          : {rec['title']}")
    if rec["duration_seconds"] is not None:
        print(f"  Duration       : {_fmt_duration(rec['duration_seconds'])}")
    if rec["bitrate_kbps"] is not None:
        print(f"  Bitrate        : {rec['bitrate_kbps']} kbps")
    print(f"  Size           : {_fmt_size(rec['size_bytes'])}")
    print()


def print_compact_local(rec: dict) -> None:
    """Print a compact line for a local SFX record."""
    dur = _fmt_duration(rec["duration_seconds"])
    size = _fmt_size(rec["size_bytes"])
    prompt = rec["prompt"][:72] + "…" if len(rec["prompt"]) > 72 else rec["prompt"]
    meta = f"{dur:6s}  {size:8s}"
    print(f"  {rec['filename']:<38}  {meta}")
    if prompt:
        print(f"    {prompt}")


def print_verbose_api(rec: dict) -> None:
    """Print all fields for an API SFX record."""
    print(f"  Prompt         : {rec['prompt']}")
    print(f"  History ID     : {rec['history_item_id']}")
    if rec["model_id"]:
        print(f"  Model          : {rec['model_id']}")
    print(f"  Created        : {rec['date'] or '—'}")
    if rec["duration_seconds"] is not None:
        print(f"  Duration       : {_fmt_duration(rec['duration_seconds'])}")
    if rec["prompt_influence"] is not None:
        print(f"  Prompt infl.   : {rec['prompt_influence']}")
    print(f"  Credits used   : {rec['credits_used']}")
    print()


def print_compact_api(rec: dict) -> None:
    """Print a compact summary line for an API SFX record."""
    dur = _fmt_duration(rec["duration_seconds"]) if rec["duration_seconds"] else ""
    prompt = rec["prompt"][:72] + "…" if len(rec["prompt"]) > 72 else rec["prompt"]
    print(f"  {rec['date']}  {rec['history_item_id']}  {dur}")
    if prompt:
        print(f"    {prompt}")


# ---------------------------------------------------------------------------
# Export kit — markdown reference + JSON inventory for Claude projects
# ---------------------------------------------------------------------------

def export_kit(records: list[dict], output_dir: str = ".") -> tuple[str, str]:
    """Generate an SFX inventory JSON and copy the scriptwriter reference doc.

    Args:
        records: Local SFX records from ``fetch_local_records()``.
        output_dir: Directory to write output files into.

    Returns:
        Tuple of (json_path, markdown_path).
    """
    os.makedirs(output_dir, exist_ok=True)

    # Write JSON inventory
    json_path = os.path.join(output_dir, "sfx_inventory.json")
    with open(json_path, "w", encoding="utf-8") as f:
        _json.dump(records, f, indent=2)
        f.write("\n")

    # Copy the scriptwriter reference doc
    ref_src = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "claude-scriptwriter-reference.md")
    ref_src = os.path.normpath(ref_src)
    md_path = os.path.join(output_dir, "claude-scriptwriter-reference.md")
    if os.path.exists(ref_src):
        shutil.copy2(ref_src, md_path)
    else:
        # Fallback: check relative to CWD (for editable installs)
        cwd_ref = os.path.join("docs", "claude-scriptwriter-reference.md")
        if os.path.exists(cwd_ref):
            shutil.copy2(cwd_ref, md_path)
        else:
            print(f"  [!] Reference doc not found at {ref_src} or {cwd_ref}")
            md_path = ""

    return json_path, md_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI entry point for SFX discovery."""
    with run_banner():
        parser = argparse.ArgumentParser(
            description="Discover personally generated Sound Effects from the ElevenLabs account or local SFX/ directory"
        )
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--api",
            action="store_true",
            help="Query ElevenLabs /v1/sound-generation/history (requires sound_generation permission)",
        )
        mode.add_argument(
            "--local",
            action="store_true",
            help="Scan local SFX/ directory only (no API key needed)",
        )
        parser.add_argument(
            "--sfx-dir",
            default=SFX_DIR,
            metavar="DIR",
            help=f"Local SFX directory to scan (default: {SFX_DIR}/)",
        )
        parser.add_argument(
            "--search",
            metavar="TEXT",
            help="Case-insensitive substring filter on the prompt/filename",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="(API mode) Paginate through the full account history; default: most recent 100",
        )
        parser.add_argument(
            "--verbose", "-v",
            action="store_true",
            help="Print all fields for each record",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output results as a JSON array",
        )
        parser.add_argument(
            "--export-kit",
            metavar="DIR",
            nargs="?",
            const=".",
            default=None,
            help="Export SFX inventory JSON + scriptwriter reference doc to DIR (default: current directory)",
        )
        args = parser.parse_args()

        api_key = os.environ.get("ELEVENLABS_API_KEY", "")
        # Default to local scan — the /v1/sound-generation/history endpoint appears
        # to be internal-only and is not accessible via public API keys regardless
        # of permission settings.  Pass --api explicitly to attempt it anyway.
        use_api = args.api

        if use_api and not api_key:
            print("[!] ELEVENLABS_API_KEY not set.")
            raise SystemExit(1)

        # --- Fetch records ---
        if use_api:
            max_items = None if args.all else 100
            try:
                records = fetch_api_records(api_key, max_items=max_items)
                data_source = "API"
            except SystemExit:
                if args.api:
                    raise  # user explicitly asked for API — propagate the error
                print()
                print("  Falling back to local SFX/ directory scan.")
                print()
                records = fetch_local_records(args.sfx_dir)
                data_source = f"local ({args.sfx_dir}/)"
        else:
            records = fetch_local_records(args.sfx_dir)
            data_source = f"local ({args.sfx_dir}/)"

        # --- Search filter ---
        if args.search:
            q = args.search.lower()
            records = [
                r for r in records
                if q in r.get("prompt", "").lower()
                or q in r.get("filename", "").lower()
            ]

        # --- Sort ---
        if records and records[0].get("date_unix"):
            records.sort(key=lambda r: r.get("date_unix", 0), reverse=True)
        else:
            records.sort(key=lambda r: r.get("filename", "").lower())

        # --- Export kit ---
        if args.export_kit is not None:
            json_path, md_path = export_kit(records, args.export_kit)
            print(f"\n--- Export kit ({len(records)} assets) ---\n")
            print(f"  JSON inventory : {json_path}")
            if md_path:
                print(f"  Reference doc  : {md_path}")
            print()
            print("  Attach both files to your Claude project as knowledge files.")
            return

        # --- Output ---
        if args.json:
            print(_json.dumps(records, indent=2))
            return

        print(f"\n--- ElevenLabs Sound Effects  [{data_source}]  ({len(records)} items) ---\n")

        if not records:
            print("  No sound-effect records found.")
            if args.search:
                print(f"  (search filter: {args.search!r})")
            return

        if args.verbose:
            for rec in records:
                if rec["source"] == "local":
                    print_verbose_local(rec)
                else:
                    print_verbose_api(rec)
        else:
            for rec in records:
                if rec["source"] == "local":
                    print_compact_local(rec)
                else:
                    print_compact_api(rec)
            print()

            if data_source.startswith("local"):
                total_size = sum(r.get("size_bytes", 0) for r in records)
                print(f"  Total size: {total_size / (1024*1024):.1f} MB  ({len(records)} files)")
            else:
                total_credits = sum(r.get("credits_used", 0) for r in records)
                print(f"  Total credits used: {total_credits:,}")

            print()
            print("  Use --verbose for full details, --json for machine-readable output,")
            print("  --search <text> to filter, --local / --api to select data source.")


if __name__ == "__main__":
    main()
