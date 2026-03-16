# XILP Pipeline Diagrams

Documentation of the six-stage automated podcast production pipeline for **THE 413**, including the cues sheet ingester pre-processing step.

---

## 1. End-to-End Overview

```mermaid
flowchart TD
    S["`📄 scripts/*.md
    Production script markdown`"]
    C["`📋 cast_the413_S01E01.json
    Voice ID + pan + filter per character`"]
    P1["XILP001_script_parser.py"]
    J["`📦 parsed/parsed_the413_S01E01.json
    127 dialogue entries + stats`"]

    CQ["`📋 cues/*.md
    Sound cues & music prompts`"]
    P6["XILP006_the413_cues_ingester.py"]
    SFXCFG["`📋 sfx_the413_S01E01.json
    SFX config (prompts + durations)`"]
    SFXLIB["`🎵 SFX/*.mp3
    Shared SFX asset library`"]
    MNFST6["`📦 cues/cues_manifest_*.json
    Structured asset catalog`"]
    DRY6["`--dry-run
    Audit report, no API calls
    Manifest always written`"]

    P2["XILP002_the413_producer.py"]
    P3["XILP003_the413_audio_assembly.py"]
    DRY["`--dry-run
    Preview lines + TTS cost
    No API calls`"]
    ST["`🎙️ stems/S01E01/*.mp3
    001_cold-open_adam.mp3 …`"]
    OUT["🎧 the413_S01E01_master.mp3"]
    MIX["mix_common.py"]

    S --> P1 --> J
    CQ --> P6
    P6 --> MNFST6
    P6 -->|"--generate"| SFXLIB
    P6 -->|"--enrich-sfx-config"| SFXCFG
    P6 -->|"--dry-run"| DRY6
    SFXCFG --> P2
    SFXLIB --> ST

    XILU004["XILU004_sample_voices_T2S.py"]
    VSAMPLES["`🎙️ voice_samples/<TAG>/
    <actor>.mp3 — audition samples`"]
    C --> XILU004
    XILU004 --> VSAMPLES

    XILU005["XILU005_discover_SFX.py"]
    SFXLIB --> XILU005

    C --> P2
    J --> P2
    P2 -->|"--dry-run"| DRY
    P2 --> ST
    C --> P3
    ST --> P3
    J --> P3
    MIX --> P3
    P3 --> OUT

    P4["XILP004_the413_studio_onboard.py"]
    STUDIO["`🎬 ElevenLabs Studio Project
    Chapters with voice-tagged nodes`"]
    DRY4["`--dry-run
    Preview chapters + voice map
    No API calls`"]

    J --> P4
    C --> P4
    P4 -->|"--dry-run"| DRY4
    P4 --> STUDIO

    P5["XILP005_the413_daw_export.py"]
    VIZ["timeline_viz.py"]
    DAW["`🎚️ daw/S01E01/
    layer_dialogue.wav + labels
    layer_ambience.wav + labels
    layer_music.wav + labels
    layer_sfx.wav + labels
    (ID3 metadata tagged)`"]
    DRY5["`--dry-run
    Show stem counts + paths
    No files written`"]
    MACRO["`--macro → Audacity macro
    THE413_S01E01.txt
    (WAV import only)`"]
    TL5["`--timeline
    ASCII timeline to stdout`"]
    TLHTML5["`--timeline-html
    S01E01_timeline.html
    (interactive, self-contained)`"]

    ST --> P5
    J --> P5
    C --> P5
    MIX --> P5
    VIZ --> P5
    P5 -->|"--dry-run"| DRY5
    P5 --> DAW
    P5 -->|"--macro"| MACRO
    P5 -->|"--timeline"| TL5
    P5 -->|"--timeline-html"| TLHTML5
```

---

## 2. XILP001 — Script Parser Internals

```mermaid
flowchart TD
    IN["📄 Production script .md"]
    ESC["`strip_markdown_escapes()
    Removes backslash escapes: bracket, equals, period`"]
    FMT["`strip_markdown_formatting()
    Removes ## headings, **bold**, trailing breaks`"]
    LINES["Split into lines"]
    SKIP["`Skip CAST section
    Skip title line
    Skip === / --- dividers`"]

    LINES --> SKIP --> LOOP

    subgraph LOOP["Line-by-line state machine"]
        direction TB
        PEND{"`pending_speaker?
        multi-line dialogue`"}
        PDIR["`(direction) line
        update pending direction`"]
        PTXT["`Spoken text line
        create dialogue entry`"]
        CHK{"Classify line"}
        SEC["`Section header
        COLD OPEN / OPENING CREDITS / ACT ONE
        update current_section`"]
        SCN["`Scene header
        SCENE N:
        update current_scene`"]
        DIR["`Stage direction
        SFX / MUSIC / AMBIENCE / BEAT
        direction entry`"]
        DLG["`SPEAKER text
        dialogue entry (single-line)
        or set pending_speaker (multi-line)`"]
        CONT["`Bare continuation text
        append to previous dialogue
        filter standalone (parentheticals)`"]
        STOP["`END OF EPISODE
        END OF PRODUCTION SCRIPT
        or PRODUCTION NOTES — break`"]

        PEND -->|"(dir)"| PDIR
        PEND -->|text| PTXT
        CHK -->|section header| SEC
        CHK -->|scene header| SCN
        CHK -->|bracket line| DIR
        CHK -->|known speaker| DLG
        CHK -->|bare text| CONT
        CHK -->|metadata or end| STOP
    end

    IN --> ESC --> FMT --> LINES
    LOOP --> ENTRIES

    subgraph ENTRIES["Output entries list"]
        direction LR
        E1["`seq · type · section · scene
        speaker · direction · text
        direction_type`"]
    end

    ENTRIES --> STATS["`Compute stats
    total_entries · dialogue_lines
    characters_for_tts · speakers`"]
    STATS --> JSON["📦 parsed_the413_S01E01.json"]
```

### Speaker normalization

```mermaid
flowchart LR
    RAW["`KNOWN_SPEAKERS list
    Ordered longest-first
    Compound names before simple`"]
    RAW --> MATCH{"`startswith match
    space, paren, or end follows?`"}
    MATCH -->|yes| KEY["`SPEAKER_KEYS lookup
    ADAM → adam
    MR. PATTERSON → mr_patterson
    FILM AUDIO (MARGARET'S VOICE) → film_audio
    STRANGER (MALE VOICE, FLAT) → stranger
    KAREN → karen · SARAH → sarah`"]
    MATCH -->|no| SKIP2["try next speaker"]
    KEY --> MODE{"`spoken_text empty?`"}
    MODE -->|yes| PEND["`pending_speaker state
    await direction/text on next lines`"]
    MODE -->|no| ENTRY["`dialogue entry (single-line)
    speaker = normalized key`"]
```

---

## 3. XILP002 — Voice Generation

```mermaid
sequenceDiagram
    actor User
    participant M as main
    participant LP as load_production
    participant SFX as sfx_config
    participant QG as Quota Guard
    participant API as ElevenLabs API
    participant FS as stems directory
    participant PJ as parsed JSON

    User->>M: python XILP002_the413_producer.py --episode S02E03 [--gen-sfx / --gen-music / --gen-ambience]
    M->>LP: load cast_the413_S02E03.json + parsed script
    LP-->>M: config dict, dialogue_entries list
    M->>SFX: load sfx_the413_S02E03.json (always, for preamble)
    SFX-->>M: SfxConfiguration model

    alt preamble block in cast config
        M->>QG: check_elevenlabs_quota
        M->>API: text_to_speech.convert(preamble_text, tina voice)
        API-->>M: audio_stream
        M->>FS: write n002_preamble_tina.mp3
        M->>SFX: look up effects["INTRO MUSIC"].source
        SFX-->>M: "SFX/The Porch Light.mp3"
        M->>FS: copy source → n001_preamble_sfx.mp3
        note over FS: play_duration % applied at copy time
Stem file reflects actual playback length
    end

    M->>QG: get_best_model_for_budget
    QG-->>M: eleven_v3 or eleven_flash_v2_5

    loop each dialogue entry from start_from
        M->>FS: stem file exists?
        alt already on disk
            FS-->>M: skip, no API call
        else voice_id is TBD
            M->>M: skip, warn user
        else
            M->>QG: has_enough_characters(text)
            alt quota exhausted
                QG-->>M: False, halt with message
            else quota OK
                M->>API: text_to_speech.convert(text, voice_id, model)
                API-->>M: audio_stream chunks
                M->>FS: write {seq:03d}_{section}_{speaker}.mp3
                M->>FS: tag_mp3 (Album, Genre, Year, Title, Artist, Lyrics)
            end
        end
    end

    alt preamble block in cast config
        M->>PJ: inject_preamble_entries()
        note over PJ: Strip any seq ≤ 0 entries
Prepend seq −2 (dialogue)
and seq −1 (INTRO MUSIC)
        PJ-->>M: parsed JSON updated in-place (idempotent)
    end

    M-->>User: Generation complete, N new stems
```

---

## 4. XILP003 — Audio Assembly (Two-Pass Multi-Track Mix)

```mermaid
flowchart TD
    C2["`📋 cast_the413_S01E01.json
    pan + filter per character`"]
    J2["`📦 parsed_the413_S01E01.json
    direction_type per entry`"]
    ST2["`stems/S01E01/*.mp3
    sorted by seq prefix`"]

    C2 --> CFG_LOAD["`CastConfiguration model
    build config dict`"]
    J2 --> IDX["`load_entries_index()
    {seq → entry} dict`"]
    ST2 --> PLANS["`collect_stem_plans()
    classify each stem by direction_type`"]
    IDX --> PLANS

    PLANS --> BRANCH{"parsed JSON\navailable?"}

    BRANCH -->|no| SEQ["`assemble_audio()
    sequential concat (fallback)`"]

    BRANCH -->|yes| FG

    subgraph FG["Foreground Pass — build_foreground()"]
        direction TB
        FG1["`Dialogue + SFX + BEAT stems
        concatenated with 600ms gaps`"]
        FG2["`timeline dict
        {seq → start_ms}`"]
        FG1 --> FG2
    end

    subgraph BG["Background Pass"]
        direction TB
        AMB["`build_ambience_layer()
        loop each AMBIENCE stem to next cue
        −10 dB`"]
        MUS["`build_music_layer()
        overlay each MUSIC sting at cue
        −6 dB`"]
        AMB --> BGMIX["ambience.overlay(music)"]
        MUS --> BGMIX
    end

    FG2 --> BG
    FG1 --> OVERLAY["foreground.overlay(background)"]
    BGMIX --> OVERLAY

    OVERLAY --> EXPORT2["export the413_S01E01_master.mp3"]
    SEQ --> EXPORT2
    EXPORT2 --> PLAY2["os.system mpg123 — WSL playback"]

    CFG_LOAD --> FG
    CFG_LOAD --> SEQ
```

> **Restartability:** XILP003 has no ElevenLabs dependency. Re-running assembly after adjusting
> effects or adding missing stems requires no API key and carries no TTS quota risk.

---

## 5. XILP004 — Studio Project Onboarding

```mermaid
flowchart TD
    PARSED["`📦 parsed_the413_S01E02.json
    Dialogue + section + scene entries`"]
    CAST["`📋 cast_the413_S01E02.json
    voice_id per character`"]

    LOAD["`load_episode()
    Validate no TBD voice_ids`"]
    BUILD["`build_content_json()
    Transform entries → chapters/blocks/nodes`"]

    PARSED --> LOAD
    CAST --> LOAD
    LOAD --> BUILD

    subgraph MAPPING["Content Mapping Rules"]
        direction TB
        SEC["`section_header
        → new chapter (name)`"]
        SCN["`scene_header
        → h2 block (narrator voice)`"]
        DLG["`dialogue
        → p block with speaker's voice_id`"]
        DIR["`direction
        → skipped (not voiced)`"]
    end

    BUILD --> MAPPING
    MAPPING --> MODE{"--dry-run?"}
    MODE -->|yes| DRY["`dry_run()
    Print chapter summary
    Show voice assignments`"]
    MODE -->|no| API["`create_project()
    client.studio.projects.create()
    from_content_json payload`"]
    API --> PROJ["`🎬 Studio Project
    project_id returned`"]
```

> **Speaker-name problem solved:** Each `tts_node` carries its own `voice_id` — speaker names
> never appear in the text, so TTS won't voice them. No manual post-creation cleanup needed.

---

## 6. Stem File Naming Convention

### Standard stems (seq ≥ 1)

```mermaid
flowchart LR
    SEQ["`seq
    003`"]
    SEP1["_"]
    SEC["`section
    cold-open`"]
    SEP2["-"]
    SCN["`scene
    scene-1`"]
    SEP3["_"]
    SPK["`speaker
    adam`"]
    EXT[".mp3"]

    SEQ --> SEP1 --> SEC --> SEP2 --> SCN --> SEP3 --> SPK --> EXT

    style SEQ fill:#d4e6f1
    style SEC fill:#d5f5e3
    style SCN fill:#fdebd0
    style SPK fill:#f9ebea
```

**Example:** `003_cold-open_adam.mp3`, `028_act1-scene-1_rian.mp3`, `102_act2-scene-5_mr_patterson.mp3`

### Preamble stems (seq < 0)

Preamble stems use an **`n` prefix** in place of the zero-padded integer, where  stands for "negative":

| Seq | Filename | Layer |
|-----|----------|-------|
| −2 | `n002_preamble_tina.mp3` | Dialogue (broadcast intro voice) |
| −1 | `n001_preamble_sfx.mp3` | Music (intro music, foreground sequential) |

 in  parses the  prefix and returns the corresponding negative integer.
Negative seqs sort before all script seqs (≥ 1) so preamble always plays first.

Preamble entries at seq −2 and −1 are injected into  by XILP002 after stem generation,
making the parsed JSON a complete record of the full episode including the broadcast intro.

---

## 7. API Cost Guard Flow

```mermaid
flowchart TD
    START["Before each API call"]
    CHK["`has_enough_characters(text)
    client.user.get()`"]
    ERR{"API error?"}
    SKIP_GUARD["`Skip guard
    no user_read permission
    return True`"]
    CALC["`remaining = limit - count
    required = len(text)`"]
    CMP{"remaining >= required?"}
    OK["✅ Proceed to API call"]
    HALT["`🛑 Halt generation
    Log chars needed vs remaining`"]

    START --> CHK --> ERR
    ERR -->|yes| SKIP_GUARD
    ERR -->|no| CALC --> CMP
    CMP -->|yes| OK
    CMP -->|no| HALT

    BUDGET["`get_best_model_for_budget()
    remaining > 5000?`"]
    V3["`eleven_v3
    standard quality`"]
    FLASH["`eleven_flash_v2_5
    50% cheaper`"]
    FALLBACK["`eleven_multilingual_v2
    API error fallback`"]

    BUDGET -->|yes| V3
    BUDGET -->|no| FLASH
    BUDGET -->|exception| FALLBACK
```

---

## 8. XILP005 — DAW Layer Export

```mermaid
flowchart TD
    C5["`📋 cast_the413_S01E01.json`"]
    J5["`📦 parsed_the413_S01E01.json`"]
    ST5["`stems/S01E01/*.mp3`"]

    C5 --> L5["`load cast config
    build speaker effects dict
    + show/season/episode metadata`"]
    J5 --> IDX5["`load_entries_index()
    {seq → entry}`"]
    ST5 --> PLANS5["`collect_stem_plans()
    classify by direction_type`"]
    IDX5 --> PLANS5

    PLANS5 --> TL5

    TL5["`build_foreground()
    foreground track + {seq → ms} timeline`"]
    L5 --> TL5

    TL5 --> DLG5["`build_dialogue_layer()
    dialogue stems at timeline positions
    phone filter + pan applied`"]
    TL5 --> AMB5["`build_ambience_layer(level_db=0)
    AMBIENCE looped to next cue
    no ducking — producer controls level`"]
    TL5 --> MUS5["`build_music_layer(level_db=0)
    MUSIC stings at cue positions`"]
    TL5 --> SFX5["`build_sfx_layer()
    SFX + BEAT at timeline positions`"]

    DLG5 --> WAV1["`daw/S01E01/
    S01E01_layer_dialogue.wav`"]
    AMB5 --> WAV2["S01E01_layer_ambience.wav"]
    MUS5 --> WAV3["S01E01_layer_music.wav"]
    SFX5 --> WAV4["S01E01_layer_sfx.wav"]

    WAV1 --> TAG5["`tag_wav()
    ID3 metadata: Album, Genre,
    Year, Title, Artist`"]
    WAV2 --> TAG5
    WAV3 --> TAG5
    WAV4 --> TAG5

    DLG5 --> LBL1["S01E01_labels_dialogue.txt"]
    AMB5 --> LBL2["S01E01_labels_ambience.txt"]
    MUS5 --> LBL3["S01E01_labels_music.txt"]
    SFX5 --> LBL4["S01E01_labels_sfx.txt"]

    TAG5 --> SCRIPT5["`S01E01_open_in_audacity.py
    Manual import instructions
    (WAVs + optional labels)`"]
    TAG5 --> MACRO5["`--macro → THE413_S01E01.txt
    Audacity macro (WAVs only)
    written to %APPDATA%/audacity/Macros/`"]

    DLG5 --> TLVIZ["`timeline_viz.py
    build_timeline_data()`"]
    AMB5 --> TLVIZ
    MUS5 --> TLVIZ
    SFX5 --> TLVIZ
    TLVIZ -->|"--timeline"| ASCII5["`ASCII timeline → stdout
    render_terminal_timeline()`"]
    TLVIZ -->|"--timeline-html"| HTML5["`S01E01_timeline.html
    render_html_timeline()
    (hover tooltips + zoom)`"]
```

> **Audacity alignment:** All four WAV files are exactly the same duration (full episode length).
> Importing them into Audacity at t=0 produces four perfectly aligned tracks — no repositioning
> or time-offset metadata required.

> **Audio metadata:** Each WAV layer is tagged with ID3 metadata (Album = show name, Genre = "Podcast",
> Year, Title = e.g. "S02E03 Dialogue", Artist = season title) via `tag_wav()` from `sfx_common.py`.

> **Label tracks:** Audacity-format label files (tab-separated start/end/text) are generated alongside
> each WAV layer. Import labels separately via `File > Import > Labels...` in Audacity.

> **Audacity macro:** `--macro` writes a one-click macro (`THE413_<TAG>.txt`) to the Audacity Macros
> directory. The macro imports the four WAV files only (labels are imported manually). Access via
> `Tools > Macros` in Audacity.

> **Preamble support:** When the cast config includes a `preamble` block, XILP002 generates
> `n002_preamble_tina.mp3` (broadcast intro voice, seq −2) and copies the intro music from
> `sfx_config.effects["INTRO MUSIC"].source` into `n001_preamble_sfx.mp3` (seq −1).  It then
> injects seq −2/−1 entries into the parsed JSON so they flow through the standard
> `collect_stem_plans()` path in XILP003 and XILP005 — no special preamble parameter needed.
> Preamble music has `foreground_override = True` so it plays sequentially, not as a background
> overlay.

> **Timeline visualization:** `--timeline` prints an ASCII multitrack view to stdout; `--timeline-html`
> writes a self-contained HTML file with color-coded swim lanes, hover tooltips, and Ctrl+scroll zoom.
> Both work with `--dry-run` — the dry-run path uses `build_foreground_timeline_only()` (mutagen
> header reads, no audio decoding) and the `compute_*_labels()` helpers in `mix_common.py`.

> **Note on mod-script-pipe:** The generated helper script includes pipe automation code, but
> Audacity 3.7.x does not reliably initialise mod-script-pipe on Windows. The Audacity macro
> (`--macro`) is the recommended automation path.

---

## 9. XILP006 — Cues Sheet Ingester

Pre-processing step that bridges a human-authored sound cues & music prompts document into the
automated pipeline.  Sits **after XILP001** and **before XILU002 / XILP002** — enriching the SFX
config and populating the shared asset library before stem generation begins.

### 9a. Overall flow

```mermaid
flowchart TD
    CQ["`📋 cues/*.md
    Sound cues & music prompts
    (MUSIC / AMBIENCE / SFX sections)`"]
    PARSE["parse_cues_markdown()"]
    ASSETS["`Asset list
    asset_id · category · reuse
    prompt · duration_seconds
    loop · scene`"]
    MANIFEST["`📦 cues/cues_manifest_<TAG>.json
    Always written — structured catalog`"]
    AUDIT["dry_run_report()"]

    CQ --> PARSE --> ASSETS
    ASSETS --> MANIFEST
    ASSETS --> AUDIT

    ASSETS --> GEN_BRANCH{"--generate?"}
    GEN_BRANCH -->|"yes, not dry-run"| GEN["generate_new_assets()"]
    GEN_BRANCH -->|"--dry-run"| SKIP_GEN["`Skip API calls
    Show credit estimate`"]
    GEN --> SFXLIB["`🎵 SFX/mus-theme-main-01.mp3
    SFX/sfx-boots-stamp-01.mp3 …
    Named by asset ID (lowercase)`"]

    ASSETS --> ENR_BRANCH{"--enrich-sfx-config?"}
    ENR_BRANCH -->|"yes, not dry-run"| ENR["enrich_sfx_config()"]
    ENR_BRANCH -->|"--dry-run"| DIFF["`Show prompt + duration diff
    No file written`"]
    ENR --> SFXCFG["`📋 sfx_the413_<TAG>.json
    Updated prompts + durations
    loop flag set for ambience`"]
```

### 9b. Cues markdown parsing

```mermaid
flowchart TD
    MD["cues/*.md"]
    SEC{"`## heading?`"}

    MD --> LINES["Read line by line"]
    LINES --> SEC

    SEC -->|"MUSIC CUES"| MUSIC_LOOP
    SEC -->|"AMBIENCE"| AMB_LOOP
    SEC -->|"SOUND EFFECTS"| SFX_LOOP
    SEC -->|"other"| NULL["section = None\nskip lines"]

    subgraph MUSIC_LOOP["MUSIC / AMBIENCE section"]
        direction TB
        H3["`### ASSET-ID (REUSE|NEW)
        → pending asset dict`"]
        PLINE["`**Prompt:** … **Duration:** … **Used:** …
        → fill pending, append to list`"]
        H3 --> PLINE
    end

    subgraph SFX_LOOP["SOUND EFFECTS section"]
        direction TB
        SCENE_H["`### Scene N: Name
        → current_scene label`"]
        ROW["`| ASSET-ID (REUSE|NEW) | Prompt | Placement |
        → append asset dict with scene`"]
        SCENE_H --> ROW
    end

    MUSIC_LOOP --> OUT2["asset list"]
    AMB_LOOP --> OUT2
    SFX_LOOP --> OUT2
```

### 9c. Library audit status codes

| Status | Meaning |
|--------|---------|
| `EXISTS` | `SFX/<asset-id>.mp3` is present and non-empty |
| `REUSE` | Asset is marked *(REUSE)* in the cues sheet but not yet in `SFX/` — must be sourced or regenerated |
| `NEW` | Asset is marked *(NEW)* — needs ElevenLabs API generation via `--generate` |

### 9d. SFX config enrichment matching

```mermaid
flowchart LR
    AID["`asset_id
    e.g. MUS-THEME-MAIN-01`"]
    KEYS["`sfx config keys
    (direction text)`"]
    MATCH{"`asset_id substring
    found in key?`"}
    UPDATE["`Update entry:
    prompt ← cues sheet prompt
    duration_seconds ← min(dur, 30s)
    loop ← True (ambience only)`"]
    SKIP["No match — skip"]

    AID --> MATCH
    KEYS --> MATCH
    MATCH -->|yes| UPDATE
    MATCH -->|no| SKIP
```

> **Duration cap:** ElevenLabs Sound Effects API accepts at most 30 seconds per call.
> Assets with longer cues-sheet durations (e.g. 3-minute underscore) are generated at 30s
> and flagged `[CAPPED]` in the audit report.  Looping in XILP003/XILP005 handles extension.

### 9e. Recommended run order for a new episode

```bash
# 1. Parse script and generate skeleton configs
python XILP001_script_parser.py "scripts/<script>.md" --episode S02E03

# 2. Ingest cues sheet — enrich sfx config + audit (no API calls yet)
python XILP006_the413_cues_ingester.py --episode S02E03 \
    --cues "cues/<cues-file>.md" --enrich-sfx-config

# 3. Preview what needs generating
python XILP006_the413_cues_ingester.py --episode S02E03 \
    --cues "cues/<cues-file>.md" --generate --dry-run

# 4. Generate new SFX/music assets into SFX/ library
python XILP006_the413_cues_ingester.py --episode S02E03 \
    --cues "cues/<cues-file>.md" --generate

# 5. Generate voice stems (sfx config already enriched)
#    Preamble: ensure sfx_the413_S02E03.json contains an "INTRO MUSIC" entry with a "source" path
#    XILP002 will copy that file → n001_preamble_sfx.mp3 and inject seq -2/-1 into parsed JSON
python XILP002_the413_producer.py --episode S02E03 --dry-run
python XILP002_the413_producer.py --episode S02E03
# Generate SFX/music/ambience stems by category (omit flags to generate all):
python XILU002_generate_SFX.py --episode S02E03 --gen-sfx --dry-run
python XILU002_generate_SFX.py --episode S02E03 --gen-music --dry-run
python XILU002_generate_SFX.py --episode S02E03 --gen-ambience --dry-run
python XILU002_generate_SFX.py --episode S02E03

# 6. Assemble master MP3 or export DAW layers
python XILP003_the413_audio_assembly.py --episode S02E03
python XILP005_the413_daw_export.py --episode S02E03 --macro

# 7. Inspect asset placement (no audio decode needed with --dry-run)
python XILP005_the413_daw_export.py --episode S02E03 --dry-run --timeline
python XILP005_the413_daw_export.py --episode S02E03 --timeline --timeline-html
```

---

## 10. Timeline Visualization (`timeline_viz.py`)

Shared module that renders asset placement across all four layers without any pydub dependency.
Consumed by XILP005 via `--timeline` and `--timeline-html`.

### 10a. Data model

```mermaid
classDiagram
    class LayerSpan {
        +float start_s
        +float end_s
        +str label
    }
    class TimelineData {
        +str tag
        +float total_duration_s
        +dict layers
    }
    TimelineData "1" --> "*" LayerSpan : layers[key]
```

### 10b. Rendering paths

```mermaid
flowchart TD
    DLG_L["`dialogue labels
    list of (start_s, end_s, speaker)`"]
    AMB_L["`ambience labels`"]
    MUS_L["`music labels`"]
    SFX_L["`sfx labels`"]

    BUILD["`build_timeline_data()
    Wraps four label lists → TimelineData`"]

    DLG_L --> BUILD
    AMB_L --> BUILD
    MUS_L --> BUILD
    SFX_L --> BUILD

    BUILD --> TERM["`render_terminal_timeline()
    Unicode ASCII — time ruler + layer bars
    auto-scales to terminal width (shutil)`"]
    BUILD --> HTML["`render_html_timeline()
    Self-contained HTML — no CDN
    color-coded swim lanes
    hover tooltips · Ctrl+scroll zoom`"]

    TERM --> STDOUT["stdout"]
    HTML --> FILE["`daw/{TAG}/{TAG}_timeline.html`"]
```

### 10c. Dry-run label path (no audio decoding)

```mermaid
flowchart LR
    PLANS["`stem_plans
    (StemPlan list)`"]
    FT["`build_foreground_timeline_only()
    mutagen header reads only
    → (total_ms, timeline)`"]
    PLANS --> FT

    FT --> DLG2["`compute_dialogue_labels()`"]
    FT --> AMB2["`compute_ambience_labels()`"]
    FT --> MUS2["`compute_music_labels()`"]
    FT --> SFX2["`compute_sfx_labels()`"]

    DLG2 --> BTD["`build_timeline_data()`"]
    AMB2 --> BTD
    MUS2 --> BTD
    SFX2 --> BTD

    BTD --> RENDER["`render_terminal_timeline()
    render_html_timeline()`"]
```

> **Fast dry-run:** `build_foreground_timeline_only()` uses `mutagen.mp3.MP3(path).info.length`
> for header-only duration reads — orders of magnitude faster than `AudioSegment.from_file()`
> for a full episode.  The `compute_*_labels()` helpers apply the same boundary logic as the
> audio-loading layer builders (`build_ambience_layer` etc.) but return label tuples only.

---

## 11. Ambience Stop Markers

Script-side directives that end an ambience loop without starting a new one.

### Recognized patterns
- `[AMBIENCE: STOP]` — explicit stop
- `[AMBIENCE: DINER FADES OUT]`, `[AMBIENCE: B&B FADES OUT]` — any `FADES OUT` suffix

### How they work
1. **XILP001** auto-generates `type: "silence", duration_seconds: 0.0` entries in the sfx config — no audio asset is created
2. **mix_common `collect_stem_plans()`** injects a synthetic `StemPlan(filepath="")` for each stop marker found in the entries index — they never have a stem file on disk
3. **`build_ambience_layer()`** uses stop markers as `bg_cues` boundary markers: the preceding ambience loop's `end_ms` is set to the stop marker's timeline position
4. Stop marker plans are skipped when loading audio (empty filepath) and generate no label in the timeline

### `loop: false` vs stop markers
| | `loop: false` | Stop marker |
|---|---|---|
| Controlled in | sfx config | script |
| Effect | Plays file once (no tiling) | Ends loop at cue position |
| Audio generated | Yes | No |
| Timeline label | Yes | No |
