# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated podcast/audio production pipeline using ElevenLabs TTS API. The project evolved from a simple multi-voice ad proof-of-concept into a full podcast episode producer for "THE 413" fiction podcast.

## Environment

- Python 3.13, virtualenv at `venv/`
- WSL2 (Linux on Windows)
- Activate: `source venv/bin/activate`
- Key packages: `elevenlabs`, `pydub`, `google-genai`, `gTTS`, `pyttsx3`, `ollama`
- ElevenLabs API key via `ELEVENLABS_API_KEY` env var
- Audio playback via `mpg123` in WSL

## Pre-Flight Script Scanner

`XILP000_script_scanner.py` — Scans a raw markdown script and reports recognized/unrecognized speakers and sections **before** running XILP001. Use this whenever onboarding a new script to catch missing `KNOWN_SPEAKERS` or `SECTION_MAP` entries early.

```bash
python XILP000_script_scanner.py "scripts/<script>.md"
python XILP000_script_scanner.py "scripts/<script>.md" --json
```

- No `--episode` flag required — reads only the script file, no side effects
- Exit code 0 = all recognized (safe to run XILP001); exit code 1 = action needed
- Imports XILP001's pure functions directly — no duplicated logic
- `--json` outputs machine-readable scan results

## Architecture: Six-Stage Pipeline (+ Cues Ingester Pre-Processing)

### Stage 1: Script Parsing
`XILP001_script_parser.py` — Parses markdown production scripts into structured JSON.

```bash
python XILP001_script_parser.py "scripts/<script>.md" --episode S01E01 --preview 10
```

- Input: Markdown scripts in `scripts/` — supports both plain text (S01E01) and markdown-formatted (S01E02+) scripts transparently
- Two-pass normalization: `strip_markdown_escapes()` removes `\[`, `\]`, etc.; `strip_markdown_formatting()` removes `**`, `##`/`###` headings, trailing double-space line breaks
- Handles both single-line dialogue (`SPEAKER (dir) Text`) and multi-line dialogue (speaker, direction, text on separate lines) via pending-speaker state machine
- Standalone parenthetical acting notes like `(beat)` or `(pause)` within dialogue continuations are filtered from spoken text
- Dividers: accepts both `===` (plain text) and `---` (markdown horizontal rules)
- End markers: stops at `END OF EPISODE` or `END OF PRODUCTION SCRIPT`
- Output: `parsed/parsed_the413_S01E01.json` — entries with seq, type, section, scene, speaker, direction, text, direction_type
- Output path derived from script header metadata (season/episode); override with `--output`
- `--episode S01E01` (optional) validates that the script header matches the intended episode tag
- When `--episode` is provided and `cast_the413_S01E01.json` / `sfx_the413_S01E01.json` don't exist, auto-generates skeleton configs with `voice_id=TBD` and default SFX prompts
- Supports `--quiet` (JSON only, skip summary) and `--debug` (write diagnostic CSV alongside JSON)
- Auto-generates BEAT variants (`BEAT — 3 SECONDS` etc.) as `type: "silence"` with duration parsed from the text (e.g. 3.0s)
- Auto-generates `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` directives as `type: "silence", duration_seconds: 0.0` stop markers — no audio asset needed
- Known speakers defined in `KNOWN_SPEAKERS` list (must be longest-first for multi-word and compound names like "FILM AUDIO (MARGARET'S VOICE)")
- Sections: COLD OPEN, OPENING CREDITS, ACT ONE, ACT TWO, MID-EPISODE BREAK, CLOSING

### Stage 1.5: Cues Sheet Ingestion (Pre-processing)
`XILP006_the413_cues_ingester.py` — Parses a sound cues & music prompts markdown file into a structured asset manifest, audits the shared SFX library, and optionally enriches the episode sfx config or generates new assets.

```bash
python XILP006_the413_cues_ingester.py --episode S02E03 --cues "cues/<file>.md"
python XILP006_the413_cues_ingester.py --episode S02E03 --cues "cues/<file>.md" --enrich-sfx-config
python XILP006_the413_cues_ingester.py --episode S02E03 --cues "cues/<file>.md" --generate
python XILP006_the413_cues_ingester.py --episode S02E03 --cues "cues/<file>.md" --generate --enrich-sfx-config
```

- `--episode` (required) derives the sfx config path (`sfx_the413_S02E03.json`)
- `--cues PATH` explicit path to the cues markdown file; auto-detected from `cues/` if omitted and exactly one `.md` exists there (canonical name: `cues/cues_the413_S02E03.md`)
- Always writes `cues/cues_manifest_<TAG>.json` — structured JSON catalog of all parsed assets
- Always prints an audit report: EXISTS / REUSE / NEW status per asset, credit estimate for NEW generation
- `--enrich-sfx-config` — updates `sfx_the413_<TAG>.json` entries that reference a cues-sheet asset ID: replaces stub prompts with the full cues-sheet prompt and corrects duration (capped at 30s API limit)
- `--generate` — calls ElevenLabs Sound Effects API to generate NEW assets into `SFX/<asset-id>.mp3` (e.g. `SFX/sfx-boots-stamp-01.mp3`); skips assets already on disk; REUSE assets are never generated here
- `--dry-run` — suppresses API calls and sfx config writes; shows enrichment diff and generation credit estimate
- Parses three cue sheet sections: MUSIC CUES (heading blocks), AMBIENCE (heading blocks), SOUND EFFECTS (markdown tables per scene)
- Duration cap: assets longer than 30s are generated at 30s and flagged `[CAPPED]` in the audit

### Stage 2: Voice Generation
`XILP002_the413_producer.py` — Calls ElevenLabs API to generate voice stems.

```bash
python XILP002_the413_producer.py --episode S01E01 --dry-run
```

- `--episode` (required) derives `cast_the413_S01E01.json` and `sfx_the413_S01E01.json`
- Reads: parsed JSON + cast config; always loads SFX config (for preamble music lookup and `--sfx-music`)
- Outputs: `stems/<TAG>/{seq:03d}_{section}[-{scene}]_{speaker}.mp3` (e.g. `stems/S01E01/003_cold-open_adam.mp3`)
- **Preamble stems** (when cast config has a `preamble` block): `n002_preamble_tina.mp3` (voice, seq −2) and `n001_preamble_sfx.mp3` (intro music, seq −1); music source read from `sfx_config.effects["INTRO MUSIC"].source`
- After generation, injects seq −2/−1 entries into the parsed JSON via `inject_preamble_entries()` — idempotent, re-running replaces existing preamble entries
- Supports `--start-from N` for resuming interrupted runs
- Supports `--dry-run` to preview lines and TTS character cost without API calls
- Supports `--terse` to truncate each line to 3 words (minimizes TTS character cost)
- Supports `--gen-sfx`, `--gen-music`, `--gen-ambience` to generate only the specified categories of stems (replaces deprecated `--sfx-music` which is kept as a shorthand for all three)
- Intro music (`INTRO MUSIC` source entry): trimmed at copy time using `play_duration` percentage from sfx config, so the stem file reflects the actual playback length
- Skips stems that already exist on disk

### Stage 3: Audio Assembly
`XILP003_the413_audio_assembly.py` — Two-pass multi-track mix into a final master MP3.

```bash
python XILP003_the413_audio_assembly.py --episode S01E01
python XILP003_the413_audio_assembly.py --episode S01E01 --parsed parsed/parsed_the413_S01E01.json
```

- When a parsed script JSON is available (auto-derived or via `--parsed`), runs a two-pass multi-track mix:
  - **Foreground pass**: dialogue + one-shot SFX/BEAT stems concatenated sequentially
  - **Background pass**: AMBIENCE stems looped across scene boundaries (ducked -10 dB); MUSIC stings overlaid at cue points (-6 dB)
  - Foreground and background combined via `AudioSegment.overlay()`
- Falls back to single-pass sequential concatenation when no parsed JSON is found
- Stem classification uses `direction_type` from the parsed JSON, keyed by seq number in the filename
- Shared mixing logic lives in `mix_common.py` — also used by XILP005
- Applies per-speaker effects (pan, phone filter) from cast config
- Supports `--output` to set the master MP3 path (default: `the413_S01E01_master.mp3`)
- No ElevenLabs API key required — safe to re-run freely

### Stage 4: Studio Project Onboarding
`XILP004_the413_studio_onboard.py` — Creates an ElevenLabs Studio project from parsed episode data.

```bash
python XILP004_the413_studio_onboard.py --episode S01E02 --dry-run
python XILP004_the413_studio_onboard.py --episode S01E02
python XILP004_the413_studio_onboard.py --episode S01E02 --quality high
```

- `--episode` (required) derives `parsed_the413_S01E02.json` and `cast_the413_S01E02.json`
- Builds `from_content_json` payload for the Studio Projects API with per-node `voice_id` assignments
- Solves the speaker-name problem: voice assignments are embedded directly — no speaker names in TTS text
- Content mapping: sections → chapters, dialogue → `tts_node` blocks, scene headers → `h2` blocks, directions → skipped
- `--dry-run` displays chapter/block summary with voice assignments without calling the API
- `--quality` sets quality preset (standard/high/ultra/ultra_lossless, default: standard)
- `--model` sets TTS model (default: eleven_v3)
- Validates no TBD voice_ids in cast config before proceeding
- Requires `ELEVENLABS_API_KEY` env var for non-dry-run mode

### Stage 5: DAW Layer Export
`XILP005_the413_daw_export.py` — Exports four isolated, full-length WAV layers for human mixing in Audacity.

```bash
python XILP005_the413_daw_export.py --episode S01E01 --dry-run
python XILP005_the413_daw_export.py --episode S01E01
python XILP005_the413_daw_export.py --episode S01E01 --macro
python XILP005_the413_daw_export.py --episode S01E01 --output-dir exports/S01E01/
python XILP005_the413_daw_export.py --episode S01E01 --dry-run --timeline
python XILP005_the413_daw_export.py --episode S01E01 --timeline --timeline-html
```

- `--episode` (required) derives `cast_the413_S01E01.json` and `parsed/parsed_the413_S01E01.json`
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
- `--timeline` prints an ASCII multitrack timeline to stdout (works with `--dry-run` via fast mutagen header reads)
- `--timeline-html` writes a self-contained interactive HTML timeline to `daw/{TAG}/{TAG}_timeline.html` (hover tooltips, Ctrl+scroll zoom)
- Preamble stems (`n002_preamble_tina.mp3`, `n001_preamble_sfx.mp3`) are picked up automatically via `collect_stem_plans()` when their seq −2/−1 entries are present in the parsed JSON (injected by XILP002)
- No ElevenLabs API key required — no API calls made
- Shared mixing logic imported from `mix_common.py`; visualization via `timeline_viz.py`

## ElevenLabs API Cost Controls

Every script that calls the API includes three guard functions (duplicated per file, not shared):
- `check_elevenlabs_quota()` — displays current character usage vs limit
- `has_enough_characters(text)` — per-line quota check before each API call
- `get_best_model_for_budget()` — switches from `eleven_v3` to `eleven_flash_v2_5` when balance is low

Always use `--dry-run` before running voice generation on a new script to verify TTS character budget.

## File Naming Convention

Scripts use prefix `XIL` (ElevenLabs, avoiding numeric prefixes). The suffix pattern is:
- `XILP000_*` — pre-flight script scanner (no API, no side effects)
- `XILU001_*` — voice discovery
- `XILU002_*` — standalone SFX stem generation
- `XILU004_*` — voice sample generator (audition cast voices)
- `XILU005_*` — SFX library discovery (local scan of SFX/ directory)
- `XILP001_*` — script parser
- `XILP002_*` — voice generation (ElevenLabs TTS)
- `XILP003_*` — audio assembly (stems → master MP3, two-pass multi-track mix)
- `XILP004_*` — Studio project onboarding (ElevenLabs Studio Projects API)
- `XILP005_*` — DAW layer export (stems → per-layer WAVs for Audacity)
- `XILP006_*` — cues sheet ingester (cues markdown → SFX library + sfx config enrichment)
- `mix_common.py` — shared mixing utilities (timeline, layer builders, fast label helpers) used by XILP003 and XILP005; `StemPlan.loop` field: `True` (default) tiles audio, `False` plays once up to scene boundary; `StemPlan.pre_trimmed` flag: skips play_duration trim for source-based stems already trimmed at copy time; `collect_stem_plans()` injects synthetic stop-marker `StemPlan` entries (filepath="") for `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` directives found in the entries index; `build_ambience_layer()` skips corrupt or unreadable stem files with a warning rather than crashing
- `sfx_common.py` — shared SFX library management, ID3 tagging (`tag_mp3`, `tag_wav`), effect generation; `ensure_shared_asset()` retries on 429 rate-limit errors (up to 5 times, linear backoff); `load_sfx_entries()` accepts `direction_types` filter set, returns `direction_type` field in each entry dict, skips entries with `duration_seconds=0.0`; `dry_run_sfx()` shows per-category credit subtotals in the SUMMARY block
- `timeline_viz.py` — multitrack timeline visualization; `render_terminal_timeline()` (ASCII) and `render_html_timeline()` (interactive HTML); no pydub dependency

## Cast Configuration

`cast_the413_S01E01.json` contains show-level metadata (`show`, `season`, `episode`, `title`) and a `cast` dict mapping character keys to settings:
```json
{
  "show": "THE 413", "season": 1, "episode": 1, "title": "The Holiday Shift",
  "cast": {
    "adam": { "full_name": "Adam Santos", "voice_id": "...", "pan": 0.0, "filter": false, "role": "Host/Narrator" }
  }
}
```
Voice IDs are discovered via `XILU001_discover_voices_T2S.py` (filters to premade category).

Optional `preamble` block (`intro_music_source` is **not** a field — intro music lives in the SFX config):
```json
{
  "preamble": {
    "text": "This is Tina Brissette... Today on The 4 1 3, {season_title}, Episode {episode}, {title}.",
    "speaker": "tina",
    "speed": 0.85
  }
}
```

## SFX Configuration

`sfx_the413_S01E01.json` maps parsed direction entry text to ElevenLabs Sound Effects API parameters:
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
- `play_duration` — percentage of file to play (e.g. `45` = play 45% of file duration); for INTRO MUSIC, the trim is applied when copying to the stem file so all downstream tools see the correct duration
- Stop markers: `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` entries use `type: "silence", duration_seconds: 0.0`; they inject a boundary marker into the mixing timeline without generating audio
- SFX stems use `_sfx` suffix: `002_cold-open_sfx.mp3`

### Shared SFX Library
Each unique sound effect is generated **once** into the `SFX/` directory as a shared asset (e.g. `SFX/beat.mp3`, `SFX/sfx_phone-buzzing.mp3`). Episode stems in `stems/<TAG>/` are copies of these shared assets with sequence-numbered filenames. This avoids regenerating the same effect for repeated uses (e.g. BEAT appears 26 times in S01E01).

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
python XILU002_generate_SFX.py --episode S01E01
```

- `--episode` (required) derives `cast_the413_S01E01.json` and `sfx_the413_S01E01.json`
- Reads: parsed script JSON + SFX config + cast config (for episode tag)
- Outputs: shared assets to `SFX/`, episode stems to `stems/<TAG>/`
- `--dry-run` shows EXISTS/CACHED/NEW status per stem with credit estimates
- `--gen-sfx`, `--gen-music`, `--gen-ambience` filter generation to the specified categories; omitting all three processes all categories
- `--dry-run` SUMMARY now shows per-category credit subtotals (MUSIC / AMBIENCE / SFX / silence)
- `--max-duration N` filters to effects ≤ N seconds (controls API credit spend)
- 429 rate-limit errors are retried automatically up to 5 times with linear backoff (10s, 20s, 30s, 40s, 50s)
- Skips stems that already exist on disk

### Voice Sample Utility
`XILU004_sample_voices_T2S.py` — Generates a short TTS sample for each cast member to audition voice assignments.

```bash
python XILU004_sample_voices_T2S.py --episode S02E03 --dry-run
python XILU004_sample_voices_T2S.py --episode S02E03
python XILU004_sample_voices_T2S.py --episode S02E03 --force
```

- `--episode` (required) or `--cast PATH` to specify the cast config
- Sample text: `"I am {full_name} not yo momma"` using `cast_member.full_name`
- Output: `voice_samples/{TAG}/{actor}.mp3` (e.g. `voice_samples/S02E03/adam.mp3`)
- Skips members with `voice_id=TBD`; `--force` regenerates existing samples
- Requires `ELEVENLABS_API_KEY`

### SFX Library Discovery
`XILU005_discover_SFX.py` — Lists and searches the local shared SFX asset library.

```bash
python XILU005_discover_SFX.py                    # local scan (default)
python XILU005_discover_SFX.py --search "diner"   # filter by keyword
python XILU005_discover_SFX.py --json             # machine-readable output
python XILU005_discover_SFX.py --api              # attempt API history (endpoint may not be public)
```

- Default mode: scans `SFX/` directory and reports all assets with duration and file size
- `--search TEXT` filters results by case-insensitive substring match on filename/prompt
- `--json` outputs results as a JSON array
- `--api` attempts to query ElevenLabs sound generation history (endpoint is not publicly accessible as of March 2026 regardless of API key permissions)

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
python -m pytest tests/ -v
```

## Key Directories

- `tests/` — Automated test suite (pytest)
- `scripts/` — Source markdown production scripts (authored manually)
- `parsed/` — Parser JSON output (generated, cacheable)
- `cues/` — Sound cues & music prompts markdown files (authored manually); `cues_manifest_<TAG>.json` generated by XILP006
- `stems/<TAG>/` — Individual voice/SFX audio files per episode (generated, expensive to recreate)
- `SFX/` — Shared SFX asset library (generated once, reused across episodes); cues-sheet assets named by asset ID (e.g. `sfx-boots-stamp-01.mp3`)
- `daw/<TAG>/` — Per-layer WAV exports for DAW mixing (generated by XILP005)
- `venv/` — Python virtualenv (do not commit)
