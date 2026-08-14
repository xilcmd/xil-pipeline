# SFX Reuse Guide -- Minimizing ElevenLabs API Credit Usage

Every second of ElevenLabs SFX generation costs approximately **40 credits**. A 30-second ambience asset costs ~1,200 credits. The `SFX/` library already contains 250+ shared assets -- reusing them costs **zero credits**.

The pipeline's SFX engine (`sfx_common.py:ensure_shared_sfx()`) follows a 3-tier priority:

1. **Cached** -- shared asset already exists on disk in `SFX/` --> return immediately (0 credits)
2. **Source** -- `source` field in sfx config points to a file --> copy it (0 credits)
3. **API** -- no cache, no source --> call ElevenLabs API (~40 credits/second)

| Method | Credits | When to use |
|--------|---------|-------------|
| Cached (already generated) | 0 | Same effect reused across episodes |
| `source` field (existing file) | 0 | Asset exists in library under a different name |
| API generation | ~40/second | Truly new sound needed |

## Discovering Available Assets

### xil-sfx-lib (recommended)

```bash
xil-sfx-lib                      # list all assets with duration and size
xil-sfx-lib --search "diner"     # filter by keyword
xil-sfx-lib --search "coffee"    # find coffee-related effects
xil-sfx-lib --json               # machine-readable output
xil-sfx-lib -v                   # verbose: all metadata fields
```

### Filesystem scan

```bash
ls SFX/*.mp3 | head -20          # quick browse
ls -la SFX/ | wc -l              # count assets
```

### Naming conventions

The library contains two naming styles:

- **Cue-sheet asset IDs**: `sfx-boots-stamp-01.mp3`, `amb-quarry-winter-01.mp3`
- **Descriptive filenames**: `FOLYProp-Pouring_hot_liquid_i-Elevenlabs.mp3`, `BELLDoor-Bright_entrance_door-Elevenlabs.mp3`

Both work identically with the `source` field.

## Using `source` in sfx Config JSON

Add a `source` field to any entry in `sfx_<slug>_<TAG>.json` to skip API generation entirely. The file is copied to the episode stem directory at generation time.

### Examples from production

```json
{
  "effects": {
    "INTRO MUSIC": {
      "source": "SFX/The Porch Light.mp3",
      "volume_percentage": 40,
      "play_duration": 10
    },
    "SFX: COFFEE BEING POURED INTO CERAMIC MUG": {
      "prompt": "Coffee being poured into ceramic mug",
      "duration_seconds": 2.0,
      "source": "SFX/FOLYProp-Pouring_hot_liquid_i-Elevenlabs.mp3"
    },
    "SFX: DINER DOOR OPENS, BELL CHIMES": {
      "prompt": "Classic diner door opening with small bell chime",
      "duration_seconds": 5.0,
      "source": "SFX/BELLDoor-Bright_entrance_door-Elevenlabs.mp3"
    },
    "AMBIENCE: RADIO BOOTH - SOFT EQUIPMENT HUM, SLIGHT STATIC, INTIMATE": {
      "source": "SFX/Invitation to Action.mp3",
      "prompt": "Radio booth ambience, soft equipment hum, slight static",
      "duration_seconds": 30.0,
      "loop": false
    }
  }
}
```

### Rules

- **Path**: always relative to project root (starts with `SFX/`)
- **`prompt`**: keep it for documentation even when `source` is set -- it has no effect on generation
- **Mixing fields**: `volume_percentage`, `loop`, `play_duration`, `ramp_in_seconds`, `ramp_out_seconds` all work the same with `source` as with API-generated assets
- **Duration**: `duration_seconds` is used for mixing calculations; it is not validated against the actual file length when `source` is set

## Writing Cues Sheets with REUSE Markers

In the cues markdown file, mark assets that already exist in `SFX/` as `(REUSE)` so `xil-cues --generate` skips them.

### Heading format (MUSIC CUES and AMBIENCE sections)

```markdown
### **MUS-THEME-MAIN-01 (REUSE)**
**Prompt:** Eerie indie folk theme, acoustic guitar **Duration:** 60 seconds **Used:** Cold open

### **MUS-STING-NEW-01 (NEW)**
**Prompt:** Brief hopeful musical release **Duration:** 5 seconds **Used:** Scene 1
```

### Table format (SOUND EFFECTS section)

```markdown
| Asset Name | Prompt | Placement |
| ----- | ----- | ----- |
| SFX-DOOR-BELL-01 (REUSE) | Diner door opening with bell chime | Karen's entrance |
| SFX-BOOTS-STAMP-01 (NEW) | Snow being stamped off boots on doormat | Karen entering |
```

**Best practice**: run `xil-sfx-lib --search "keyword"` before writing the cues sheet to check what already exists in the library.

## Script Directions and sfx Config Keys

The parser (XILP001) extracts direction text verbatim as the `text` field in parsed JSON. This text becomes the **key** in the sfx config `effects` dict:

```
Script direction:     [SFX: DINER DOOR OPENS, BELL CHIMES]
                          |
Parsed JSON text:     "SFX: DINER DOOR OPENS, BELL CHIMES"
                          |
sfx config key:       "SFX: DINER DOOR OPENS, BELL CHIMES": { "source": "SFX/..." }
```

When writing a new episode script, reuse the **exact same direction text** from previous episodes to match existing sfx config entries. This lets you copy `source` fields across episode configs without modification.

## New Episode Workflow Checklist

1. **Audit the library**
   ```bash
   xil-sfx-lib --search "keyword"
   ```
   Check for each sound effect you plan to use.

2. **Write the cues sheet** -- mark every asset that exists in `SFX/` as `(REUSE)`.

3. **Dry-run the cues ingester**
   ```bash
   xil-cues --episode S0xExx --dry-run
   ```
   Review the audit report. Check credit estimate for `NEW` assets.

4. **Write the production script** -- use direction text matching existing sfx config keys where possible.

5. **Parse the script**
   ```bash
   xil-parse scripts/script.md --episode S0xExx
   ```
   This auto-generates a skeleton sfx config if one doesn't exist.

6. **Add `source` fields** -- for each effect that exists in `SFX/`, add a `"source": "SFX/filename.mp3"` entry to the sfx config JSON.

7. **Dry-run SFX generation**
   ```bash
   xil-sfx --episode S0xExx --dry-run
   ```
   Verify reused assets show `CACHED` (0 credits) not `NEW`.

8. **Generate only what is truly new**
   ```bash
   xil-sfx --episode S0xExx
   ```

## Quick Reference

| Goal | Command / Action |
|------|-----------------|
| List all SFX assets | `xil-sfx-lib` |
| Search for a sound | `xil-sfx-lib --search "keyword"` |
| Machine-readable asset list | `xil-sfx-lib --json` |
| Skip API for an effect | Add `"source": "SFX/filename.mp3"` to sfx config |
| Mark cue as reuse | Append `(REUSE)` to asset ID in cues sheet |
| Preview SFX credit cost | `xil-sfx --episode TAG --dry-run` |
| Preview cues credit cost | `xil-cues --episode TAG --dry-run` |
| Enrich sfx config from cues | `xil-cues --episode TAG --enrich-sfx-config` |

## eleven_v3 Audio Events vs. SFX Stems

Certain sounds that previously required a `[SFX: ...]` direction and a generated
stem can now be handled inline by the TTS model using v3 audio tags embedded in
dialogue text:

| Old approach (SFX stem, costs credits) | v3 inline tag (free, in voice stem) |
|---|---|
| `[SFX: ADAM SIGHS HEAVILY]` | `[sighs]` embedded in Adam's dialogue text |
| `[SFX: NERVOUS LAUGH]` | `[chuckles]` or `[laughs]` in dialogue |
| `[SFX: SHARP INTAKE OF BREATH]` | `[gasps]` in dialogue |

**When to still use SFX stems:** for sounds *between* speakers, overlapping with
ambience, or effects that need precise mix placement (volume, timing relative to
other tracks). Inline tags render inside the single voice stem and cannot be
mixed independently in the DAW layers.

See `claude-scriptwriter-reference.md` for the full v3 tag vocabulary.
