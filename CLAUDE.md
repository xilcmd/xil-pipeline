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
  models.py                # Pydantic data models, slug/path resolution
  mix_common.py            # Shared mixing utilities
  sfx_common.py            # SFX library management, ID3 tagging
  sfx_backends.py          # Pluggable SFX backends (ElevenLabs API / local AudioLDM 2 / local Stable Audio Open)
  timeline_viz.py          # Timeline visualization
  xil_init.py              # Project scaffolding (xil-init command, --type aware)
  chatterbox_worker.py     # Persistent Chatterbox TTS worker (venv-chatterbox subprocess)
  whisper_worker.py        # Persistent Faster-Whisper STT worker (venv-whisper subprocess)
  audioldm2_worker.py      # Persistent AudioLDM 2 SFX/music/ambience worker (venv-audioldm2 subprocess)
  stableaudio_worker.py    # Persistent Stable Audio Open SFX/music/ambience worker (shares venv-audioldm2)
  XILP000_*.py … XILP012_*.py   # Pipeline stages
  XILU001_*.py … XILU018_*.py   # Utility scripts
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
- **Optional local SFX venv** (`venv-audioldm2/`): a separate virtualenv for the AudioLDM 2 Large
  sound-effect/music/ambience backend (`--sfx-backend audioldm2`). Like `venv-chatterbox/`, it is
  auto-detected at the workspace or repo root (or located via `XIL_CODEROOT` — see Code Root) and
  never installed into the main package. The same venv also serves the Stable Audio Open backend
  (`--sfx-backend stableaudio`) — `StableAudioPipeline` ships in the same diffusers install, no
  extra packages needed. Stable Audio Open's weights are **license-gated on HuggingFace**: accept
  the license at <https://huggingface.co/stabilityai/stable-audio-open-1.0> while logged in, then
  authenticate the venv once via `HF_TOKEN` env var or `huggingface-cli login`. Setup
  (validated on Python 3.13 + NVIDIA driver CUDA 12.6):

  ```bash
  python -m venv venv-audioldm2
  venv-audioldm2/bin/pip install --upgrade pip
  # GPU torch built for CUDA 12.4 (the default PyPI wheel targets CUDA 13 and is too new for a 12.6 driver → CPU-only):
  venv-audioldm2/bin/pip install 'torch==2.6.0' --index-url https://download.pytorch.org/whl/cu124
  venv-audioldm2/bin/pip install diffusers 'transformers==4.49.0' accelerate scipy soundfile pydub audioop-lts torchsde
  ```

  Version pins that matter:
  - **`transformers==4.49.0`** — newer 5.x breaks the diffusers AudioLDM2 pipeline (loads the
    language model as `GPT2Model` instead of `GPT2LMHeadModel` → `_update_model_kwargs_for_generation`
    AttributeError); older 4.44.x has no Python 3.13 `tokenizers` wheel. 4.49.0 is the working middle.
  - **`torch==2.6.0` from the cu124 index** — the default `pip install torch` pulls a CUDA 13 build
    that a 12.6 driver rejects (`torch.cuda.is_available()` → False → silent CPU fallback). cu124
    matches `venv-chatterbox/`.
  - **`audioop-lts`** — required on Python 3.13+, where pydub's `audioop` stdlib dependency was removed.

  AudioLDM 2 Large needs ~6–8 GB VRAM in fp16; the worker auto-falls back to CPU when CUDA is
  unavailable (CPU works but is minutes-per-clip at the default 200 steps). Stable Audio Open
  behaves the same (fp16 on CUDA, CPU fallback) and generates 44.1 kHz stereo up to 47.55 s
  per clip.

## Workspace Root (`XIL_PROJECTROOT`)

All pipeline commands resolve content paths relative to the **workspace root**. By default this is the current working directory (existing behaviour). Set `XIL_PROJECTROOT` to point at a content directory from anywhere:

```bash
export XIL_PROJECTROOT=/path/to/xil-content
xil produce --episode S04E02   # works from any directory
xil-gui                        # GUI shows correct workspace
```

Resolution order for `get_workspace_root()` (in `models.py`):

1. `XIL_PROJECTROOT` environment variable (absolute path, tilde-expanded)
2. `Path.cwd()` — current working directory (no env var set)

`xil-init` with no directory argument scaffolds into `XIL_PROJECTROOT` when set, otherwise into the current directory.

`logs/`, `configs/`, `parsed/`, `stems/`, `SFX/`, `daw/`, `masters/`, `cues/`, `scripts/`, `posts/` all resolve under the workspace root. This enables a clean separation of the installed software from user content — install `xil-pipeline` once via `pip`, point `XIL_PROJECTROOT` at a content-only directory.

## Code Root (`XIL_CODEROOT`)

The **code root** is where the optional local-model virtualenvs live —
`venv-chatterbox/` (Chatterbox TTS), `venv-whisper/` (Faster-Whisper STT), and
`venv-audioldm2/` (AudioLDM 2 SFX/music/ambience). These carry heavy ML dependencies and
are **never installed into the main package**; each pipeline command auto-detects them at
run time.

`XIL_CODEROOT` decouples *where the venvs live* from `XIL_PROJECTROOT` (*where the show
content lives*). When you install `xil-pipeline` once and point `XIL_PROJECTROOT` at a
separate content-only directory, the model venvs stay next to the code — set
`XIL_CODEROOT` so the pipeline finds them regardless of the active workspace:

```bash
export XIL_CODEROOT=/path/to/xil-pipeline     # holds venv-chatterbox/, venv-whisper/, venv-audioldm2/
export XIL_PROJECTROOT=/path/to/xil-content   # holds scripts/, configs/, stems/, …
xil produce --episode S04E02 --backend chatterbox --sfx-backend audioldm2
```

Resolution order for each model venv's `python3` (`resolve_venv_python()` in `models.py`):

1. The explicit per-command flag — `--chatterbox-python` / `--whisper-python` /
   `--audioldm2-python`. Wins when provided.
2. `XIL_CODEROOT` — when set, `$XIL_CODEROOT/<venv-name>/bin/python3` is used
   **exclusively**: it overrides auto-detection entirely and there is **no fallback**
   (the command errors if the interpreter is absent there).
3. Auto-detect — `<workspace>/<venv-name>/bin/python3`, then the repo root next to the
   running install.

`XIL_CODEROOT` is optional; when unset, detection falls back to the workspace-root /
repo-root search (existing behaviour). It is consulted only for locating these venvs —
all content paths still resolve under `XIL_PROJECTROOT`.

## Project Configuration

`project.json` at the workspace root declares the show name and optional season title used across the pipeline:

```json
{
    "show": "THE 413",
    "season": 3,
    "season_title": "The Holiday Shift"
}
```

All scripts accept a `--show` CLI flag to override the show name. Resolution order: `--show` arg > `project.json` > hardcoded fallback `"sample"`.

The `season_title` key in `project.json` is the workspace-level default for the season/arc title. When a script header contains `Arc: "…"`, that value takes precedence; when absent, `project.json` `season_title` fills in. Resolution order: script header `Arc:` > `project.json` `season_title` > `None`. The `{season_title}` placeholder in PREAMBLE/POSTAMBLE script text resolves from this value.

The `season` key in `project.json` is the workspace-level default season number. When a script header contains `Season N:`, that value takes precedence; when absent, `project.json` `season` fills in. Resolution order: script header `Season N:` > `project.json` `season` > `None`.

### Content Type

`project.json` supports an optional `"type"` field (default: `"podcast"`):

```json
{
  "show": "THE 413",
  "type": "drama",
  "season": 3,
  "season_title": "The Architect"
}
```

Four types are supported: **podcast**, **audiobook**, **drama**, **special**. The type drives:
- Section map used by `xil-parse` (e.g. `CHAPTER ONE` for audiobook, `PROLOGUE/ACT ONE` for drama)
- Default `gap_ms` for `xil-assemble` / `xil-daw` (400 ms audiobook, 800 ms drama, 600 ms others)
- Sample script, speakers.json, and subdirectory layout generated by `xil-init --type`
- Audiobook type adds `"tag_format": "V{volume:02d}C{chapter:02d}"` to `project.json` for `V01C01`-style tags

`project.json` without a `type` field defaults to `"podcast"` with no change in behavior.

### Workspace Layout (0.1.8+)

New workspaces created with `xil-init` use a normalized directory layout:

```
configs/{slug}/speakers.json        ← was speakers.json at root
configs/{slug}/cast_{tag}.json      ← was cast_{slug}_{tag}.json at root
configs/{slug}/sfx_{tag}.json       ← was sfx_{slug}_{tag}.json at root
parsed/{slug}/parsed_{tag}.json     ← was parsed/parsed_{slug}_{tag}.json
daw/{slug}/{tag}/                   ← was daw/{tag}/
masters/{slug}/{tag}_master.mp3     ← was masters/{slug}_{tag}_master.mp3
cues/{slug}/cues_{tag}.md           ← was cues/cues_{slug}_{tag}.md
stems/{slug}/{tag}/                 ← unchanged
```

Existing pre-0.1.8 workspaces continue to work automatically — `derive_paths()` detects the legacy layout (cast config at root) and returns legacy paths. Run `xil migrate-workspace` to move files to the new layout.

File paths are derived dynamically via `derive_paths(slug, tag)`. The slug is the show name lowercased with all non-alphanumeric characters removed (e.g., `"THE 413"` → `"the413"`, `"Night Owls"` → `"nightowls"`).

## Project Scaffolding

`xil-init` scaffolds a new show workspace with sample content:

```bash
xil-init my-show --show "Night Owls"
xil-init my-show --show "Night Owls" --type drama
xil-init my-show --show "Night Owls" --type audiobook
```

The `--type` flag (choices: `podcast` [default], `audiobook`, `drama`, `special`) selects:
- A type-specific sample script with appropriate sections and cast
- A type-specific `speakers.json` (single narrator for audiobook, full cast for drama, etc.)
- `project.json` `"type"` field + `"tag_format"` for audiobook (`V01C01`)

Creates: `project.json`, `speakers.json`, `scripts/sample_{tag}.md`, and empty subdirectories in the normalized layout. The sample script exercises all parser features so the user can immediately run `xil-scan` and `xil-parse --dry-run`.

## Speaker Configuration

`configs/{slug}/speakers.json` provides optional enrichment (voice_id, pan, filter, role, etc.) for the parser.  Since 0.2.0 it is **not required** for speaker recognition — characters declared in the script's `CAST:` block are always recognized.

```json
[
    {"display": "ADAM", "key": "adam"},
    {"display": "MR. PATTERSON", "key": "mr_patterson"},
    {"display": "FILM AUDIO (MARGARET'S VOICE)", "key": "film_audio"}
]
```

Recognition order (merged, all sources combined): `CAST:` block entries from the script itself → `configs/{slug}/speakers.json` (JSON key always wins over auto-derived key) → built-in defaults (only when neither CAST entries nor JSON file exist). The list is auto-sorted longest-first for compound-name matching. Both `xil-scan` (XILP000) and `xil-parse` (XILP001) accept the `--speakers` flag.

### CAST: block format

Every script should declare its cast in the header using dialogue-label names (the ALL-CAPS prefix used in dialogue lines), with optional `—` role descriptions:

```
CAST:
* ADAM — Adam Santos, Host
* MR. PATTERSON — Recurring Caller
* FILM AUDIO (MARGARET'S VOICE) — Archive audio
```

Characters listed here are automatically recognized during parsing even if absent from `speakers.json`. For new series or one-off characters added mid-series, adding them to the `CAST:` block is sufficient to parse correctly; run `xil scan --harvest-cast` afterwards to propagate them to `speakers.json` for enrichment.

## Pre-Flight Script Scanner

`XILP000_script_scanner.py` — Scans a raw markdown script and reports recognized/unrecognized speakers and sections **before** running XILP001. Use this whenever onboarding a new script to catch missing speakers or `SECTION_MAP` entries early.

```bash
xil scan "scripts/<script>.md"
xil scan "scripts/<script>.md" --json
xil scan --harvest-cast                  # union CAST: blocks across all scripts → speakers.json diff
xil scan --harvest-cast --yes            # auto-add new entries to speakers.json
xil scan --backfill-cast --dry-run       # preview CAST: block insertion for old scripts
xil scan --backfill-cast --yes           # write CAST: blocks to all scripts that lack one
```

- No `--episode` flag required for single-script scan — reads only the script file, no side effects
- Exit code 0 = all recognized (safe to run XILP001); exit code 1 = action needed
- Imports XILP001's pure functions directly — no duplicated logic
- `--json` outputs machine-readable scan results
- `--speakers PATH` overrides the speaker list (see Speaker Configuration)
- `--harvest-cast` scans all `scripts/*.md`, collects CAST: entries, and reports characters missing from speakers.json; `--yes` adds them automatically; `--scripts-dir DIR` overrides the default scripts directory
- `--backfill-cast` adds CAST: blocks to scripts that don't have one, inferring speakers from existing parsed JSON (most reliable) or body scan against speakers.json; `--yes` writes files, default is dry-run; `--scripts-dir DIR` overrides the default scripts directory

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
- Supports `--backend elevenlabs|gtts|chatterbox` (default: `elevenlabs`): `gtts` routes all dialogue voice stems through Google Translate TTS at no cost — flat single voice, useful for duration checks; `chatterbox` uses local Chatterbox TTS with per-character zero-shot voice cloning from `voice_refs/<key>.wav` reference clips — near-production quality, GPU-accelerated, free after setup; SFX/music/ambience generation is unaffected by `--backend`; eleven_v3 inline tags are stripped before gTTS/Chatterbox calls
- `--backend chatterbox` options: `--chatterbox-python PATH` (default: auto-detect `./venv-chatterbox/bin/python3`); `--voice-refs DIR` (default: `voice_refs/`) for per-speaker `.wav` reference clips; `--exaggeration FLOAT` emotion level 0.0–1.0 (default: 0.5); missing voice refs fall back to Chatterbox default voice
- Supports `--sfx-backend elevenlabs|audioldm2|stableaudio` (default: `elevenlabs`) — an **independent** backend axis for SFX/MUSIC/AMBIENCE generation, orthogonal to the dialogue `--backend`. `audioldm2` runs a local AudioLDM 2 Large diffusion model (`venv-audioldm2/`, via `audioldm2_worker.py`) — free, GPU-accelerated, no API credits. `stableaudio` runs a local Stable Audio Open 1.0 model (`stableaudio_worker.py`, **sharing the same `venv-audioldm2/`** — `StableAudioPipeline` ships in the installed diffusers) — 44.1 kHz stereo, ≤47.55 s per clip, HF license-gated weights (one-time accept + `HF_TOKEN`/`huggingface-cli login`). Model-generated assets are stored backend-tagged as `SFX/<slug>.audioldm2.mp3` / `SFX/<slug>.stableaudio.mp3` so they coexist with ElevenLabs assets and switching backends does not silently reuse the wrong audio; `silence`/`source` assets stay backend-independent (plain name). ElevenLabs API key is only required when `--sfx-backend elevenlabs` and SFX generation is requested (or the dialogue backend is elevenlabs)
- `--sfx-backend audioldm2` options: `--audioldm2-python PATH` (default: auto-detect `./venv-audioldm2/bin/python3`); `--audioldm2-guidance FLOAT` guidance scale / prompt adherence (default: 3.5); `--audioldm2-steps INT` diffusion inference steps (default: 200); `--audioldm2-negative-prompt STR` (default: `"low quality, noise"`). AudioLDM 2 emits 16 kHz audio and quantises `duration_seconds` to its latent rate, so very short SFX may run slightly long
- `--sfx-backend stableaudio` options: `--stableaudio-python PATH` (default: auto-detect the shared `venv-audioldm2` Python); `--stableaudio-guidance FLOAT` (default: 7.0); `--stableaudio-steps INT` (default: 100); `--stableaudio-negative-prompt STR` (default: `"low quality, average quality"`); `--stableaudio-seed INT` reproducibility seed (default: nondeterministic). Durations beyond the model max (47.55 s) are clamped in the worker with a warning
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
- Applies per-speaker effects (pan, audio filters) from cast config; `filter` field accepts `false`/`null` (none), `true`/`"phone"` (phone filter), `"vintage"` (vintage filter), or a comma-separated combination such as `"vintage,phone"`
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
- `XILU013_*` — SFX config hydrator (writes pipe-hint `source` fields from parsed JSON into the SFX config)
- `XILU014_*` — episode summary CSV generator (scans all parsed JSONs → one-row-per-episode CSV with dialogue_lines, words, tts_chars)
- `XILU015_*` — stem verifier (scans a stems folder → JSON report with filename, seq/scene/speaker parse, size, duration, bitrate, SHA-256, and optional Faster-Whisper transcription; requires `venv-whisper/`)
- `XILU016_*` — stem compare (cross-references a `stem_verify` Whisper transcript report against the parsed script; flags garbled/silent/missing dialogue stems using `difflib.SequenceMatcher`; no extra dependencies)
- `XILU017_*` — remove show (delete all workspace files for a given show slug; `--dry-run` safe; `--yes` skips confirmation prompt; `--include-scripts` also removes `scripts/*_{slug}_*.md` files; `SFX/` and `logs/` are never touched; accepts show name or slug; clears `.active_show` when it points to the removed show)
- `XILU018_*` — remove episode (delete all workspace artifacts for a single episode tag — cast/sfx configs, parsed JSON/CSV, stems dir, DAW dir, master MP3s, cues, posts, voice_samples; `--dry-run` safe; `--yes` skips confirmation prompt; source script in `scripts/`, shared `SFX/`, and `logs/` are never touched; handles both normalized and legacy layouts)
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
- `mix_common.py` — shared mixing utilities (timeline, layer builders, fast label helpers) used by XILP003 and XILP005; `StemPlan.scene` (str|None): scene label from parsed JSON, used for scene-scoped vintage filter; `StemPlan.loop` field: `True` (default) tiles audio, `False` plays once up to scene boundary; `StemPlan.pre_trimmed` flag: skips play_duration trim for source-based stems already trimmed at copy time; `StemPlan.volume_percentage` (float|None): volume as a percentage (100 = unity, None = no change); `StemPlan.ramp_in_seconds` / `StemPlan.ramp_out_seconds`: fade durations in seconds (None = no fade); `_resolve_audio_params()` resolves volume/ramp from per-effect config or category defaults for MUSIC, AMBIENCE, SFX, and BEAT direction types; `volume_percentage`, `ramp_in_seconds`, and `ramp_out_seconds` each fall back to the global key when no category-specific key exists (e.g. SFX/MUSIC when `sfx_volume_percentage`/`music_ramp_in_seconds` are absent from the config defaults); `collect_stem_plans()` skips stale stems (header entries, type mismatch, speaker mismatch), deduplicates by seq number, and injects synthetic stop-marker `StemPlan` entries (filepath="") for `AMBIENCE: STOP` and `AMBIENCE: * FADES OUT` directives found in the entries index; `build_sfx_layer()` and `build_foreground()` apply `volume_percentage` to SFX/BEAT stems; `build_ambience_layer()` skips corrupt or unreadable stem files with a warning rather than crashing; `apply_vintage_filter()` applies a 1960s-era HF roll-off + 1 dB reduction; `_apply_speaker_filters(segment, filter_val)` resolves the cast config `filter` string and applies the named filter chain (`false`/`None` = none, `true`/`"phone"` = phone, `"vintage"` = vintage, `"vintage,phone"` = both); `_vf_engaged_seqs(stem_plans)` returns the set of dialogue seq numbers that fall within a `VINTAGE FILTER: ENGAGES`…`VINTAGE FILTER: DISENGAGES` span — used by `build_foreground()` and `build_dialogue_layer()` to apply the vintage EQ to dialogue within those spans (script-direction spans take precedence over `vintage_scenes` list fallback)
- `sfx_common.py` — shared SFX library management, ID3 tagging (`tag_mp3`, `tag_wav`), effect generation; `ensure_shared_sfx()` / `generate_sfx()` accept a `backend: SfxBackend` (defaults to an ElevenLabs backend wrapping `client`) and delegate all model generation to it — the streaming-download + 429/5xx/network retry loop now lives in `ElevenLabsSfxBackend`, not inline; `shared_sfx_path(sfx_dir, effect_key, backend="elevenlabs")` returns backend-tagged paths for non-default backends; `load_sfx_entries()` accepts `direction_types` filter set, returns `direction_type` field in each entry dict, skips entries with `duration_seconds=0.0`; `dry_run_sfx()` accepts `backend_name` — matches assets against the backend-tagged path and reports local backends as free instead of API credits; `tag_mp3()` accepts optional `cover_art_path` — when supplied and file exists, reads the image and embeds an APIC front-cover frame (PNG or JPEG detected by extension) in the same `tags.save()` call as other ID3 fields
- `sfx_backends.py` — pluggable SFX/music/ambience generation backends behind the `SfxBackend` protocol (`generate_to(out_path, prompt, duration_seconds, prompt_influence)` + `close()`); `ElevenLabsSfxBackend` wraps `client.text_to_sound_effects.convert` (streaming, atomic rename, retry on 429/5xx/network); `_DiffusionWorkerClient` is the shared subprocess bridge for local diffusion workers (JSON-over-stdio, same pattern as `_ChatterboxClient`; subclasses set `_WORKER`/`_LABEL`/`_READY_HINT` and may override `_request_extras()`); `AudioLDM2SfxBackend` drives `_AudioLDM2Client` (`audioldm2_worker.py`), `StableAudioSfxBackend` drives `_StableAudioClient` (`stableaudio_worker.py`, adds a `seed` request field) — both workers run in `venv-audioldm2/`; `make_sfx_backend(name, client=None, *, audioldm2_python=None, stableaudio_python=None, guidance=3.5, steps=200, negative_prompt=…, seed=None, device="cuda")` is the factory, auto-detecting the venv-audioldm2 Python (workspace root → repo root). AudioLDM 2 maps `duration_seconds`→`audio_length_in_s`; Stable Audio maps it to `audio_end_in_s`; both use `guidance_scale` in place of `prompt_influence`
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

### Shared SFX Library

Each unique sound effect is generated **once** into the `SFX/` directory as a shared asset (e.g. `SFX/beat.mp3`, `SFX/sfx_phone-buzzing.mp3`). Episode stems in `stems/<slug>/<TAG>/` are copies of these shared assets with sequence-numbered filenames. This avoids regenerating the same effect for repeated uses (e.g. BEAT appears 26 times in S01E01). See `docs/sfx-reuse-guide.md` for a workflow guide on maximizing SFX reuse and minimizing API credit spend.

- Shared asset naming: `slugify_effect_key()` in `sfx_common.py` converts direction text to filesystem-safe slugs; `shared_sfx_path(sfx_dir, effect_key, backend)` appends a `.<backend>` infix for non-ElevenLabs backends (e.g. `SFX/sfx_door-opens.audioldm2.mp3`), keeping the plain name for `elevenlabs` so existing caches still hit. Only model-generated `type: sfx` assets are backend-tagged; `silence`/`source` assets stay plain
- `--dry-run` shows three statuses: `EXISTS` (episode stem on disk), `CACHED` (shared asset exists, will be copied), `NEW` (needs generation). For `--sfx-backend audioldm2`, NEW generation is reported as **local (free)** instead of an API credit estimate
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
xil sfx --episode S01E01 --sfx-backend audioldm2 --gen-sfx
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
- `--sfx-backend elevenlabs|audioldm2|stableaudio` (default: `elevenlabs`) selects the generation backend; `--audioldm2-python`, `--audioldm2-guidance`, `--audioldm2-steps`, `--audioldm2-negative-prompt` configure AudioLDM 2, and `--stableaudio-python`, `--stableaudio-guidance`, `--stableaudio-steps`, `--stableaudio-negative-prompt`, `--stableaudio-seed` configure Stable Audio Open (same semantics as `xil-produce`). ElevenLabs API key is only required for `--sfx-backend elevenlabs`
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
- `--backend elevenlabs|gtts|chatterbox` (default: `elevenlabs`): selects TTS backend for sample generation
- Default sample text: `"I am {name} not yo momma"`; override with `--sample-text` (use `{name}` placeholder)
- Output: `voice_samples/{TAG}/{backend}/{actor}.mp3` — backend subdirectory enables side-by-side comparison
- Skips members with `voice_id=TBD` (ElevenLabs only); `--force` regenerates existing samples
- `--chatterbox-python PATH`, `--voice-refs DIR`, `--exaggeration FLOAT` — Chatterbox-specific options (same as `xil-produce`)
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
- Parses `logs/xil_YYYY-MM-DD.log` files; three regex patterns match ElevenLabs, gTTS, and Chatterbox generation lines
- State machine: generation line → saved line → SHA256 line → emits one record
- `run_index` counter increments per `Phase 1: Generating` marker, grouping stems by production run
- Output columns: `log_date`, `log_file`, `run_index`, `log_line`, `seq`, `speaker`, `backend`, `char_count`, `stem_path`, `stem_filename`, `sha256`
- No ElevenLabs API key required — reads local log files only

### Web Dashboard

`xil_gui.py` — Gradio browser dashboard that supplements the CLI. Nine tabs, in display order: **Setup** (initialize a new workspace / select active show, with content-type selector), **Project** (edit project.json), **Episodes** (workspace overview), **Run Stage** (launch pipeline stages with live log streaming), **Speakers** / **Cast Config** / **SFX Config** (edit the respective JSON configs), **Audio Preview** (browse and play stems), **Timeline** (interactive HTML timeline). Requires the `[gui]` optional extra.

```bash
pip install 'xil-pipeline[gui]'
xil-gui                        # opens http://localhost:7860
xil-gui --share                # generates a public ngrok URL for partner access (72h, no auth)
xil-gui --port 8080            # custom port
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
- **Timeline tab**: embeds the `daw/{TAG}/{TAG}_timeline.html` iframe if generated; prompts `xil daw --timeline-html` when absent
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

After executing any plan that changes pipeline behaviour, CLI flags, file formats, or module interfaces, **both** `CLAUDE.md` (root) and `docs/pipeline.md` must be updated to reflect those changes **before committing**. This applies equally to Claude and human contributors. Specifically:

- New CLI flags or flag removals → update the relevant stage description in CLAUDE.md and the corresponding section/sequence diagram in pipeline.md
- New module fields or dataclass additions → update `mix_common.py` / `sfx_common.py` bullet points in CLAUDE.md
- New SFX config keys or behaviours → update the SFX Configuration section
- New pipeline stages or utilities → add a XILP/XILU entry under File Naming Convention and a stage section in pipeline.md
- Any behavioural change visible to operators → update the relevant stage bullets in CLAUDE.md

If a plan is large enough to have its own plan file, tick this as the final step before closing the plan.

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
