# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated, show-agnostic podcast/audio production pipeline using ElevenLabs TTS API. Turns a markdown production script into a podcast-ready MP3.

## Package Structure

The project is packaged as `xil-pipeline` (import name `xil_pipeline`) using hatchling. All pipeline and utility scripts live under `src/xil_pipeline/`:

```
src/xil_pipeline/          # Python package (42 modules)
  __init__.py              # version + key re-exports
  xil.py                   # Unified `xil` command dispatcher
  log_config.py            # Logging setup — pretty console + structured v2 log file
  models.py                # Pydantic data models, slug/path resolution
  mix_common.py            # Shared mixing utilities
  sfx_common.py            # SFX library management, ID3 tagging
  sfx_backends.py          # Pluggable SFX backends (ElevenLabs API / local AudioLDM 2 / local Stable Audio Open)
  timeline_viz.py          # Timeline visualization
  xil_init.py              # Project scaffolding (xil-init command, --type aware)
  REMOVED_MARKER # Persistent Chatterbox TTS worker (venv-chatterbox subprocess)
  chatterbox_turbo_worker.py  # Persistent Chatterbox Turbo TTS worker — native paralinguistic tags (venv-chatterbox)
  whisper_worker.py        # Persistent Faster-Whisper STT worker (venv-whisper subprocess)
  XILP000_*.py … XILP012_*.py   # Pipeline stages
  mmaudio_worker.py        # Persistent MMAudio text-to-audio SFX worker (venv-mmaudio subprocess)
  XILU001_*.py … XILU021_*.py   # Utility scripts
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
- **PARALINGUISTIC TAG NEAR-MISSES** report section: flags inline `[tags]` that look like misspelled Chatterbox Turbo cues (`[laughs]` → `[laugh]`, `[clears throat]` → `[clear throat]`, `[surprise]` → `[surprised]`) and suggests the correct token. Turbo silently strips unknown tags, so these otherwise fail invisibly at produce time. Detection is `difflib` similarity ≥ 0.72 against `ALLOWED_TAGS` plus an alias map for variants difflib scores too low; tags that simply aren't Turbo cues (ElevenLabs-only `[exhausted]`, `[pause]`, `[curious]`) are never flagged. **Advisory only — never affects the exit code**, since the same script may target the ElevenLabs backend. Implemented by `scan_paralinguistic_tags()`; the scan JSON carries it under `paralinguistic_near_misses`

## Architecture: Nine-Stage Pipeline (+ Cues Ingester Pre-Processing)

### Stage 1: Script Parsing

`XILP001_script_parser.py` — Parses markdown production scripts into structured JSON.

```bash
xil parse "scripts/<script>.md" --episode S01E01 --preview 10
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
- `season_title` is extracted from the `Arc: "…"` token in the script header (e.g. `THE 413 Season 1: Episode 1: "The Empty Booth" Arc: "The Holiday Shift"`) and stored in the parsed JSON; it is available as `{season_title}` in PREAMBLE/POSTAMBLE script text
- Supports `--quiet` (JSON only, skip summary) and `--debug` (write diagnostic CSV alongside JSON)
- Auto-generates BEAT variants (`BEAT — 3 SECONDS` etc.) as `type: "silence"` with duration parsed from the text (e.g. 3.0s)
- Auto-generates `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` directives as `type: "silence", duration_seconds: 0.0` stop markers — no audio asset needed
- Known speakers resolved in priority order: `CAST:` block in the script header (auto-discovered, no setup required) → `speakers.json` enrichment (JSON key overrides auto-derived key) → built-in defaults (only when both sources are absent); new characters declared in `CAST:` are recognized immediately without editing `speakers.json`
- `--speakers PATH` overrides the speakers.json path (see Speaker Configuration)
- Sections: COLD OPEN, OPENING CREDITS, ACT ONE, ACT TWO, MID-EPISODE BREAK, CLOSING
- Direction pipe-hints: `[<TEXT> | <file>.mp3 | <key>=<value>]` — segments after the direction text are classified independently (order-free, either half optional). A `.mp3`/`.wav` segment becomes the cue's `source`; a `key=value` segment whose key is in `HINT_ATTRS` becomes a per-cue config override (currently `play_volume_pct=20%` → `volume_percentage: 20`, the `%` optional, range 0–200). Unrecognized segments are left on the direction text rather than swallowed, and a malformed value warns and is dropped instead of failing the parse. Hints land on the parsed entry as `sfx_source` / `sfx_overrides`, then flow into `sfx_<TAG>.json` via `generate_sfx_config()` (new config) or `backfill_sfx_sources()` / `xil sfx-hydrate` (existing config). **A `source` hint never replaces one already in the config; attribute hints do overwrite** — the script is authoritative for playback settings. Silence cues (BEAT) ignore attribute hints. Add a key to `HINT_ATTRS` to support a new attribute; the rest of the chain is generic

### Stage 1.5: Cues Sheet Ingestion (Pre-processing)

`XILP006_cues_ingester.py` — Parses a sound cues & music prompts markdown file into a structured asset manifest, audits the shared SFX library, and optionally enriches the episode sfx config or generates new assets.

```bash
xil cues --episode S02E03 --cues "cues/<file>.md"
xil cues --episode S02E03 --cues "cues/<file>.md" --enrich-sfx-config
xil cues --episode S02E03 --cues "cues/<file>.md" --generate
xil cues --episode S02E03 --cues "cues/<file>.md" --generate --enrich-sfx-config
```

- `--episode` or `--tag` (one required) derives the sfx config path (`sfx_<slug>_S02E03.json`)
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
xil produce --episode S01E01 --dry-run
```

- `--episode` or `--tag` (one required) derives `cast_<slug>_S01E01.json` and `sfx_<slug>_S01E01.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Reads: parsed JSON + cast config; always loads SFX config (for INTRO/OUTRO MUSIC source lookup)
- Outputs: `stems/<slug>/<TAG>/{seq:03d}_{section}[-{scene}]_{speaker}.mp3` (e.g. `stems/the413/S01E01/003_cold-open_adam.mp3`)
- **Preamble/Postamble**: `PREAMBLE` and `POSTAMBLE` are first-class script sections parsed by XILP001 and written into the parsed JSON with contiguous seq numbers. XILP002 generates their voice stems through the standard `generate_voices()` loop, applying a per-section speed override from the cast config `preamble.speed` / `postamble.speed` field. No separate injection step — preamble/postamble entries exist in the parsed JSON from the start
- INTRO/OUTRO MUSIC source stems are copied from `sfx_config.effects["INTRO MUSIC"/"OUTRO MUSIC"].source` at produce time
- Supports `--start-from N` for resuming interrupted runs; `--stop-at N` to halt after a specific seq (useful for previewing a section without regenerating the full episode)
- Supports `--dry-run` to preview lines and TTS character cost without API calls; includes a per-speaker breakdown table (lines + chars to generate vs. already on disk) sorted by chars descending; per-entry marker: `[ ]` = will generate, `[=]` = stem exists/skip, `[x]` = out of range
- Supports `--terse` to truncate each line to 3 words (minimizes TTS character cost)
- Supports `--gen-sfx`, `--gen-music`, `--gen-ambience` to generate only the specified categories of stems (replaces deprecated `--sfx-music` which is kept as a shorthand for all three)
- Supports `--local-only` (used with `--gen-sfx`/`--gen-music`/`--gen-ambience`) to skip any effect that would require an API call — only assets already in `SFX/` (CACHED) or silence entries are placed; no credits spent
- Supports `--backend elevenlabs|gtts|chatterbox-turbo` (default: `elevenlabs`): `gtts` routes all dialogue voice stems through Google Translate TTS at no cost — flat single voice, useful for duration checks; `chatterbox-turbo` uses the local **Chatterbox Turbo** model (`venv-chatterbox`) with per-character zero-shot voice cloning from `voice_refs/<key>.wav` reference clips, and natively renders 19 paralinguistic tags (see Chatterbox Turbo Paralinguistic Tags below); SFX/music/ambience generation is unaffected by `--backend`. Tag handling differs by backend: eleven_v3 native tags are passed through to ElevenLabs; `gtts` **strips all `[tags]`**; `chatterbox-turbo` **keeps allow-listed paralinguistic tags** and strips the rest (e.g. ElevenLabs-only `[exhausted]`)
- **Classic `chatterbox` was removed in #62** (Turbo supersedes it). `--backend chatterbox` is still accepted as a deprecated alias: it warns and generates with Turbo, and the stem is recorded as `chatterbox-turbo`. **Historical data still says `chatterbox`** — ~8,460 log records and ~1,559 stem-manifest entries — and is still parsed by `xil-stem-log`. Those values are correct history, not corruption; `backend` is part of the manifest dedup key, so old entries simply never match a new request
- `--backend chatterbox-turbo` options: `--chatterbox-python PATH` (default: auto-detect `./venv-chatterbox/bin/python3`); `--voice-refs DIR` (default: `voice_refs/`) for per-speaker `.wav` reference clips; missing voice refs fall back to Chatterbox default voice
- `--backend chatterbox-turbo` uses `--chatterbox-python`/`--voice-refs` and `venv-chatterbox`. Reference clips **must be >5 seconds** (Turbo asserts this); Turbo conditionals are cached as `voice_refs/<key>.turbo.conds.pt` — kept distinct because they are **not** interchangeable with the classic `.conds.pt` files still on disk. If the model repo is gated, set `HF_TOKEN` before first run
- `--device cuda|cpu` (default: `cuda`) selects the Chatterbox Turbo device. Not usually needed: `chatterbox_turbo_worker.py` auto-falls back to `cpu` whenever `cuda` is requested but `torch.cuda.is_available()` is false (slower, but functional — no separate flag or setup needed for a CPU-only machine), logging the fallback and reporting the actual device in its ready signal, which the client surfaces as a warning if it differs from what was requested. `--device cpu` is for forcing CPU even when a GPU **is** available (e.g. it's busy with another job) — the worker never overrides an explicit `cpu` request back to `cuda`

#### Chatterbox Turbo Paralinguistic Tags

`ResembleAI/chatterbox-turbo` renders **19** paralinguistic cues as dedicated tokens (IDs 50257–50275 in the model's `added_tokens.json`). Write them inline in script dialogue; `chatterbox_turbo_worker.py` keeps these and strips every other bracketed token before generation.

| Category | Tags |
| --- | --- |
| Emotion | `[angry]` `[fear]` `[surprised]` `[happy]` `[crying]` `[sarcastic]` |
| Delivery style | `[whispering]` `[dramatic]` `[narration]` `[advertisement]` |
| Vocal gesture | `[laugh]` `[chuckle]` `[sigh]` `[gasp]` `[groan]` `[cough]` `[sniff]` `[shush]` `[clear throat]` |

Authoring rules:

- **Exact spelling only — there are no plural or variant forms.** `[laugh]` is a token; `[laughs]`, `[chuckles]`, `[coughs]` are not, and are stripped. Likewise `[clear throat]` (with the space) is the token — `[clears throat]` and `[throat clearing]` are stripped.
- Matching is case-insensitive, so `[LAUGH]` and `[Angry]` both work.
- Any tag outside this set is removed, which is what makes ElevenLabs-only tags (`[exhausted]`, `[pause]`) safe to leave in a shared script — they are honoured under `--backend elevenlabs` and dropped under `chatterbox-turbo`.
- The allow-list lives in `ALLOWED_TAGS` in `chatterbox_turbo_worker.py` and is pinned by `tests/test_chatterbox_turbo_worker.py`. It is derived from the model's tokenizer, not from prose docs — re-derive from `added_tokens.json` after a model bump rather than editing by hand.
- Only the `chatterbox-turbo` backend understands these. Classic `chatterbox` and `gtts` strip all `[tags]`; ElevenLabs has its own separate tag vocabulary.
- Supports `--sfx-backend elevenlabs|mmaudio` (default: `elevenlabs`) — an **independent** backend axis for SFX/MUSIC/AMBIENCE generation, orthogonal to the dialogue `--backend`. `mmaudio` runs MMAudio locally in `venv-mmaudio` (text-to-audio mode) — free, GPU-accelerated (~6 GB VRAM), writes backend-tagged assets to `SFX/<slug>.mmaudio.mp3`
- **MMAudio weights are CC BY-NC 4.0 — NON-COMMERCIAL ONLY** (the code is MIT, the checkpoints are not). `--mmaudio-accept-noncommercial` is **required**; without it the backend refuses to start. Every session logs the constraint and every generated asset carries it in its ID3 comment, so an MMAudio clip stays identifiable by filename *and* by tag if a show is later monetised
- MMAudio is trained at **8 seconds** and warns that large deviation degrades quality, so generation always runs at `--mmaudio-duration` (default 8.0) and the result is **trimmed in the backend** to the cue's `duration_seconds`. The trim cannot be left to the mixer: for a prompt-generated cue `duration_seconds` is the *generation length* and there is no mix-time clip (that applies only to `source=` cues). Other options: `--mmaudio-python`, `--mmaudio-cfg` (4.5), `--mmaudio-steps` (25), `--mmaudio-negative-prompt`, `--mmaudio-seed`
- MMAudio is primarily a **video**-to-audio model; this uses its supported text-only path. It installs from a git clone, not PyPI
- **venv-mmaudio install order matters.** MMAudio declares `torch >= 2.5.1` with no upper bound, so `pip install -e MMAudio` pulls the newest torch (a CUDA 13 build) and silently replaces a driver-matched one — on a CUDA 12.x driver that falls back to **CPU-only** and breaks torchaudio. Re-pin `torch/torchaudio/torchvision` from the cu124 index **after** the MMAudio install, then confirm `torch.cuda.is_available()` is True. Do **not** try to clean up the leftover `nvidia-*` CUDA 13 packages by uninstalling them individually: cu12 and cu13 share install paths, so removing one deletes shared libraries the other still claims (`libcudnn.so.9`, `libnccl.so.2` disappear while pip still lists the package). Force-reinstall the torch stack instead
- `mmaudio_worker.py` wraps generation in `torch.inference_mode()`. This is **required**, not an optimisation — MMAudio's own `demo.py` decorates its entry point the same way, and without it generation fails with "Inference tensors cannot be saved for backward"
- **Optional STT venv** (`venv-whisper/`): a separate virtualenv at the workspace or repo root containing `faster-whisper`. Used by `xil-stem-verify` (XILU015) for post-generation transcription verification. Worker script: `whisper_worker.py` (same JSON-over-stdin/stdout subprocess protocol as `chatterbox_worker.py`). Probes CUDA at startup and auto-falls back to CPU/int8 if `libcublas` is unavailable.
- Intro music (`INTRO MUSIC` source entry): trimmed at copy time using `play_duration` percentage from sfx config, so the stem file reflects the actual playback length
- Skips stems that already exist on disk

### Stage 3: Audio Assembly

`XILP003_audio_assembly.py` — Two-pass multi-track mix into a final master MP3.

```bash
xil assemble --episode S01E01
xil assemble --episode S01E01 --parsed parsed/parsed_<slug>_S01E01.json
```

- When a parsed script JSON is available (auto-derived or via `--parsed`), runs a two-pass multi-track mix:
  - **Foreground pass**: dialogue + one-shot SFX/BEAT stems concatenated sequentially
  - **Background pass**: AMBIENCE stems looped across scene boundaries (ducked -10 dB); MUSIC stings overlaid at cue points (-6 dB)
  - Foreground and background combined via `AudioSegment.overlay()`
- Falls back to single-pass sequential concatenation when no parsed JSON is found
- Stem classification uses `direction_type` from the parsed JSON, keyed by seq number in the filename
- Shared mixing logic lives in `mix_common.py` — also used by XILP005
- Applies per-speaker effects (pan, audio filters) from cast config; `filter` field accepts `false`/`null` (none), `true`/`"phone"` (phone filter), `"vintage"` (vintage filter), `"film"` (Film Audio — warm 1990s film print, ffmpeg), `"speakerphone"` (narrow-band speakerphone, ffmpeg), or a comma-separated combination such as `"vintage,phone"`; unknown names are ignored with a warning
- Applies scene-scoped vintage filter to all dialogue in scenes listed in `sfx_config.vintage_scenes`; applied after the per-speaker filter chain
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Supports `--output` to set the master MP3 path (default: `<slug>_S01E01_master.mp3`)
- `--gap-ms N` sets the silence gap between foreground stems in milliseconds (default: 600); reducing to 200–300 can shorten episode runtime by 1.5–2 minutes
- No ElevenLabs API key required — safe to re-run freely

### Stage 4: Studio Project Onboarding

`XILP004_studio_onboard.py` — Creates an ElevenLabs Studio project from parsed episode data.

```bash
xil studio-onboard --episode S01E02 --dry-run
xil studio-onboard --episode S01E02
xil studio-onboard --episode S01E02 --quality high
```

- `--episode` or `--tag` (one required) derives `parsed_<slug>_S01E02.json` and `cast_<slug>_S01E02.json`
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

`XILP005_daw_export.py` — Exports up to five isolated, full-length WAV layers for human mixing in Audacity.

```bash
xil daw --episode S01E01 --dry-run
xil daw --episode S01E01
xil daw --episode S01E01 --macro
xil daw --episode S01E01 --output-dir exports/S01E01/
xil daw --episode S01E01 --dry-run --timeline
xil daw --episode S01E01 --timeline --timeline-html
```

- `--episode` or `--tag` (one required) derives `cast_<slug>_S01E01.json` and `parsed/parsed_<slug>_S01E01.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Outputs up to five WAV files to `daw/{TAG}/` — all identical duration, all aligned at t=0:
  - `{TAG}_layer_dialogue.wav` — spoken dialogue (audio filter chain + pan applied per speaker)
  - `{TAG}_layer_ambience.wav` — environmental background looped to fill scene durations
  - `{TAG}_layer_music.wav` — music stings/themes at cue positions
  - `{TAG}_layer_sfx.wav` — one-shot SFX and BEAT silences
  - `{TAG}_layer_vintage_filter.wav` — vintage-filtered dialogue layer; only present when VINTAGE FILTER direction markers appear in the script
- Each WAV is tagged with ID3 metadata (Album, Genre, Year, Title, Artist) via `tag_wav()` from `sfx_common.py`
- Generates four Audacity label track files (`{TAG}_labels_dialogue.txt`, etc.) — tab-separated start/end/text
- Generates `{TAG}_open_in_audacity.py` — prints WAV import instructions (labels listed separately as optional)
- `--macro` writes an Audacity macro (`THE413_{TAG}.txt`) to `%APPDATA%\audacity\Macros\` for one-click WAV import via `Tools > Macros`
- `--dry-run` shows stem counts and output paths without writing files
- `--gap-ms N` sets the silence gap between foreground stems in milliseconds (default: 600); reducing to 200–300 can shorten episode runtime by 1.5–2 minutes
- `--save-aup3` includes a `SaveProject2` command in the generated `{TAG}_open_in_audacity.py` helper script (requires mod-script-pipe in Audacity)
- `--timeline` prints an ASCII multitrack timeline to stdout (works with `--dry-run` via fast mutagen header reads)
- `--timeline-html` writes a self-contained interactive HTML timeline to `daw/{TAG}/{TAG}_timeline.html` (hover tooltips, Ctrl+scroll zoom)
- The HTML timeline shows two **structure bands above the minute ruler** — Sections (`cold-open`, `act1`, …) over Scenes (`scene-1`, …) — derived from the parsed entries' `section`/`scene` fields via `derive_structure_bands()` in `mix_common.py`. Band boundaries come from the first *stemmed* entry of each group (section/scene headers carry no stem); entries with no scene (e.g. preamble) leave a deliberate gap
- **The HTML is a static artifact**: all CSS/JS/data are inlined at generation time and the GUI only reads the file off disk, so renderer changes never reach already-generated timelines. Regenerate cheaply — without re-exporting layer WAVs — with `xil daw --episode {TAG} --dry-run --timeline-html`
- Preamble/postamble stems are picked up automatically via `collect_stem_plans()` — they are regular seq-numbered entries in the parsed JSON (no special negative-seq handling)
- No ElevenLabs API key required — no API calls made
- Shared mixing logic imported from `mix_common.py`; visualization via `timeline_viz.py`

### Stage 6: Stem Migration (Punch-In Workflow)

`XILP007_stem_migrator.py` — Migrates episode stems when a parsed script is revised. Compares an old and new parsed JSON, copies unchanged stems to their new seq-numbered filenames, and reports which entries need fresh TTS/SFX generation. Run XILP002 afterwards to fill only the gaps.

```bash
xil migrate --episode S02E03 --dry-run
xil migrate --episode S02E03
xil migrate \
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
xil cleanup --episode S02E03 --dry-run
xil cleanup --episode S02E03
xil cleanup \
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
xil import --episode S02E02 --zip "ElevenLabs_exports/export.zip" --dry-run
xil import --episode S02E02 --zip "ElevenLabs_exports/export.zip"
xil import --episode S02E02 --zip "ElevenLabs_exports/export.zip" --gen-sfx --gen-music --gen-beats
xil import --episode S02E02 --zip "ElevenLabs_exports/export.zip" --all --force
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

`XILP011_master_export.py` — Overlays the DAW layer WAVs from XILP005 into a single podcast-ready MP3.

```bash
xil master --episode S02E03 --dry-run
xil master --episode S02E03
xil master --episode S02E03 --show "Night Owls"
```

- `--episode` or `--tag` (one required) derives DAW layer paths and cast config
- `--show` overrides the show name (default: from `project.json`)
- `--daw-dir` overrides the DAW layer directory (default: `daw/<TAG>/`)
- `--output` overrides the output MP3 path (default: `masters/<TAG>_<slug>_<YYYY-MM-DD>.mp3`)
- `--dry-run` shows layer summary without writing files
- Output format: stereo, 48 kHz, VBR MP3 (~145–185 kbps, LAME quality 2)
- Output filename: `S02E03_the413_2026-03-24.mp3` (episode tag, show slug, run date)
- Overlays all present DAW layers at unity gain (XILP005 handles mix balance)
- Reads cast config for ID3 metadata (album, title, artist)
- Auto-detects `configs/<slug>/cover_art.PNG` (also `.png`, `.jpg`, `.jpeg`) and embeds it as an ID3 APIC front-cover frame — silently skipped when absent
- No ElevenLabs API key required — no API calls made

### Stage 10: Social Media Post Draft Generator

`XILP012_publish.py` — Reads a parsed episode JSON, builds a structured episode summary, and calls the Claude API (Haiku) to produce three ready-to-edit Facebook/Instagram post variants. Output is an editable markdown file.

```bash
xil publish --episode S04E01 --dry-run
xil publish --episode S04E01
xil publish --episode S04E01 --platform instagram
xil publish --all
```

- `--episode` or `--tag` (one required unless `--all` is used) derives parsed JSON and cast config
- `--show` overrides the show name (default: from `project.json`)
- `--platform facebook|instagram` — affects post length/style guidance (default: `facebook`)
- `--dry-run` — prints the Claude prompt and estimated token count; no API call, no file written
- `--all` — generate posts for every parsed episode under the current show slug (retroactive batch)
- `--model` — override the Claude model ID (default: `claude-haiku-4-5-20251001`)
- Output: `posts/{slug}/{tag}_posts.md` — editable markdown with three variants
- Three post variants: **Hype** (new episode teaser, no spoilers past cold open), **Quote** (pull quote from cold open + tune-in CTA), **Spotlight** (one cast member feature, cycles by episode number mod cast count)
- Requires `ANTHROPIC_API_KEY` environment variable (only for non-dry-run mode)
- Requires `[publish]` optional extra: `pip install 'xil-pipeline[publish]'` (installs `anthropic>=0.40`)
- System prompt is tagged with `cache_control: ephemeral` to minimize cost on `--all` batch runs
- No ElevenLabs API key required — no ElevenLabs calls made

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
- `XILU007_*` — MP3 hash utility (compute SHA256 checksums for stem files)
- `XILU008_*` — stem log report (parse daily logs → chronological stem generation CSV with backend/model/hash)
- `XILU009_*` — workspace migration tool (move pre-0.1.8 legacy layout to normalized `configs/`/`parsed/`/`daw/` structure)
- `XILU010_*` — MP3 loudness profiler (peak, average, and minimum dBFS per stem or directory)
- `XILU011_*` — SFX config CSV flattener (sfx_<tag>.json → one-row-per-effect CSV for audit/debug)
- `XILU012_*` — parsed JSON CSV exporter (parsed_<tag>.json → one-row-per-entry CSV for audit/debug)
- `XILU013_*` — SFX config hydrator (writes pipe-hint `source` fields from parsed JSON into the SFX config). By default fills only *missing* sources. `--force` also **replaces** a differing one — guarded by an existence check: a replacement is skipped (with a warning) when the hinted file is not on disk, which protects bare `SFX/<file>` sources that work today from being repointed at a slug-form path with nothing behind it. `--dry-run --force` prints `old.mp3 → new.mp3` per cue; **always preview**, because a stale hint will happily replace a well-matched asset with a worse one. Every source set or replaced is written to the edit journal
- `XILU014_*` — episode summary CSV generator (scans all parsed JSONs → one-row-per-episode CSV with dialogue_lines, words, tts_chars)
- `XILU015_*` — stem verifier (scans a stems folder → JSON report with filename, seq/scene/speaker parse, size, duration, bitrate, SHA-256, and optional Faster-Whisper transcription; requires `venv-whisper/`)
- `XILU016_*` — stem compare (cross-references a `stem_verify` Whisper transcript report against the parsed script; flags garbled/silent/missing dialogue stems using `difflib.SequenceMatcher`; no extra dependencies)
- `XILU017_*` — remove show (delete all workspace files for a given show slug; `--dry-run` safe; `--yes` skips confirmation prompt; `--include-scripts` also removes `scripts/*_{slug}_*.md` files; `SFX/` and `logs/` are never touched; accepts show name or slug; clears `.active_show` when it points to the removed show)
- `XILU018_*` — remove episode (delete all workspace artifacts for a single episode tag — cast/sfx configs, parsed JSON/CSV, stems dir, DAW dir, master MP3s, cues, posts, voice_samples; `--dry-run` safe; `--yes` skips confirmation prompt; source script in `scripts/`, shared `SFX/`, and `logs/` are never touched; handles both normalized and legacy layouts)
- `XILU021_*` — SFX clipping impact report (`xil sfx-impact`; sweeps every show's `sfx_<tag>.json`, measures each `source=` cue against the file on disk, and grades how much audio `duration_seconds` is cutting: `1-nochange` <0.1s lost, `2-minor` <3s, `3-review` ≥3s, `EXCLUDED` for looped beds and explicit `play_duration`, `MISSING` for unreadable sources; mirrors the precedence in `mix_common.collect_stem_plans` so the report cannot drift from the mixer; each impacted row carries the config edit that would restore full length — always `play_duration: 100`, **not** `duration_seconds: 0`: both play the whole file, but only `play_duration` is in `SFX_EDIT_FIELDS` and therefore replayed by the timeline edit journal, so a `duration_seconds` edit silently reverts to the parser's 5.0 default on the next skeleton rebuild; **read-only — never writes a config**; emits CSV to `reports/sfx_impact_<date>.csv` plus an optional self-contained `--html` review page; `--show` / `--episode` narrow the sweep, `--tier` filters, `--output -` streams CSV to stdout)
- `XILU022_*` — SFX library reconciliation (`xil sfx-match`; for every cue whose declared `source` does not resolve on disk — the ones that make `xil produce` refuse to start and `xil sfx-impact` grades `MISSING` — searches the whole `SFX/` library for the sound under another name. Matches on the asset's **ID3 `TIT2` title** (its originating cue text, written by `tag_mp3`), falling back to the `USLT` generation prompt then the filename; titles and filenames in this library have drifted apart, so the report shows the title. Ranks by **coverage** (`|cue ∩ cand| / |cue|`) with jaccard as tie-break — coverage leads because library titles are far more verbose than cue text and a symmetric measure punishes the right answers. Tiers `EXACT` (slug hit) / `STRONG` / `REVIEW` / `NONE`. Two guards keep a plausible-looking wrong answer from auto-applying: a **category gate** (`classify_placement` must agree, so an `AMBIENCE:` cue never matches a `MUSIC` asset) and a **margin rule** (a leader with no daylight over its runner-up is a review case — real cues do have three candidates tied at coverage 1.00). Exact-slug lookup runs over **all** assets, not the deduped pool, because dedupe can discard the very copy carrying the slug name. `--apply` copies the match into the show's pool named for the **cue** (`shared_sfx_path(sfx_dir(show), cue)`), retitles only `TIT2`/`COMM` so the prompt and grade frames survive, and journals the new `source` — after which the exact-slug rule holds and `xil discover-sfx --export-kit` lists it under the cue text, so the scriptwriter stops inventing placeholders for it. `--emit-hints` writes a paste-ready `[CUE | file.mp3]` block with commented alternates; `--apply` takes `EXACT`+`STRONG` unless `--accept-review`; the library scan is deferred until a broken cue is actually found)
- `xil_gui.py` — Gradio web dashboard (`xil-gui` entry point); requires `[gui]` extra; nine tabs in order: Setup (with content type selector), Project, Episodes, Run Stage, Speakers, Cast Config, SFX Config, Audio Preview, Timeline
- `xil_use.py` — active show context switcher (`xil-use` entry point / `xil use`); no args lists shows in `configs/` and marks the active one; with a show name or slug (multi-word names may be unquoted) writes `<workspace>/.active_show`
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
- `XILP011_*` — final master MP3 export (overlay DAW layer WAVs → single stereo 48 kHz VBR MP3; embeds APIC cover art from `configs/<slug>/cover_art.PNG` when present)
- `XILP012_*` — social media post draft generator (parsed JSON + Claude Haiku → `posts/{slug}/{tag}_posts.md`; 3 variants: Hype, Quote, Spotlight; requires `[publish]` extra + `ANTHROPIC_API_KEY`)
- `mix_common.py` — shared mixing utilities (timeline, layer builders, fast label helpers) used by XILP003 and XILP005; `StemPlan.scene` (str|None): scene label from parsed JSON, used for scene-scoped vintage filter; `StemPlan.loop` field: `True` (default) tiles audio, `False` plays once up to scene boundary; `StemPlan.pre_trimmed` flag: skips play_duration trim for source-based stems already trimmed at copy time; `StemPlan.volume_percentage` (float|None): volume as a percentage (100 = unity, None = no change); `StemPlan.ramp_in_seconds` / `StemPlan.ramp_out_seconds`: fade durations in seconds (None = no fade); `_resolve_audio_params()` resolves volume/ramp from per-effect config or category defaults for MUSIC, AMBIENCE, SFX, and BEAT direction types; `volume_percentage`, `ramp_in_seconds`, and `ramp_out_seconds` each fall back to the global key when no category-specific key exists (e.g. SFX/MUSIC when `sfx_volume_percentage`/`music_ramp_in_seconds` are absent from the config defaults); `collect_stem_plans()` skips stale stems (header entries, type mismatch, speaker mismatch), deduplicates by seq number, and injects synthetic stop-marker `StemPlan` entries (filepath="") for `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` directives found in the entries index; `build_sfx_layer()` and `build_foreground()` apply `volume_percentage` to SFX/BEAT stems; `build_ambience_layer()` skips corrupt or unreadable stem files with a warning rather than crashing; `apply_vintage_filter()` applies a 1960s-era HF roll-off + 1 dB reduction; `_apply_speaker_filters(segment, filter_val)` resolves the cast config `filter` string and dispatches through `FILTER_REGISTRY` (name → module-level function name, resolved late so patching works): `phone`, `vintage`, `film`, `speakerphone`; unknown names warn once and pass through; `_span_treatments(stem_plans)` maps dialogue seq → ordered treatment names from open script spans, driven by `SPAN_DIRECTION_TREATMENTS` (`VINTAGE FILTER`→`vintage`, `FILM AUDIO`→`film`); overlapping spans stack in declaration order; `_vf_engaged_seqs(stem_plans)` is a thin wrapper retained for the existing test surface — used by `build_foreground()` and `build_dialogue_layer()` (script-direction spans take precedence over `vintage_scenes` list fallback); `_SPAN_SENTINEL_MARKERS` controls which markers get synthetic boundary plans (VINTAGE FILTER: DISENGAGES only, since ENGAGES binds a real crackle stem; FILM AUDIO: both)
- `sfx_common.py` — shared SFX library management, ID3 tagging (`tag_mp3`, `tag_wav`), effect generation; `ensure_shared_sfx()` / `generate_sfx()` accept a `backend: SfxBackend` (defaults to an ElevenLabs backend wrapping `client`) and delegate all model generation to it — the streaming-download + 429/5xx/network retry loop now lives in `ElevenLabsSfxBackend`, not inline; `shared_sfx_path(sfx_dir, effect_key, backend="elevenlabs")` returns backend-tagged paths for non-default backends; `load_sfx_entries()` accepts `direction_types` filter set, returns `direction_type` field in each entry dict, skips entries with `duration_seconds=0.0`; `dry_run_sfx()` accepts `backend_name` — matches assets against the backend-tagged path and reports local backends as free instead of API credits; `tag_mp3()` accepts optional `cover_art_path` — when supplied and file exists, reads the image and embeds an APIC front-cover frame (PNG or JPEG detected by extension) in the same `tags.save()` call as other ID3 fields
- `sfx_backends.py` — SFX/music/ambience generation behind the `SfxBackend` protocol (`generate_to(out_path, prompt, duration_seconds, prompt_influence)` + `close()`); `ElevenLabsSfxBackend` wraps `client.text_to_sound_effects.convert` (streaming, atomic rename, retry on 429/5xx/network); `_WorkerClient` is the shared JSON-over-stdio subprocess bridge for local model workers (subclasses set `_WORKER`/`_LABEL`/`_READY_HINT`, may override `_request_extras()`); `MMAudioSfxBackend` drives `_MMAudioClient` (`mmaudio_worker.py`) and exposes `asset_comment`, which `ensure_shared_sfx` passes to `tag_mp3` so the CC BY-NC notice is embedded in the file; `make_sfx_backend(name, client=None, **kw)` is the factory and raises `ValueError` for unknown names
- `timeline_viz.py` — multitrack timeline visualization; `render_terminal_timeline()` (ASCII) and `render_html_timeline()` (interactive HTML); no pydub dependency; HTML bar badges: `ri` (↑ ramp in, left), `ro` (↓ ramp out, right-top), `pd` (% play duration, center), `vb` (🔊 volume%, right-bottom, shown when `volume_pct != 100`); applies to music, ambience, and SFX spans
- `models.py` — Pydantic data models plus `get_workspace_root()` (respects `XIL_PROJECTROOT` env var), `show_slug()`, `derive_paths()`, `resolve_slug()` for dynamic show-based path derivation; `DEFAULT_SLUG = "sample"` fallback; `ProjectConfig` model with `type`/`tag_format` fields; `TYPE_DEFAULTS` dict with gap_ms and stability per content type; `derive_paths_legacy()` returns pre-0.1.8 paths anchored to workspace root (used by migration tool); `derive_paths()` auto-detects layout (legacy if root cast config exists, normalized otherwise); `load_project_config()` / `resolve_project_type()` helpers
- `xil.py` — unified dispatcher that maps subcommands (`scan`, `parse`, `produce`, etc.) to existing module `main()` entry points; prints command list on `xil --help`; `xil-*` commands remain supported
- `xil_init.py` — project scaffolding; `--type podcast|audiobook|drama|special` selects sample script, speakers.json, and project.json `type` field; creates new normalized workspace layout (`configs/{slug}/`)

## Cast Configuration

`cast_<slug>_S01E01.json` (e.g. `cast_the413_S01E01.json`) contains show-level metadata (`show`, `season`, `episode`, `title`) and a `cast` dict mapping character keys to settings:

```json
{
  "show": "THE 413", "season": 1, "episode": 1, "title": "The Holiday Shift",
  "cast": {
    "adam": { "full_name": "Adam Santos", "voice_id": "...", "pan": 0.0, "filter": false, "role": "Host/Narrator" },
    "mr_patterson": { "full_name": "Mr. Patterson", "voice_id": "...", "pan": -0.3, "filter": "phone", "role": "Caller" },
    "young_adam": { "full_name": "Young Adam", "voice_id": "...", "pan": 0.0, "filter": "vintage", "role": "Flashback" }
  }
}
```

Voice IDs are discovered via `XILU001_discover_voices_T2S.py` (filters to premade category).

Optional `preamble` and `postamble` blocks define the speaker and speed for those sections. The actual dialogue text lives in the script (PREAMBLE/POSTAMBLE sections); the cast config only needs `speaker` and `speed`:

```json
{
  "preamble": {
    "speaker": "tina",
    "speed": 0.85
  },
  "postamble": {
    "speaker": "tina",
    "speed": 0.85
  }
}
```

Intro/outro music lives in the SFX config under `"INTRO MUSIC"` / `"OUTRO MUSIC"` keys — not in the cast config. All TTS generation uses `eleven_v3` unconditionally.

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
- `"INTRO MUSIC"` / `"OUTRO MUSIC"` are reserved keys for preamble/postamble music; XILP002 reads their `source` field to copy the audio file into the appropriate seq-numbered stem — no API generation
- `type: "sfx"` (default) entries call `client.text_to_sound_effects.convert()` with the `prompt`
- `type: "silence"` entries (BEAT/LONG BEAT) generate local silent audio — no API call
- `loop: false` entries play the audio file once up to the scene boundary (no tiling); `loop: true` (default) tiles the file to fill the full scene duration
- `volume_percentage` — per-effect volume as a percentage (100 = unity, 50 = half volume); applies to SFX, BEAT, MUSIC, and AMBIENCE entries; overrides the category default (`sfx_volume_percentage`, `music_volume_percentage`, `ambience_volume_percentage`) in the `defaults` block
- `play_duration` — percentage of file to play (e.g. `45` = play 45% of file duration); for INTRO MUSIC, the trim is applied when copying to the stem file so all downstream tools see the correct duration
- Stop markers: `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` entries use `type: "silence", duration_seconds: 0.0`; they inject a boundary marker into the mixing timeline without generating audio
- `vintage_scenes` — optional top-level list of scene labels (e.g. `["scene-3", "scene-4", "scene-5"]`); all dialogue in those scenes receives the vintage audio filter (HF roll-off + 1 dB reduction) during assembly; applied after per-speaker filters; omit or leave empty for no vintage treatment; the same scene labels used in the parsed JSON `scene` field
- SFX stems use `_sfx` suffix: `002_cold-open_sfx.mp3`
- Both per-effect overrides (`effects.<key>.volume_percentage`/`ramp_in_seconds`/`ramp_out_seconds`/`play_duration`) and category defaults (`defaults.<layer>_volume_percentage`/`<layer>_ramp_in_seconds`/`<layer>_ramp_out_seconds` for `music`/`sfx`/`ambience`) are editable from the xil-gui Timeline tab's double-click modal — see the Web Dashboard section's Timeline-tab bullet

### Shared SFX Library

Each unique sound effect is generated **once** into the `SFX/` directory as a shared asset (e.g. `SFX/beat.mp3`, `SFX/sfx_phone-buzzing.mp3`). Episode stems in `stems/<slug>/<TAG>/` are copies of these shared assets with sequence-numbered filenames. This avoids regenerating the same effect for repeated uses (e.g. BEAT appears 26 times in S01E01). See `docs/guides/sfx-reuse-guide.md` for a workflow guide on maximizing SFX reuse and minimizing API credit spend.

- Shared asset naming: `slugify_effect_key()` in `sfx_common.py` converts direction text to filesystem-safe slugs; `shared_sfx_path(sfx_dir, effect_key, backend)` appends a `.<backend>` infix for non-ElevenLabs backends (historical assets only, e.g. `SFX/sfx_door-opens.audioldm2.mp3`), keeping the plain name for `elevenlabs` so existing caches still hit. Only model-generated `type: sfx` assets are backend-tagged; `silence`/`source` assets stay plain
- `--dry-run` shows three statuses: `EXISTS` (episode stem on disk), `CACHED` (shared asset exists, will be copied), `NEW` (needs generation). NEW generation is reported with an API credit estimate
- Common SFX functions live in `sfx_common.py` — both XILU002 and XILP002 delegate to it. The actual audio generation is delegated to a pluggable `SfxBackend` (`sfx_backends.py`)
- `tag_mp3()` writes ID3 metadata (Album, Genre, Year, Title, Artist, Lyrics) to MP3 stems
- `tag_wav()` writes ID3 metadata (Album, Genre, Year, Title, Artist) to WAV layer exports

### Standalone SFX Utility

`XILU002_generate_SFX.py` — Generates SFX stems independently of XILP002 voice generation.

```bash
xil sfx --episode S01E01 --dry-run
xil sfx --episode S01E01 --gen-sfx
xil sfx --episode S01E01 --gen-music
xil sfx --episode S01E01 --gen-ambience
xil sfx --episode S01E01 --max-duration 5.0
xil sfx --episode S01E01 --local-only
xil sfx --episode S01E01 --gen-sfx
xil sfx --episode S01E01
```

- `--episode` or `--tag` (one required) derives `cast_<slug>_S01E01.json` and `sfx_<slug>_S01E01.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- Reads: parsed script JSON + SFX config + cast config (for episode tag)
- Outputs: shared assets to `SFX/`, episode stems to `stems/<slug>/<TAG>/`
- `--dry-run` shows EXISTS/CACHED/NEW status per stem with credit estimates
- `--gen-sfx`, `--gen-music`, `--gen-ambience` filter generation to the specified categories; omitting all three processes all categories
- `--dry-run` SUMMARY now shows per-category credit subtotals (MUSIC / AMBIENCE / SFX / silence)
- `--max-duration N` filters to effects ≤ N seconds (controls API credit spend)
- `--local-only` skips any effect not already present in `SFX/`; only CACHED assets and silence entries are placed, no API calls made
- `--sfx-backend elevenlabs` (default) selects the generation backend; it is the only remaining choice since #62
- 429 rate-limit errors are retried automatically up to 5 times with linear backoff (10s, 20s, 30s, 40s, 50s)
- Skips stems that already exist on disk

### CSV Annotation Utility

`XILU003_csv_sfx_join.py` — Joins a parsed episode CSV with the SFX JSON and cast JSON, producing an annotated review CSV with SFX prompt, duration, and cast metadata columns appended alongside each dialogue and direction entry.

```bash
xil csv-join --episode S02E03
xil csv-join --episode S02E03 --output my_review.csv
```

- `--episode` or `--tag` (one required) derives `parsed/parsed_<slug>_{TAG}.csv`, `sfx_<slug>_{TAG}.json`, and `cast_<slug>_{TAG}.json`
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- `--csv`, `--sfx`, `--cast` override individual input paths
- `--output` overrides the output CSV path (default: `parsed/annotated_<slug>_{TAG}.csv`)
- No API key required — read-only join utility

### Voice Sample Utility

`XILU004_sample_voices_T2S.py` — Generates a short TTS sample for each cast member to audition voice assignments.

```bash
xil sample --episode S02E03 --dry-run
xil sample --episode S02E03
xil sample --episode S02E03 --backend chatterbox
xil sample --episode S02E03 --backend gtts
xil sample --episode S02E03 --force
```

- `--episode` or `--tag` (one required) or `--cast PATH` to specify the cast config
- `--show` overrides the show name used for slug derivation (see Project Configuration)
- `--backend elevenlabs|gtts|chatterbox-turbo` (default: `elevenlabs`): selects TTS backend for sample generation (`chatterbox-turbo` renders the 19 native paralinguistic tags — see Chatterbox Turbo Paralinguistic Tags under Stage 2; uses `venv-chatterbox`, needs reference clips >5 s). `chatterbox` is a deprecated alias that warns and uses Turbo. Putting a tag in `--sample-text` (e.g. `"[sarcastic] I am {name}"`) is a quick way to audition a cue against a voice ref
- Default sample text: `"I am {name} not yo momma"`; override with `--sample-text` (use `{name}` placeholder)
- Output: `voice_samples/{TAG}/{backend}/{actor}.mp3` — backend subdirectory enables side-by-side comparison
- Skips members with `voice_id=TBD` (ElevenLabs only); `--force` regenerates existing samples
- `--chatterbox-python PATH`, `--voice-refs DIR`, `--device cuda|cpu` — Chatterbox Turbo options (same as `xil-produce`; `--device` auto-falls back to `cpu` when `cuda` is unavailable, so it's only needed to force `cpu` on a working GPU)
- Requires `ELEVENLABS_API_KEY` for `--backend elevenlabs`

### SFX Library Discovery

`XILU005_discover_SFX.py` — Lists and searches the local shared SFX asset library.

```bash
xil sfx-lib                    # local scan (default)
xil sfx-lib --local            # explicit local scan
xil sfx-lib --sfx-dir SFX/    # override local scan directory
xil sfx-lib --search "diner"   # filter by keyword
xil sfx-lib --json             # machine-readable output
xil sfx-lib --api              # attempt API history (not publicly accessible)
xil sfx-lib --api --all        # paginate full API history (default: most recent 100)
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
xil splice --episode S02E03 --insert-after 322 \
    --from-parsed parsed/parsed_the413_S02E02.json --from-seq-range 232-233 \
    --section post-interview --dry-run
xil splice --episode S02E03 --delete-seq-range 100-105 --dry-run
xil splice --episode S02E03 --insert-after 322 \
    --from-json new_entries.json
```

- `--episode` or `--tag` (one required) derives target parsed JSON path
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

### Logging

`log_config.py` writes **two formats from one logger** — the console stays human-readable, the file is machine-readable.

- **Console (stdout)** — unchanged: plain `INFO`, `[!]` for warnings, `[ERROR]`/`[CRITICAL]`, `[debug]`. The `run_banner()` bars print here as always.
- **File** — `logs/xil_v2_YYYY-MM-DD_<host>.log`, one record per line:

```text
2026-07-26T19:03:00-0400|RUN|hibirdy|produce|BEGIN argv="xil produce --episode S01E01 --backend chatterbox-turbo" pid=1234 ver=0.3.1 cwd=/mnt/cloudsy/xil-projects
2026-07-26T19:03:02-0400|INFO|hibirdy|produce|  > [006] adam via Chatterbox Turbo (282 chars)...
2026-07-26T19:05:22-0400|RUN|hibirdy|produce|END elapsed=142.3s
```

- Fields are `<iso-8601 ts>|<LEVEL>|<host>|<stage>|<message>`. **The message may contain `|`** — split off only the four leading fields.
- **One file per host.** The workspace is often a shared network mount, and appends are *not* atomic across clients on 9p/SMB/NFS — two machines sharing one file can interleave or lose lines. Each host owns `xil_v2_<date>_<host>.log`; the `host` field additionally keeps attribution visible when grepping or concatenating.
- `stage` comes from `sys.argv[0]`: both `xil-produce` and `xil produce` yield `produce`.
- Each invocation is bracketed by `RUN` records from `run_banner()` — the `BEGIN` line records the full command, so any block of output can be traced to what produced it.
- Whitespace-only records are dropped from the file (console spacing only); multi-line messages get one prefixed line each.
- A record can be routed to one sink via `logger.log(..., extra={"console": False})` or `{"file": False}` — used to keep the decorative bars on the terminal and the `BEGIN`/`END` records in the file.
- **v1 → v2**: pre-v2 logs were a bare stdout transcript named `xil_YYYY-MM-DD.log`. Run `python3 tools/migrate_logs_v1.py` (`-n` to preview) to rename them to `xil_v1_YYYY-MM-DD.log` so the format is unambiguous from the filename. Both formats are still parsed by `xil-stem-log` and `tools/xil_effort.py`.

### Stem Log Report

`XILU008_stem_log_report.py` — Parses daily pipeline log files into a chronological stem generation CSV. Useful for auditing what was generated, when, with which backend, and confirming SHA256 checksums.

```bash
xil-stem-log --episode S03E03
xil-stem-log --episode S03E03 --since 2026-04-01
xil-stem-log --slug the413
xil-stem-log --logs-dir logs/ --output stem_log_report.csv
xil-stem-log --episode S03E03 --audit
xil-stem-log --episode S03E03 --audit --audit-threshold 20
```

- `--episode TAG` (optional) filters records to a specific episode tag (e.g. `S03E03`); matched against `stem_path`
- `--slug SLUG` (optional) filters records to a specific show slug (e.g. `the413`); matched against `stem_path`
- `--logs-dir DIR` path to log directory (default: `logs/`)
- `--output PATH` output CSV path (default: `stem_log_report.csv`); use `-` for stdout
- `--since DATE` filter to logs on or after the given date (YYYY-MM-DD)
- `--show` print CSV to stdout (equivalent to `--output -`)
- `--audit` cross-references logged `char_count` values against the current parsed JSON and flags stems whose logged character count differs from the current text length by more than `--audit-threshold` percent (default: 10); useful for detecting stale stems after script edits
- `--audit-threshold N` percentage threshold for the `--audit` flag (default: 10)
- Parses both log formats — v2 `logs/xil_v2_YYYY-MM-DD.log` (structured; the `ts|LEVEL|stage|` prefix is stripped before matching) and v1 `logs/xil_YYYY-MM-DD.log` / `xil_v1_*` (bare stdout transcript); regex patterns match ElevenLabs, gTTS, Chatterbox, and Chatterbox Turbo generation lines
- State machine: generation line → saved line → SHA256 line → emits one record
- `run_index` groups stems by production run — from `run_banner`'s `RUN … BEGIN` record in v2 logs, or the `Phase 1: Generating` marker in v1 logs (never both, so runs aren't double-counted)
- Log files are ordered by the date in the filename, so v1 and v2 files interleave chronologically
- Output columns: `log_date`, `log_file`, `run_index`, `log_line`, `seq`, `speaker`, `backend`, `char_count`, `stem_path`, `stem_filename`, `sha256`
- No ElevenLabs API key required — reads local log files only

### Web Dashboard

`xil_gui.py` — Gradio browser dashboard that supplements the CLI. Nine tabs, in display order: **Setup** (initialize a new workspace / select active show, with content-type selector), **Project** (edit project.json), **Episodes** (workspace overview), **Run Stage** (launch pipeline stages with live log streaming), **Speakers** / **Cast Config** / **SFX Config** (edit the respective JSON configs), **Audio Preview** (browse and play stems), **Timeline** (interactive HTML timeline). Requires the `[gui]` optional extra.

```bash
pip install 'xil-pipeline[gui]'
xil-gui                        # opens http://localhost:7860
xil-gui --share                # generates a public ngrok URL for partner access (72h, no auth)
xil-gui --port 8080            # custom port
xil-gui --verbose              # detailed logs for the Timeline SFX dialog (open/save)
xil gui                        # via unified dispatcher
```

Episode detection checks both legacy root `cast_{slug}_{tag}.json` and normalized `configs/{slug}/cast_{tag}.json` locations, so both old and new workspaces show up.

### Workspace Migration

`XILU009_migrate_workspace.py` — moves pre-0.1.8 workspace files to the normalized layout in one idempotent pass.

```bash
xil migrate-workspace --dry-run    # preview what would move
xil migrate-workspace              # execute moves
```

- **Episodes tab**: workspace overview — all detected episodes with parse/stems/DAW/master status; episode dropdowns show `[Arc]  —  Title` next to the tag (read from cast config); Episodes table has a `Title [Arc]` column
- **Audio Preview tab**: episode + filter selector (all/dialogue/sfx/music/ambience), stem dropdown, in-browser MP3 playback via `gr.Audio`; stem labels enriched from parsed JSON when available
- **Run Stage tab**: select episode + stage (assemble/daw/master/produce/parse), dry-run checkbox (default on), extra flags field; live stdout streaming via generator + `demo.queue()`
- **Timeline tab**: embeds the `daw/{TAG}/{TAG}_timeline.html` iframe if generated; prompts `xil daw --timeline-html` when absent; double-clicking a MUSIC/SFX/AMBIENCE span opens a sound-profile editor modal (`GET /xil/get-sfx`, `POST /xil/update-sfx`) with a collapsed "Category defaults (`<layer>`)" section (`POST /xil/update-sfx-defaults`) for editing that layer's `defaults.<layer>_*` fallback values — both save paths journal to `configs/{slug}/sfx_{tag}_edits.jsonl` (`scope: "defaults"` for the latter) so edits survive config regeneration (see `xil sfx-restore`)
- `--share` uses Gradio's ngrok tunnel — open access, suitable for trusted collaborators during a session only
- `allowed_paths=[os.getcwd()]` enables Gradio to serve local MP3/WAV/HTML files
- Subprocess isolation: each stage run is a fresh `subprocess.Popen` so the GUI stays alive if a stage errors
- No ElevenLabs API key required for the GUI itself (stages may require it depending on flags)

## Developer/Maintainer Rules

Automated testing via Python and Bash serves as the fundamental mechanism for the Verification Loop. The project mandates that Claude must mention how it will verify its work before it begins any task.

Use tests for everything it implements:

- Determine which tests are appropriate; the model will then generate a test for every single feature it builds
- Test-Driven Development (TDD): A key best practice is implementing a verification-led technique where tests for a new feature are written first, followed by the actual code implementation

### Documentation Currency Rule

After executing any plan that changes pipeline behaviour, CLI flags, file formats, or module interfaces, **both** `CLAUDE.md` (root) and `docs/internals/pipeline.md` must be updated to reflect those changes **before committing**. This applies equally to Claude and human contributors. Specifically:

- New CLI flags or flag removals → update the relevant stage description in CLAUDE.md and the corresponding section/sequence diagram in pipeline.md
- New module fields or dataclass additions → update `mix_common.py` / `sfx_common.py` bullet points in CLAUDE.md
- New SFX config keys or behaviours → update the SFX Configuration section
- New pipeline stages or utilities → add a XILP/XILU entry under File Naming Convention and a stage section in pipeline.md
- Any behavioural change visible to operators → update the relevant stage bullets in CLAUDE.md

If a plan is large enough to have its own plan file, tick this as the final step before closing the plan.

### SFX source precedence

`source` is a **journaled** field (`SFX_EDIT_FIELDS`), so an asset assignment survives a rebuild from a fresh script `.md`. That has a precedence consequence worth knowing:

`create_sfx_config` writes the skeleton from the script's pipe-hints **first**, then replays `sfx_<tag>_edits.jsonl` **on top** — so **the journal beats the script hint**. Replay logs a WARNING naming both values whenever it overrides one; re-run `xil sfx-hydrate --force` to make the script win again. `xil sfx-match --apply` writes through the same journal, which is why an asset it finds survives the next rebuild.

This is deliberately the opposite of the rule for `play_volume_pct` (#49, script wins). Durability across a script rebuild was the goal for `source`; immediate script authority was the goal for attributes. The warning is what stops the difference being silent.

### Docs Site Navigation

`mkdocs.yml` has no `nav:` — `awesome-pages` builds it from `.pages` files and otherwise sorts by ASCII filename. Two knobs, both in the repo:

- `docs/.pages` — top-level order. The trailing `...` catches unlisted pages, so a new doc never vanishes from the nav.
- `DOC_CATEGORIES` in `docs/build_docs.py` — maps a **repo-root** `.md` to `configuration` / `guides` / `internals`. Adding a categorized doc is one dict entry; only the symlink is placed in the section, the source file stays at the repo root so existing path references keep working. Unlisted files land flat in `docs/`.

Pages authored directly under `docs/` just live in the right folder — `should_copy_markdown_file` skips `docs`, so the build never touches them. `clean_generated_docs` removes only *symlinks* from category folders (an `rmtree` would delete committed pages) and sweeps stale root symlinks for any newly categorized file.

Cross-page links must resolve in the **docs** layout, not the repo layout. `strict: true` fails the build on a broken internal link — that is the safety net when moving a page between sections.

### Docstring Standard

Two-tier rule — pick the tier by function complexity:

**Tier 1 — one-liner.** Use for boilerplate entry points and small helpers whose signature makes the purpose obvious:

```python
def get_parser() -> argparse.ArgumentParser:
    """Return the argument parser for xil-parse."""

def main() -> None:
    """CLI entry point for the script parser."""

def _sfx_manifest_path(stems_dir: str) -> str:
    """Return the SFX stem manifest JSON path inside *stems_dir*."""
```

**Tier 2 — Google-style.** Use for public functions with non-trivial parameters or structured return values. Include a one-line summary, `Args:` block, and `Returns:` block. Show the return structure inline when it is a dict or complex type:

```python
def scan_script(
    lines: list[str],
    known_speakers: list[str] | None = None,
    speaker_keys: dict[str, str] | None = None,
) -> dict:
    """Scan normalized *lines* and classify every ALL-CAPS candidate.

    Args:
        lines: Normalized script lines.
        known_speakers: Ordered list of speaker display names (longest-first).
            Defaults to the module-level speakers from XILP001.
        speaker_keys: Mapping from display names to normalized keys.
            Defaults to the module-level speakers from XILP001.

    Returns a dict::

        {
            "sections":     [{"text": str, "slug": str, "line": int}, ...],
            "speakers":     {key: {"display": str, "count": int, "lines": [int, ...]}, ...},
            "unrecognized": [{"text": str, "lines": [int, ...]}, ...],
        }
    """
```

Private helpers (`_foo`) use Tier 1 unless their parameters genuinely need explanation. Never write multi-paragraph docstrings or repeat what the type annotations already say.

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

Unix man pages for all 37 CLI commands are pre-generated and committed to `man/man1/`. They are installed automatically when the package is built into a wheel and installed via pip.

**Regenerating after CLI changes** (run whenever flags or descriptions change):

```bash
pip install -e ".[dev]"      # includes argparse-manpage
python docs/build_man.py  # regenerate all 36 argparse-based pages
# xil.1 is hand-crafted — edit man/man1/xil.1 directly when the dispatcher changes
```

Regenerate a single page: `python docs/build_man.py xil-parse`

Always commit the regenerated `.1` files alongside any CLI flag change. The `get_parser()` function in each module (extracted from `main()`) is what `build_man.py` calls to obtain the parser — keep it in sync with any `add_argument` changes.

**Drift protection**: `python docs/build_man.py --check` exits 1 if any committed page is stale relative to its parser (the comparison ignores the `.TH` date field, so it is stable across days). CI runs this check on Linux after the lint step, and `tests/test_man_pages.py` cross-checks `pyproject.toml [project.scripts]` against the `COMMANDS` registry in `build_man.py` — a new CLI entry point without man-page registration fails the suite. The SEE ALSO block on generated pages is derived from `COMMANDS` automatically. Every new console script must be added to `COMMANDS` in `docs/build_man.py` (or `HAND_CRAFTED` for manually maintained pages like `xil.1`).

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
- `stems/<slug>/<TAG>/` — Individual voice/SFX audio files per episode (generated, expensive to recreate)
- `SFX/` — Shared SFX asset library (generated once, reused across episodes); cues-sheet assets named by asset ID (e.g. `sfx-boots-stamp-01.mp3`)
- `daw/<TAG>/` — Per-layer WAV exports for DAW mixing (generated by XILP005)
- `venv/` — Python virtualenv (do not commit)
