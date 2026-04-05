# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated, show-agnostic podcast/audio production pipeline using ElevenLabs TTS API. Turns a markdown production script into a podcast-ready MP3.

## Package Structure

The project is packaged as `xil-pipeline` (import name `xil_pipeline`) using hatchling. All pipeline and utility scripts live under `src/xil_pipeline/`:

```
src/xil_pipeline/          # Python package (24 modules)
  __init__.py              # version + key re-exports
  xil.py                   # Unified `xil` command dispatcher
  models.py                # Pydantic data models, slug/path resolution
  mix_common.py            # Shared mixing utilities
  sfx_common.py            # SFX library management, ID3 tagging
  timeline_viz.py          # Timeline visualization
  xil_init.py              # Project scaffolding (xil-init command)
  XILP000_*.py … XILP011_*.py   # Pipeline stages
  XILU001_*.py … XILU006_*.py   # Utility scripts
tests/                     # Pytest test suite
docs/                      # MkDocs documentation
pyproject.toml             # Packaging config (hatchling)
project.json               # Show name config (runtime, read from CWD)
speakers.json              # Speaker definitions (optional, overrides built-in defaults)
cast_*.json, sfx_*.json    # Episode configs (workspace data, stays at root)
```

Install for development: `pip install -e ".[all,dev]"`

All internal imports use the package namespace: `from xil_pipeline.models import ...`

## Environment

- Python 3.12+, virtualenv at `venv/`
- WSL2 (Linux on Windows)
- Activate: `source venv/bin/activate`
- Install: `pip install -e ".[all,dev]"` (editable install with all optional deps)
- Core packages: `elevenlabs`, `pydub`, `pydantic`, `mutagen`, `httpx`
- Optional: `google-genai`, `gTTS`, `pyttsx3`, `ollama`
- ElevenLabs API key via `ELEVENLABS_API_KEY` env var
- Audio playback via `mpg123` in WSL

## Project Configuration

`project.json` at the repo root declares the show name and optional season title used across the pipeline:
```json
{
    "show": "THE 413",
    "season_title": "The Holiday Shift"
}
```

All scripts accept a `--show` CLI flag to override the show name. Resolution order: `--show` arg > `project.json` > hardcoded fallback `"sample"`.

The `season_title` key in `project.json` is the workspace-level default for the season/arc title. When a script header contains `Arc: "…"`, that value takes precedence; when absent, `project.json` `season_title` fills in. Resolution order: script header `Arc:` > `project.json` `season_title` > `None`. The `{season_title}` placeholder in preamble/postamble segment text resolves from this value.

File paths are derived dynamically: `cast_<slug>_<TAG>.json`, `sfx_<slug>_<TAG>.json`, `parsed/parsed_<slug>_<TAG>.json`, etc. The slug is the show name lowercased with all non-alphanumeric characters removed (e.g., `"THE 413"` → `"the413"`, `"Night Owls"` → `"nightowls"`).

## Project Scaffolding

`xil-init` scaffolds a new show workspace with sample content:

```bash
xil-init my-show --show "Night Owls"
```

Creates: `project.json`, `speakers.json`, `scripts/sample_S01E01.md`, and empty subdirectories (`parsed/`, `stems/`, `SFX/`, `daw/`, `masters/`, `cues/`). The sample script exercises all parser features (dialogue, directions, sections, scenes) so the user can immediately run `xil-scan` and `xil-parse --dry-run`.

## Speaker Configuration

`speakers.json` in the project root defines the speaker names the parser recognizes:
```json
[
    {"display": "ADAM", "key": "adam"},
    {"display": "MR. PATTERSON", "key": "mr_patterson"},
    {"display": "FILM AUDIO (MARGARET'S VOICE)", "key": "film_audio"}
]
```

Resolution order: `--speakers PATH` flag > `speakers.json` in CWD > built-in defaults. The list is auto-sorted longest-first for correct compound-name matching. Both `xil-scan` (XILP000) and `xil-parse` (XILP001) accept the `--speakers` flag.

## Pre-Flight Script Scanner

`XILP000_script_scanner.py` — Scans a raw markdown script and reports recognized/unrecognized speakers and sections **before** running XILP001. Use this whenever onboarding a new script to catch missing speakers or `SECTION_MAP` entries early.

```bash
python XILP000_script_scanner.py "scripts/<script>.md"
python XILP000_script_scanner.py "scripts/<script>.md" --json
```

- No `--episode` flag required — reads only the script file, no side effects
- Exit code 0 = all recognized (safe to run XILP001); exit code 1 = action needed
- Imports XILP001's pure functions directly — no duplicated logic
- `--json` outputs machine-readable scan results
- `--speakers PATH` overrides the speaker list (see Speaker Configuration)

## Architecture: Nine-Stage Pipeline (+ Cues Ingester Pre-Processing)

### Stage 1: Script Parsing
`XILP001_script_parser.py` — Parses markdown production scripts into structured JSON.

```bash
python XILP001_script_parser.py "scripts/<script>.md" --episode S01E01 --preview 10
```

- Input: Markdown scripts in `scripts/` — supports both plain text (S01E01) and markdown-formatted (S01E02+) scripts transparently
- Two-pass normalization: `strip_markdown_escapes()` removes `\[`, `\]`, etc.; `strip_markdown_formatting()` removes `**`, `##`/`###` headings, trailing double-space line breaks
- Handles both single-line dialogue (`SPEAKER (dir) Text`) and multi-line dialogue (speaker, direction, text on separate lines) via pending-speaker state machine
- Standalone parenthetical acting notes like `(beat)` or `(pause)` within dialogue continuations are filtered from spoken text
- Square-bracket stage directions with unrecognized `direction_type` (acting notes like `[drawn out]`, `[quietly]`) are silently skipped rather than emitted as `type: direction, direction_type: None` noise entries
- Dividers: accepts both `===` (plain text) and `---` (markdown horizontal rules)
- End markers: stops at `END OF EPISODE` or `END OF PRODUCTION SCRIPT`
- Output: `parsed/parsed_<slug>_S01E01.json` — entries with seq, type, section, scene, speaker, direction, text, direction_type
- Output path derived from script header metadata (season/episode); override with `--output`
- `--episode S01E01` (optional) validates that the script header matches the intended episode tag
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- When `--episode` is provided and `cast_<slug>_S01E01.json` / `sfx_<slug>_S01E01.json` don't exist, auto-generates skeleton configs with `voice_id=TBD` and default SFX prompts; the cast skeleton includes `season_title` populated from the script header's `Arc: "…"` declaration (or `null` when absent)
- `season_title` is extracted from the `Arc: "…"` token in the script header (e.g. `THE 413 Season 1: Episode 1: "The Empty Booth" Arc: "The Holiday Shift"`) and stored in the parsed JSON; it is available as `{season_title}` in preamble/postamble segment text
- Supports `--quiet` (JSON only, skip summary) and `--debug` (write diagnostic CSV alongside JSON)
- Auto-generates BEAT variants (`BEAT — 3 SECONDS` etc.) as `type: "silence"` with duration parsed from the text (e.g. 3.0s)
- Auto-generates `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` directives as `type: "silence", duration_seconds: 0.0` stop markers — no audio asset needed
- Known speakers loaded from `speakers.json` (see Speaker Configuration); built-in defaults used as fallback
- `--speakers PATH` overrides the speaker list (see Speaker Configuration)
- Sections: COLD OPEN, OPENING CREDITS, ACT ONE, ACT TWO, MID-EPISODE BREAK, CLOSING

### Stage 1.5: Cues Sheet Ingestion (Pre-processing)
`XILP006_cues_ingester.py` — Parses a sound cues & music prompts markdown file into a structured asset manifest, audits the shared SFX library, and optionally enriches the episode sfx config or generates new assets.

```bash
python XILP006_cues_ingester.py --episode S02E03 --cues "cues/<file>.md"
python XILP006_cues_ingester.py --episode S02E03 --cues "cues/<file>.md" --enrich-sfx-config
python XILP006_cues_ingester.py --episode S02E03 --cues "cues/<file>.md" --generate
python XILP006_cues_ingester.py --episode S02E03 --cues "cues/<file>.md" --generate --enrich-sfx-config
```

- `--episode` (required) derives the sfx config path (`sfx_<slug>_S02E03.json`)
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- `--cues PATH` explicit path to the cues markdown file; auto-detected from `cues/` if omitted and exactly one `.md` exists there (canonical name: `cues/cues_<slug>_S02E03.md`)
- Always writes `cues/cues_manifest_<TAG>.json` — structured JSON catalog of all parsed assets
- Always prints an audit report: EXISTS / REUSE / NEW status per asset, credit estimate for NEW generation
- `--enrich-sfx-config` — updates `sfx_<slug>_<TAG>.json` entries that reference a cues-sheet asset ID: replaces stub prompts with the full cues-sheet prompt and corrects duration (capped at 30s API limit)
- `--generate` — calls ElevenLabs Sound Effects API to generate NEW assets into `SFX/<asset-id>.mp3` (e.g. `SFX/sfx-boots-stamp-01.mp3`); skips assets already on disk; REUSE assets are never generated here
- `--dry-run` — suppresses API calls and sfx config writes; shows enrichment diff and generation credit estimate
- Parses three cue sheet sections: MUSIC CUES (heading blocks), AMBIENCE (heading blocks), SOUND EFFECTS (markdown tables per scene)
- Duration cap: assets longer than 30s are generated at 30s and flagged `[CAPPED]` in the audit

### Stage 2: Voice Generation
`XILP002_producer.py` — Calls ElevenLabs API to generate voice stems.

```bash
python XILP002_producer.py --episode S01E01 --dry-run
```

- `--episode` (required) derives `cast_<slug>_S01E01.json` and `sfx_<slug>_S01E01.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Reads: parsed JSON + cast config; always loads SFX config (for preamble music lookup and `--sfx-music`)
- Outputs: `stems/<TAG>/{seq:03d}_{section}[-{scene}]_{speaker}.mp3` (e.g. `stems/S01E01/003_cold-open_adam.mp3`)
- **Preamble stems** (when cast config has a `preamble` block): `n002_preamble_tina.mp3` (voice, seq −2) and `n001_preamble_sfx.mp3` (intro music, seq −1); music source read from `sfx_config.effects["INTRO MUSIC"].source`
- After generation, injects seq −2/−1 entries into the parsed JSON via `inject_preamble_entries()` — idempotent, re-running replaces existing preamble entries
- **Postamble stems** (when cast config has a `postamble` block): `{max+1:03d}_postamble_{speaker}.mp3` (voice) and `{max+2:03d}_postamble_sfx.mp3` (outro music, source from `sfx_config.effects["OUTRO MUSIC"].source`); injected into parsed JSON with `section="postamble"` via `inject_postamble_entries()` — idempotent
- Both `preamble` and `postamble` support multi-segment TTS: `segments` list with optional `shared_key` caches stock parts to `SFX/{shared_key}.mp3` (generated once, reused across episodes); episode-specific segments (no `shared_key`) are generated as temp files, concatenated with pydub, then cleaned up; legacy `text` field still works as a fallback
- Supports `--start-from N` for resuming interrupted runs; `--stop-at N` to halt after a specific seq (useful for previewing a section without regenerating the full episode)
- Supports `--dry-run` to preview lines and TTS character cost without API calls; includes a per-speaker breakdown table (lines + chars to generate vs. already on disk) sorted by chars descending; per-entry marker: `[ ]` = will generate, `[=]` = stem exists/skip, `[x]` = out of range
- Supports `--terse` to truncate each line to 3 words (minimizes TTS character cost)
- Supports `--gen-sfx`, `--gen-music`, `--gen-ambience` to generate only the specified categories of stems (replaces deprecated `--sfx-music` which is kept as a shorthand for all three)
- Supports `--local-only` (used with `--gen-sfx`/`--gen-music`/`--gen-ambience`) to skip any effect that would require an API call — only assets already in `SFX/` (CACHED) or silence entries are placed; no credits spent
- Intro music (`INTRO MUSIC` source entry): trimmed at copy time using `play_duration` percentage from sfx config, so the stem file reflects the actual playback length
- Skips stems that already exist on disk

### Stage 3: Audio Assembly
`XILP003_audio_assembly.py` — Two-pass multi-track mix into a final master MP3.

```bash
python XILP003_audio_assembly.py --episode S01E01
python XILP003_audio_assembly.py --episode S01E01 --parsed parsed/parsed_<slug>_S01E01.json
```

- When a parsed script JSON is available (auto-derived or via `--parsed`), runs a two-pass multi-track mix:
  - **Foreground pass**: dialogue + one-shot SFX/BEAT stems concatenated sequentially
  - **Background pass**: AMBIENCE stems looped across scene boundaries (ducked -10 dB); MUSIC stings overlaid at cue points (-6 dB)
  - Foreground and background combined via `AudioSegment.overlay()`
- Falls back to single-pass sequential concatenation when no parsed JSON is found
- Stem classification uses `direction_type` from the parsed JSON, keyed by seq number in the filename
- Shared mixing logic lives in `mix_common.py` — also used by XILP005
- Applies per-speaker effects (pan, phone filter) from cast config
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Supports `--output` to set the master MP3 path (default: `<slug>_S01E01_master.mp3`)
- `--gap-ms N` sets the silence gap between foreground stems in milliseconds (default: 600); reducing to 200–300 can shorten episode runtime by 1.5–2 minutes
- No ElevenLabs API key required — safe to re-run freely

### Stage 4: Studio Project Onboarding
`XILP004_studio_onboard.py` — Creates an ElevenLabs Studio project from parsed episode data.

```bash
python XILP004_studio_onboard.py --episode S01E02 --dry-run
python XILP004_studio_onboard.py --episode S01E02
python XILP004_studio_onboard.py --episode S01E02 --quality high
```

- `--episode` (required) derives `parsed_<slug>_S01E02.json` and `cast_<slug>_S01E02.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Builds `from_content_json` payload for the Studio Projects API with per-node `voice_id` assignments
- Solves the speaker-name problem: voice assignments are embedded directly — no speaker names in TTS text
- Content mapping: sections → chapters, dialogue → `tts_node` blocks, scene headers → `h2` blocks, directions → skipped
- `--dry-run` displays chapter/block summary with voice assignments without calling the API
- `--quality` sets quality preset (standard/high/ultra/ultra_lossless, default: standard)
- `--model` sets TTS model (default: eleven_v3)
- Validates no TBD voice_ids in cast config before proceeding
- Requires `ELEVENLABS_API_KEY` env var for non-dry-run mode

### Stage 5: DAW Layer Export
`XILP005_daw_export.py` — Exports four isolated, full-length WAV layers for human mixing in Audacity.

```bash
python XILP005_daw_export.py --episode S01E01 --dry-run
python XILP005_daw_export.py --episode S01E01
python XILP005_daw_export.py --episode S01E01 --macro
python XILP005_daw_export.py --episode S01E01 --output-dir exports/S01E01/
python XILP005_daw_export.py --episode S01E01 --dry-run --timeline
python XILP005_daw_export.py --episode S01E01 --timeline --timeline-html
```

- `--episode` (required) derives `cast_<slug>_S01E01.json` and `parsed/parsed_<slug>_S01E01.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Outputs four WAV files to `daw/{TAG}/` — all identical duration, all aligned at t=0:
  - `{TAG}_layer_dialogue.wav` — spoken dialogue (phone filter + pan applied)
  - `{TAG}_layer_ambience.wav` — environmental background looped to fill scene durations
  - `{TAG}_layer_music.wav` — music stings/themes at cue positions
  - `{TAG}_layer_sfx.wav` — one-shot SFX and BEAT silences
- Each WAV is tagged with ID3 metadata (Album, Genre, Year, Title, Artist) via `tag_wav()` from `sfx_common.py`
- Generates four Audacity label track files (`{TAG}_labels_dialogue.txt`, etc.) — tab-separated start/end/text
- Generates `{TAG}_open_in_audacity.py` — prints WAV import instructions (labels listed separately as optional)
- `--macro` writes an Audacity macro (`THE413_{TAG}.txt`) to `%APPDATA%\audacity\Macros\` for one-click WAV import via `Tools > Macros`
- `--dry-run` shows stem counts and output paths without writing files
- `--gap-ms N` sets the silence gap between foreground stems in milliseconds (default: 600); reducing to 200–300 can shorten episode runtime by 1.5–2 minutes
- `--save-aup3` includes a `SaveProject2` command in the generated `{TAG}_open_in_audacity.py` helper script (requires mod-script-pipe in Audacity)
- `--timeline` prints an ASCII multitrack timeline to stdout (works with `--dry-run` via fast mutagen header reads)
- `--timeline-html` writes a self-contained interactive HTML timeline to `daw/{TAG}/{TAG}_timeline.html` (hover tooltips, Ctrl+scroll zoom)
- Preamble stems (`n002_preamble_tina.mp3`, `n001_preamble_sfx.mp3`) are picked up automatically via `collect_stem_plans()` when their seq −2/−1 entries are present in the parsed JSON (injected by XILP002)
- No ElevenLabs API key required — no API calls made
- Shared mixing logic imported from `mix_common.py`; visualization via `timeline_viz.py`

### Stage 6: Stem Migration (Punch-In Workflow)
`XILP007_stem_migrator.py` — Migrates episode stems when a parsed script is revised. Compares an old and new parsed JSON, copies unchanged stems to their new seq-numbered filenames, and reports which entries need fresh TTS/SFX generation. Run XILP002 afterwards to fill only the gaps.

```bash
python XILP007_stem_migrator.py --episode S02E03 --dry-run
python XILP007_stem_migrator.py --episode S02E03
python XILP007_stem_migrator.py \
    --old parsed/orig_parsed_<slug>_S02E03.json \
    --new parsed/parsed_<slug>_S02E03.json \
    --stems stems/S02E03 [--dry-run] [--strict]
```

- `--episode TAG` derives `--old` (`parsed/orig_parsed_<slug>_{TAG}.json`), `--new` (`parsed/parsed_<slug>_{TAG}.json`), and `--stems` (`stems/{TAG}`) automatically
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- `--orig-prefix` (default: `orig_`) sets the filename prefix for the old parsed JSON
- `--dry-run` — shows the full plan without copying any files
- `--strict` — exact text match only; default is **fuzzy** (normalises em-dash, ellipsis, curly quotes so punctuation-only edits don't force unnecessary regen)
- `--quiet` — prints only the summary, not per-stem details
- Status codes printed per stem: `COPY` (unchanged, will be/was copied), `SPEAKER` (text matches but speaker reassigned → regen), `NEW` (no old entry matches → generate), `MISSING` (match found but old file absent → generate); each status line is followed by a truncated text snippet (first 55 chars) for visual content verification
- Two-phase matching: phase 1 matches on (text, speaker); phase 2 (dialogue only) falls back to text-only to detect speaker reassignments
- After running (without `--dry-run`), run `XILP002_producer.py --episode TAG` — it skips stems already on disk, so only SPEAKER/NEW/MISSING slots get API calls
- No ElevenLabs API key required — no API calls made

### Stage 7: Stale Stem Cleanup
`XILP008_stale_stem_cleanup.py` — Removes stale stems left behind after a parsed script revision and stem migration. After XILP007 copies unchanged stems to new seq-numbered filenames, old stems whose seq numbers now map to a different entry type remain on disk. This script finds and deletes them.

```bash
python XILP008_stale_stem_cleanup.py --episode S02E03 --dry-run
python XILP008_stale_stem_cleanup.py --episode S02E03
python XILP008_stale_stem_cleanup.py \
    --parsed parsed/parsed_<slug>_S02E03.json \
    --stems stems/S02E03 [--dry-run]
```

- `--episode TAG` derives `--parsed` (`parsed/parsed_<slug>_{TAG}.json`) and `--stems` (`stems/{TAG}`) automatically
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- `--parsed` and `--stems` override individual paths (both required if `--episode` is omitted)
- `--dry-run` — lists stale stems without deleting them
- A stem is stale when its filename disagrees with the current parsed entry: entry type is a header (`section_header`/`scene_header`), `_sfx` suffix but entry is now `dialogue`, speaker suffix but entry is now `direction`, dialogue stem whose speaker suffix doesn't match the parsed speaker, or seq not present in parsed JSON at all
- Duplicate detection: when multiple stems share the same seq, keeps only the one whose basename matches the expected `{seq}_{section}[-{scene}]_{speaker|sfx}` pattern
- Uses `extract_seq()` and `load_entries_index()` from `mix_common.py`
- No ElevenLabs API key required — no API calls made

### Stage 8: Studio Export Import
`XILP010_studio_import.py` — Extracts dialogue and direction stems from an ElevenLabs Studio export ZIP and renames them to the pipeline's stem naming convention.

```bash
python XILP010_studio_import.py --episode S02E02 --zip "ElevenLabs_exports/export.zip" --dry-run
python XILP010_studio_import.py --episode S02E02 --zip "ElevenLabs_exports/export.zip"
python XILP010_studio_import.py --episode S02E02 --zip "ElevenLabs_exports/export.zip" --gen-sfx --gen-music --gen-beats
python XILP010_studio_import.py --episode S02E02 --zip "ElevenLabs_exports/export.zip" --all --force
```

- `--episode TAG` (required) derives parsed JSON path and stems output directory
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- `--zip PATH` (required) path to the ElevenLabs Studio export ZIP
- `--parsed PATH` overrides parsed JSON path (default: `parsed/parsed_<slug>_{TAG}.json`)
- `--stems-dir PATH` overrides stems output directory (default: `stems/{TAG}`)
- `--dry-run` — shows extraction plan without writing files
- `--force` — overwrites existing stems on disk (default: skip if exists)
- `--gen-sfx` — include SFX direction entries (extracted as `_sfx` stems)
- `--gen-music` — include MUSIC direction entries (extracted as `_sfx` stems)
- `--gen-beats` — include BEAT direction entries (extracted as `_sfx` stems)
- `--all` — include all direction types (SFX, MUSIC, BEAT, AMBIENCE); headers are always skipped
- Dialogue entries are always extracted; direction entries require one of the `--gen-*` or `--all` flags
- ElevenLabs Studio exports one MP3 per parsed entry (`NNN_Chapter N.mp3`)
- Reuses `make_stem_name()` from XILP007 for canonical stem filename generation
- No ElevenLabs API key required — no API calls made

### Stage 9: Final Master MP3 Export
`XILP011_master_export.py` — Overlays the four DAW layer WAVs from XILP005 into a single podcast-ready MP3.

```bash
python XILP011_master_export.py --episode S02E03 --dry-run
python XILP011_master_export.py --episode S02E03
python XILP011_master_export.py --episode S02E03 --show "Night Owls"
```

- `--episode` (required) derives DAW layer paths and cast config
- `--show` overrides the show name (default: from `project.json`)
- `--daw-dir` overrides the DAW layer directory (default: `daw/<TAG>/`)
- `--output` overrides the output MP3 path (default: `masters/<TAG>_<slug>_<YYYY-MM-DD>.mp3`)
- `--dry-run` shows layer summary without writing files
- Output format: stereo, 48 kHz, VBR MP3 (~145–185 kbps, LAME quality 2)
- Output filename: `S02E03_the413_2026-03-24.mp3` (episode tag, show slug, run date)
- Overlays all four layers at unity gain (XILP005 handles mix balance)
- Reads cast config for ID3 metadata (album, title, artist)
- No ElevenLabs API key required — no API calls made

## ElevenLabs API Cost Controls

Every script that calls the API includes three guard functions (duplicated per file, not shared):
- `check_elevenlabs_quota()` — displays current character usage vs limit
- `has_enough_characters(text)` — per-line quota check before each API call
- `get_best_model_for_budget()` — always returns `eleven_v3`; logs a warning when balance is low (no longer falls back to `eleven_flash_v2_5`, which does not support `[pause]` and other native audio tags)

Always use `--dry-run` before running voice generation on a new script to verify TTS character budget.

## File Naming Convention

All scripts live under `src/xil_pipeline/` and are installed as `xil-*` console entry points plus a unified `xil` command via `pyproject.toml` (example: `xil parse ...` routes to `xil-parse`). Scripts use prefix `XIL` (ElevenLabs, avoiding numeric prefixes). The suffix pattern is:
- `XILP000_*` — pre-flight script scanner (no API, no side effects)
- `XILU001_*` — voice discovery (browse ElevenLabs voices; `--update-cast` back-fills role/language_code into a cast JSON)
- `XILU002_*` — standalone SFX stem generation
- `XILU003_*` — CSV + SFX/cast annotation utility (joins parsed episode CSV with SFX JSON and cast JSON for review)
- `XILU004_*` — voice sample generator (audition cast voices)
- `XILU005_*` — SFX library discovery (`--local` scans `SFX/` directory, default; `--api` queries ElevenLabs history)
- `XILU006_*` — parsed JSON splice utility (insert/delete entries with automatic seq renumbering)
- `XILP001_*` — script parser
- `XILP002_*` — voice generation (ElevenLabs TTS)
- `XILP003_*` — audio assembly (stems → master MP3, two-pass multi-track mix)
- `XILP004_*` — Studio project onboarding (ElevenLabs Studio Projects API)
- `XILP005_*` — DAW layer export (stems → per-layer WAVs for Audacity)
- `XILP006_*` — cues sheet ingester (cues markdown → SFX library + sfx config enrichment)
- `XILP007_*` — stem migrator (diff old vs new parsed JSON, copy unchanged stems, report what needs regen); `--dry-run` report shows truncated text snippets alongside COPY/NEW/SPEAKER/MISSING entries for visual content verification without cross-referencing JSON files
- `XILP008_*` — stale stem cleanup (delete stems whose seq no longer matches the current parsed JSON)
- `XILP009_*` — reverse script generator (parsed JSON → production script markdown)
- `XILP010_*` — Studio export importer (ElevenLabs Studio ZIP → pipeline stems)
- `XILP011_*` — final master MP3 export (overlay 4 DAW layer WAVs → single stereo 48 kHz VBR MP3)
- `mix_common.py` — shared mixing utilities (timeline, layer builders, fast label helpers) used by XILP003 and XILP005; `StemPlan.loop` field: `True` (default) tiles audio, `False` plays once up to scene boundary; `StemPlan.pre_trimmed` flag: skips play_duration trim for source-based stems already trimmed at copy time; `StemPlan.volume_percentage` (float|None): volume as a percentage (100 = unity, None = no change); `StemPlan.ramp_in_seconds` / `StemPlan.ramp_out_seconds`: fade durations in seconds (None = no fade); `_resolve_audio_params()` resolves volume/ramp from per-effect config or category defaults for MUSIC, AMBIENCE, SFX, and BEAT direction types; `volume_percentage`, `ramp_in_seconds`, and `ramp_out_seconds` each fall back to the global key when no category-specific key exists (e.g. SFX/MUSIC when `sfx_volume_percentage`/`music_ramp_in_seconds` are absent from the config defaults); `collect_stem_plans()` skips stale stems (header entries, type mismatch, speaker mismatch), deduplicates by seq number, and injects synthetic stop-marker `StemPlan` entries (filepath="") for `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` directives found in the entries index; `build_sfx_layer()` and `build_foreground()` apply `volume_percentage` to SFX/BEAT stems; `build_ambience_layer()` skips corrupt or unreadable stem files with a warning rather than crashing
- `sfx_common.py` — shared SFX library management, ID3 tagging (`tag_mp3`, `tag_wav`), effect generation; `ensure_shared_asset()` retries on 429 rate-limit errors (up to 5 times, linear backoff); `load_sfx_entries()` accepts `direction_types` filter set, returns `direction_type` field in each entry dict, skips entries with `duration_seconds=0.0`; `dry_run_sfx()` shows per-category credit subtotals in the SUMMARY block
- `timeline_viz.py` — multitrack timeline visualization; `render_terminal_timeline()` (ASCII) and `render_html_timeline()` (interactive HTML); no pydub dependency; HTML bar badges: `ri` (↑ ramp in, left), `ro` (↓ ramp out, right-top), `pd` (% play duration, center), `vb` (🔊 volume%, right-bottom, shown when `volume_pct != 100`); applies to music, ambience, and SFX spans
- `models.py` — Pydantic data models plus `show_slug()`, `derive_paths()`, `resolve_slug()` for dynamic show-based path derivation; `DEFAULT_SLUG = "sample"` fallback
- `xil.py` — unified dispatcher that maps subcommands (`scan`, `parse`, `produce`, etc.) to existing module `main()` entry points; prints command list on `xil --help`; `xil-*` commands remain supported
- `xil_init.py` — project scaffolding; `scaffold()` creates workspace with `project.json`, `speakers.json`, sample script, and empty subdirectories

## Cast Configuration

`cast_<slug>_S01E01.json` (e.g. `cast_the413_S01E01.json`) contains show-level metadata (`show`, `season`, `episode`, `title`) and a `cast` dict mapping character keys to settings:
```json
{
  "show": "THE 413", "season": 1, "episode": 1, "title": "The Holiday Shift",
  "cast": {
    "adam": { "full_name": "Adam Santos", "voice_id": "...", "pan": 0.0, "filter": false, "role": "Host/Narrator" }
  }
}
```
Voice IDs are discovered via `XILU001_discover_voices_T2S.py` (filters to premade category).

Optional `preamble` and `postamble` blocks (`intro_music_source` is **not** a field — intro/outro music lives in the SFX config under `"INTRO MUSIC"` / `"OUTRO MUSIC"` keys):
```json
{
  "preamble": {
    "speaker": "tina",
    "speed": 0.85,
    "segments": [
      { "text": "This is the Berkshire Talking Chronicle...", "shared_key": "preamble-the413-tina-intro" },
      { "text": "{season_title}, Episode {episode}, {title}, by Tina Brissette.", "shared_key": null },
      { "text": " Thank you for listening...", "shared_key": "preamble-the413-tina-outro" }
    ]
  },
  "postamble": {
    "speaker": "tina",
    "speed": 0.85,
    "segments": [
      { "text": "This is Tina Brissette... Episode {episode} \"{title}\"", "shared_key": null },
      { "text": " The material is read exactly as printed...", "shared_key": "postamble-the413-tina-outro" }
    ]
  }
}
```
Legacy single-string `"text"` field still works as a fallback for un-migrated episodes. `segments[].shared_key` caches stock parts to `SFX/{shared_key}.mp3` — generated once, reused across episodes. Use native v3 audio tags like `[pause]` for pauses — SSML (`<break time="1s"/>`) is no longer supported; `_select_model()` no longer falls back to `eleven_multilingual_v2` for SSML segments (it logs a warning instead). All TTS generation uses `eleven_v3` unconditionally.

## SFX Configuration

`sfx_<slug>_S01E01.json` (e.g. `sfx_the413_S01E01.json`) maps parsed direction entry text to ElevenLabs Sound Effects API parameters:
```json
{
  "show": "THE 413", "season": 1, "episode": 1,
  "defaults": { "prompt_influence": 0.3 },
  "effects": {
    "INTRO MUSIC": { "source": "SFX/The Porch Light.mp3" },
    "SFX: PHONE BUZZING": { "prompt": "Phone vibrating buzz", "duration_seconds": 2.0 },
    "BEAT": { "type": "silence", "duration_seconds": 1.0 }
  }
}
```
- Keys match the `text` field of parsed direction entries exactly
- `"INTRO MUSIC"` is the reserved key for preamble intro music; XILP002 reads its `source` field to copy the audio file into `n001_preamble_sfx.mp3` — no API generation
- `type: "sfx"` (default) entries call `client.text_to_sound_effects.convert()` with the `prompt`
- `type: "silence"` entries (BEAT/LONG BEAT) generate local silent audio — no API call
- `loop: false` entries play the audio file once up to the scene boundary (no tiling); `loop: true` (default) tiles the file to fill the full scene duration
- `volume_percentage` — per-effect volume as a percentage (100 = unity, 50 = half volume); applies to SFX, BEAT, MUSIC, and AMBIENCE entries; overrides the category default (`sfx_volume_percentage`, `music_volume_percentage`, `ambience_volume_percentage`) in the `defaults` block
- `play_duration` — percentage of file to play (e.g. `45` = play 45% of file duration); for INTRO MUSIC, the trim is applied when copying to the stem file so all downstream tools see the correct duration
- Stop markers: `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` entries use `type: "silence", duration_seconds: 0.0`; they inject a boundary marker into the mixing timeline without generating audio
- SFX stems use `_sfx` suffix: `002_cold-open_sfx.mp3`

### Shared SFX Library
Each unique sound effect is generated **once** into the `SFX/` directory as a shared asset (e.g. `SFX/beat.mp3`, `SFX/sfx_phone-buzzing.mp3`). Episode stems in `stems/<TAG>/` are copies of these shared assets with sequence-numbered filenames. This avoids regenerating the same effect for repeated uses (e.g. BEAT appears 26 times in S01E01). See `docs/sfx-reuse-guide.md` for a workflow guide on maximizing SFX reuse and minimizing API credit spend.

- Shared asset naming: `slugify_effect_key()` in `sfx_common.py` converts direction text to filesystem-safe slugs
- `--dry-run` shows three statuses: `EXISTS` (episode stem on disk), `CACHED` (shared asset exists, will be copied), `NEW` (needs API generation)
- Common SFX functions live in `sfx_common.py` — both XILU002 and XILP002 delegate to it
- `tag_mp3()` writes ID3 metadata (Album, Genre, Year, Title, Artist, Lyrics) to MP3 stems
- `tag_wav()` writes ID3 metadata (Album, Genre, Year, Title, Artist) to WAV layer exports

### Standalone SFX Utility
`XILU002_generate_SFX.py` — Generates SFX stems independently of XILP002 voice generation.

```bash
python XILU002_generate_SFX.py --episode S01E01 --dry-run
python XILU002_generate_SFX.py --episode S01E01 --gen-sfx
python XILU002_generate_SFX.py --episode S01E01 --gen-music
python XILU002_generate_SFX.py --episode S01E01 --gen-ambience
python XILU002_generate_SFX.py --episode S01E01 --max-duration 5.0
python XILU002_generate_SFX.py --episode S01E01 --local-only
python XILU002_generate_SFX.py --episode S01E01
```

- `--episode` (required) derives `cast_<slug>_S01E01.json` and `sfx_<slug>_S01E01.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Reads: parsed script JSON + SFX config + cast config (for episode tag)
- Outputs: shared assets to `SFX/`, episode stems to `stems/<TAG>/`
- `--dry-run` shows EXISTS/CACHED/NEW status per stem with credit estimates
- `--gen-sfx`, `--gen-music`, `--gen-ambience` filter generation to the specified categories; omitting all three processes all categories
- `--dry-run` SUMMARY now shows per-category credit subtotals (MUSIC / AMBIENCE / SFX / silence)
- `--max-duration N` filters to effects ≤ N seconds (controls API credit spend)
- `--local-only` skips any effect not already present in `SFX/`; only CACHED assets and silence entries are placed, no API calls made
- 429 rate-limit errors are retried automatically up to 5 times with linear backoff (10s, 20s, 30s, 40s, 50s)
- Skips stems that already exist on disk

### CSV Annotation Utility
`XILU003_csv_sfx_join.py` — Joins a parsed episode CSV with the SFX JSON and cast JSON, producing an annotated review CSV with SFX prompt, duration, and cast metadata columns appended alongside each dialogue and direction entry.

```bash
python XILU003_csv_sfx_join.py --episode S02E03
python XILU003_csv_sfx_join.py --episode S02E03 --output my_review.csv
```

- `--episode` (required) derives `parsed/parsed_<slug>_{TAG}.csv`, `sfx_<slug>_{TAG}.json`, and `cast_<slug>_{TAG}.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- `--csv`, `--sfx`, `--cast` override individual input paths
- `--output` overrides the output CSV path (default: `parsed/annotated_<slug>_{TAG}.csv`)
- No API key required — read-only join utility

### Voice Sample Utility
`XILU004_sample_voices_T2S.py` — Generates a short TTS sample for each cast member to audition voice assignments.

```bash
python XILU004_sample_voices_T2S.py --episode S02E03 --dry-run
python XILU004_sample_voices_T2S.py --episode S02E03
python XILU004_sample_voices_T2S.py --episode S02E03 --force
```

- `--episode` (required) or `--cast PATH` to specify the cast config
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Sample text: `"I am {full_name} not yo momma"` using `cast_member.full_name`
- Output: `voice_samples/{TAG}/{actor}.mp3` (e.g. `voice_samples/S02E03/adam.mp3`)
- Skips members with `voice_id=TBD`; `--force` regenerates existing samples
- Requires `ELEVENLABS_API_KEY`

### SFX Library Discovery
`XILU005_discover_SFX.py` — Lists and searches the local shared SFX asset library.

```bash
python XILU005_discover_SFX.py                    # local scan (default)
python XILU005_discover_SFX.py --local            # explicit local scan
python XILU005_discover_SFX.py --sfx-dir SFX/    # override local scan directory
python XILU005_discover_SFX.py --search "diner"   # filter by keyword
python XILU005_discover_SFX.py --json             # machine-readable output
python XILU005_discover_SFX.py --api              # attempt API history (not publicly accessible)
python XILU005_discover_SFX.py --api --all        # paginate full API history (default: most recent 100)
```

- Default mode: scans `SFX/` directory (equivalent to `--local`) and reports all assets with duration and file size
- `--local` / `--api` are mutually exclusive mode flags; `--local` is the default
- `--sfx-dir DIR` overrides the local scan directory (default: `SFX/`)
- `--search TEXT` filters results by case-insensitive substring match on filename/prompt
- `--json` outputs results as a JSON array
- `--verbose` / `-v` prints all metadata fields per asset
- `--api` attempts to query ElevenLabs sound generation history (endpoint is not publicly accessible as of March 2026 regardless of API key permissions)
- `--all` (API mode only) paginates through the full account history; default retrieves only the most recent 100 results
- `--export-kit [DIR]` generates an SFX inventory JSON (`sfx_inventory.json`) and copies the scriptwriter reference doc (`claude-scriptwriter-reference.md`) into DIR (default: current directory); attach both files to a Claude project as knowledge files to enable SFX-aware script writing

### Parsed JSON Splice Utility
`XILU006_splice_parsed.py` — Inserts entries into or deletes entries from a parsed episode JSON with automatic seq renumbering.

```bash
python XILU006_splice_parsed.py --episode S02E03 --insert-after 322 \
    --from-parsed parsed/parsed_the413_S02E02.json --from-seq-range 232-233 \
    --section post-interview --dry-run
python XILU006_splice_parsed.py --episode S02E03 --delete-seq-range 100-105 --dry-run
python XILU006_splice_parsed.py --episode S02E03 --insert-after 322 \
    --from-json new_entries.json
```

- `--episode` (required) derives target parsed JSON path
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- `--parsed PATH` overrides target parsed JSON path
- `--insert-after N` — seq number to insert after
- `--from-parsed PATH` + `--from-seq-range N-M` — extract entries from another parsed JSON by seq range
- `--from-json PATH` — read entries from a standalone JSON array file
- `--section` / `--scene` — override section/scene on inserted entries (default: inherit from insertion point)
- `--delete-seq-range N-M` — remove entries in range and renumber (can combine with insertion: delete first, then insert)
- `--dry-run` — show plan without writing files
- `--no-backup` — skip writing backup file
- `--quiet` — summary only, no per-entry detail
- Before modifying, writes `parsed/pre_splice_parsed_<slug>_<TAG>.json` as a backup (compatible with `XILP007 --orig-prefix pre_splice_`)
- Preamble entries (seq <= 0) are never renumbered or deleted
- Recomputes the `stats` block after modification
- No ElevenLabs API key required — no API calls made

## Developer/Maintainer Rules

Automated testing via Python and Bash serves as the fundamental mechanism for the Verification Loop. The project mandates that Claude must mention how it will verify its work before it begins any task.

Use tests for everything it implements:
- Determine which tests are appropriate; the model will then generate a test for every single feature it builds
- Test-Driven Development (TDD): A key best practice is implementing a verification-led technique where tests for a new feature are written first, followed by the actual code implementation

### Documentation Currency Rule

After executing any plan that changes pipeline behaviour, CLI flags, file formats, or module interfaces, **both** `CLAUDE.md` (root) and `docs/pipeline.md` must be updated to reflect those changes **before committing**. This applies equally to Claude and human contributors. Specifically:

- New CLI flags or flag removals → update the relevant stage description in CLAUDE.md and the corresponding section/sequence diagram in pipeline.md
- New module fields or dataclass additions → update `mix_common.py` / `sfx_common.py` bullet points in CLAUDE.md
- New SFX config keys or behaviours → update the SFX Configuration section
- New pipeline stages or utilities → add a XILP/XILU entry under File Naming Convention and a stage section in pipeline.md
- Any behavioural change visible to operators → update the relevant stage bullets in CLAUDE.md

If a plan is large enough to have its own plan file, tick this as the final step before closing the plan.

### Script Entry Point Style

Always use the `if __name__ == "__main__":` idiom. All application logic that would otherwise follow it must live inside a `main()` function — the dunder-main block must contain only the call to `main()`:

```python
def main():
    parser = argparse.ArgumentParser(...)
    args = parser.parse_args()
    # all application logic here

if __name__ == "__main__":
    main()
```

This keeps the `__main__` block to a single line, makes the entry point testable by calling `main()` directly, and prevents module-level side effects when the file is imported.

## Running Tests

```bash
pip install -e ".[all,dev]"
pytest tests/ -v
```

## Man Pages

Unix man pages for all 19 CLI commands are pre-generated and committed to `man/man1/`. They are installed automatically when the package is built into a wheel and installed via pip.

**Regenerating after CLI changes** (run whenever flags or descriptions change):

```bash
pip install -e ".[dev]"      # includes argparse-manpage
python scripts/build_man.py  # regenerate all 18 argparse-based pages
# xil.1 is hand-crafted — edit man/man1/xil.1 directly when the dispatcher changes
```

Regenerate a single page: `python scripts/build_man.py xil-parse`

Always commit the regenerated `.1` files alongside any CLI flag change. The `get_parser()` function in each module (extracted from `main()`) is what `build_man.py` calls to obtain the parser — keep it in sync with any `add_argument` changes.

**Post-install access on Debian** (for `pip install --user`):

```bash
# Pages land in ~/.local/share/man/man1/ — add to ~/.bashrc:
export MANPATH="$HOME/.local/share/man:$(manpath 2>/dev/null)"

# Then:
man xil-parse

# For apropos/whatis support:
mandb --user-db ~/.local/share/man
```

System-wide installs (`sudo pip install`) land in `/usr/local/share/man/man1/` and are indexed by default (`sudo mandb` to refresh).

## Key Directories

- `src/xil_pipeline/` — Python package (all pipeline and utility scripts, shared modules)
- `tests/` — Automated test suite (pytest)
- `scripts/` — Source markdown production scripts (authored manually)
- `parsed/` — Parser JSON output (generated, cacheable)
- `cues/` — Sound cues & music prompts markdown files (authored manually); `cues_manifest_<TAG>.json` generated by XILP006
- `stems/<TAG>/` — Individual voice/SFX audio files per episode (generated, expensive to recreate)
- `SFX/` — Shared SFX asset library (generated once, reused across episodes); cues-sheet assets named by asset ID (e.g. `sfx-boots-stamp-01.mp3`)
- `daw/<TAG>/` — Per-layer WAV exports for DAW mixing (generated by XILP005)
- `venv/` — Python virtualenv (do not commit)
