# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Recursively hash all MP3 files under a directory and log filename : SHA-256.

Walks a directory tree, finds every *.mp3 file, and prints one line per file:

    <path> : <sha256hex>

Useful for verifying stem integrity before/after migrations, detecting duplicates
in the shared SFX library, and producing a manifest that can be diffed over time.

Usage:
    xil mp3-hash                          # scan current directory
    xil mp3-hash SFX/                     # scan SFX library
    xil mp3-hash stems/S03E01/            # scan one episode's stems
    xil mp3-hash stems/ --output hashes.txt
    xil mp3-hash SFX/ --json
    xil mp3-hash . --absolute
"""

import argparse
import hashlib
import json
import os

from xil_pipeline.log_config import configure_logging, get_logger
from xil_pipeline.sfx_common import run_banner

logger = get_logger(__name__)


def hash_file(path: str) -> str:
    """Return the hex-encoded SHA-256 digest of a file.

    Reads in 64 KiB chunks to keep memory use flat for large audio files.

    Args:
        path: Absolute or relative path to the file.

    Returns:
        64-character lowercase hex string.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_mp3s(root: str) -> list[tuple[str, str]]:
    """Recursively find *.mp3 files under *root* and return (abs_path, sha256) pairs.

    Files within each directory are yielded in sorted order so the output is
    deterministic across runs.

    Args:
        root: Directory to scan.

    Returns:
        List of ``(absolute_path, sha256_hex)`` tuples.
    """
    results = []
    for dirpath, _dirs, filenames in os.walk(root):
        for fname in sorted(filenames):
            if fname.lower().endswith(".mp3"):
                full = os.path.abspath(os.path.join(dirpath, fname))
                results.append((full, hash_file(full)))
    return results


def _run(args: "argparse.Namespace") -> None:
    """Execute the hash scan with parsed arguments."""
    quiet = args.json  # suppress logger.info when emitting machine-readable JSON

    root = os.path.abspath(args.path)

    if os.path.isfile(root):
        # Single-file mode: hash just this one file
        records = [(root, hash_file(root))]
    elif os.path.isdir(root):
        if not quiet:
            logger.info("Scanning %s for MP3 files…", root)
        records = scan_mp3s(root)
    else:
        logger.error("Not a file or directory: %s", root)
        return

    if not records:
        if not quiet:
            logger.info("No MP3 files found under %s", root)
        return

    # Build display paths
    display: list[tuple[str, str]] = []
    for abs_path, digest in records:
        if args.absolute:
            label = abs_path
        else:
            try:
                label = os.path.relpath(abs_path, root)
            except ValueError:
                label = abs_path  # different drive on Windows
        display.append((label, digest))

    if args.json:
        print(json.dumps([{"path": p, "sha256": d} for p, d in display], indent=2))
    else:
        for label, digest in display:
            logger.info("%s : %s", label, digest)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            for label, digest in display:
                f.write(f"{label} : {digest}\n")
        if not quiet:
            logger.info("Written: %s", args.output)

    if not quiet:
        logger.info("Hashed %d MP3 file(s) under %s", len(records), root)


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xil-mp3-hash",
        description="Recursively hash MP3 files and log <path> : <sha256>",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=".",
        help="File to hash or directory to scan recursively (default: current directory)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        metavar="FILE",
        help="Write results to FILE in addition to logging",
    )
    parser.add_argument(
        "--absolute",
        action="store_true",
        help="Print absolute paths (default: paths relative to scan root)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help='Output a JSON array of {"path": ..., "sha256": ...} to stdout (no banner)',
    )
    return parser


def main() -> None:
    """CLI entry point for recursive MP3 SHA-256 hashing."""
    args = get_parser().parse_args()

    # In --json mode skip configure_logging and run_banner so stdout
    # contains only valid JSON (safe to pipe directly to jq / python -m json.tool).
    if args.json:
        _run(args)
    else:
        configure_logging()
        with run_banner():
            _run(args)


if __name__ == "__main__":
    main()
