# xil-pipeline Script & Cues Writing Reference

You are helping write production scripts and sound cues sheets for an audio drama podcast. The scripts you produce will be processed by the xil-pipeline toolset, which generates voice audio via ElevenLabs TTS and sound effects via the ElevenLabs Sound Effects API.

**Critical constraint:** Every second of SFX generation costs ~40 API credits. The project has an existing SFX library with 250+ pre-generated assets. You MUST reuse these assets whenever possible instead of inventing new sounds. The SFX inventory is provided as a companion JSON file.

## Production Script Format

The pipeline parser expects this exact markdown format:

```
Show Name Season N: Episode N: "Episode Title"

CAST:
* CHARACTER_NAME — brief description
* ANOTHER_CHARACTER — brief description

===

COLD OPEN

SCENE 1: LOCATION NAME

[AMBIENCE: Description of environmental sound]

CHARACTER_NAME
Dialogue line here.

[SFX: Description of sound effect]

CHARACTER_NAME (acting direction)
More dialogue here.

[BEAT]

[BEAT — 3 SECONDS]

===

ACT ONE

SCENE 2: ANOTHER LOCATION

[AMBIENCE: New environment description]

...

===

END OF EPISODE
```

### Key format rules

- **Header line**: `Show Name Season N: Episode N: "Title"` (first line, no markup)
- **Cast block**: `* NAME — description` (one per line, before first `===`)
- **Section dividers**: `===` on its own line
- **Section headers**: `COLD OPEN`, `ACT ONE`, `ACT TWO`, `MID-EPISODE BREAK`, `OPENING CREDITS`, `CLOSING` (on own line, no brackets)
- **Scene headers**: `SCENE N: LOCATION NAME` (on own line)
- **Dialogue**: Speaker name on one line, dialogue text on the next
- **Acting directions**: In parentheses after speaker name: `CHARACTER (whispering)`
- **Directions**: In square brackets: `[SFX: ...]`, `[AMBIENCE: ...]`, `[MUSIC: ...]`, `[BEAT]`, `[BEAT — N SECONDS]`
- **End marker**: `END OF EPISODE` (stops parsing)
- **Ambience stop**: `[AMBIENCE: STOP]` or `[AMBIENCE: description FADES OUT]` to end a looping ambience

## eleven_v3 Audio Tags (Inline Dialogue Modifiers)

The pipeline runs voice generation through ElevenLabs `eleven_v3`, which supports an
inline tag system for controlling emotion, pacing, and vocal performance. These tags
are embedded **inside dialogue text** — they are not stage directions.

**Critical distinction:** Stage directions use square brackets on their own line:
```
[SFX: DOOR OPENS]          ← stage direction, parsed separately, triggers SFX stem
```
Audio tags use square brackets *within* the spoken text on the dialogue line:
```
ADAM
I've been here all night. [exhausted] Every. Single. Night.
```

### Pause tags (v3 equivalent of `[BEAT]`)

| Tag | Effect |
|-----|--------|
| `[pause]` | Standard beat pause |
| `[short pause]` | Brief hesitation |
| `[long pause]` | Extended silence |

Use these in preference to `[BEAT — N SECONDS]` stage directions when the pause is
character-driven rather than a production beat. They render inline within the voice
stem rather than creating a separate silence stem.

### Audio events (non-speech sounds)

| Tag | Effect |
|-----|--------|
| `[laughs]` | Laughter |
| `[chuckles]` | Soft laugh |
| `[sighs]` | Audible sigh |
| `[gasps]` | Sudden intake of breath |
| `[gulps]` | Nervous swallow |
| `[coughs]` | Single cough |
| `[crying]` | Crying or tearful quality |

These inject a rendered audio event at the exact position in the spoken line — no
separate SFX stem needed, no API credits consumed from the SFX budget.

### Emotional delivery

| Tag | Effect |
|-----|--------|
| `[excited]` | Energised, enthusiastic |
| `[nervous]` | Anxious, hesitant quality |
| `[frustrated]` | Irritated, clipped |
| `[exhausted]` | Tired, heavy delivery |
| `[sorrowful]` | Sad, weighted |
| `[calm]` | Measured, quiet |
| `[resigned tone]` | Accepting defeat |

### Tone and attitude

| Tag | Effect |
|-----|--------|
| `[whispers]` | Hushed delivery |
| `[deadpan]` | Flat, dry |
| `[sarcastic]` | Ironic, knowing |
| `[cheerfully]` | Bright, upbeat |
| `[playfully]` | Light, teasing |
| `[curious]` | Questioning, open |

### Delivery and pacing

| Tag | Effect |
|-----|--------|
| `[rushed]` | Faster cadence |
| `[drawn out]` | Stretched, deliberate |
| `[hesitates]` | Momentary stumble |
| `[stammers]` | Stuttering delivery |

### Usage rules

- Tags affect all following text until the next tag overrides them
- Tags can be stacked: `[nervous][hushed]` applies both qualities
- Place tags at the natural transition point: `I'm fine. [resigned tone] Totally fine.`
- **Do not put a v3 tag alone on its own line** — the parser reads a bare `[tag]` line
  as an unknown stage direction and skips it. Keep tags embedded mid-text or at the
  very start of the dialogue text line, not on a line by themselves.
- Punctuation amplifies tag effects — ellipses and commas create natural breath points

### Example

```
ADAM (exhausted)
Everything is fine. [long pause] I keep telling myself that. [sighs] Maybe one day
I'll believe it.

JESS
[curious] What happened to you tonight?

ADAM
[laughs] Nothing. [pause] [resigned tone] Everything.
```

## Using Existing SFX Assets in Scripts

When writing a direction that matches an existing asset in the SFX library, include a **filename hint** using a pipe separator:

```
[SFX: DINER DOOR OPENS, BELL CHIMES | BELLDoor-Bright_entrance_door-Elevenlabs.mp3]

[SFX: COFFEE BEING POURED INTO CERAMIC MUG | FOLYProp-Pouring_hot_liquid_i-Elevenlabs.mp3]

[AMBIENCE: RADIO BOOTH - SOFT EQUIPMENT HUM, SLIGHT STATIC, INTIMATE | ambience_radio-booth-soft-equipment-hum-slight-static-intimate.mp3]

[MUSIC: EERIE INDIE FOLK THEME, FADES UNDER | music_mus-theme-main-01-eerie-indie-folk-fades-under.mp3]
```

The format is: `[TYPE: DESCRIPTION | filename.mp3]`

- The description before the pipe becomes the sfx config key
- The filename after the pipe tells the operator which file from `SFX/` to assign as the `source`
- If no matching asset exists, omit the pipe and filename — it will be generated via API

### How to find matching assets

Check the companion **SFX inventory JSON file** for assets. Search by:
- **Filename**: look for keywords in the filename (e.g., "coffee", "door", "ambience")
- **Title** (ID3 tag): the effect key stored in the file metadata
- **Category prefixes**: `sfx_` or `sfx-` (one-shot effects), `ambience_` or `amb-` (environments), `music_` or `mus-` (musical cues), `beat` (silences/transitions)

### When to reuse vs. generate new

**Always reuse** when:
- The same type of sound exists (door opening, coffee pour, footsteps, phone buzz)
- An ambience for the same location type exists (diner, radio booth, outdoor)
- A music theme or sting has been established for recurring use

**Generate new** only when:
- No similar sound exists in the library
- The scene requires a very specific sound not covered by existing assets
- A unique musical cue is needed for a new emotional moment

## Cues Sheet Format

The cues sheet is a separate markdown document that catalogs all sound assets needed for an episode. It has three sections:

### MUSIC CUES (heading blocks)

```markdown
## **MUSIC CUES**

### **MUS-THEME-MAIN-01 (REUSE)**
**Prompt:** Eerie indie folk theme, acoustic guitar with subtle synth, mysterious but warm, late-night radio feel **Duration:** 60 seconds **Used:** Cold open, closing

### **MUS-STING-NEW-01 (NEW)**
**Prompt:** Brief hopeful musical release, tension dissolving into warmth **Duration:** 5 seconds **Used:** Scene 1 resolution
```

### AMBIENCE (heading blocks)

```markdown
## **AMBIENCE**

### **AMB-DINER-MORNING-01 (REUSE)**
**Prompt:** Morning diner ambience, coffee machine hissing, occasional plate clink, subdued atmosphere **Duration:** Loop **Used:** Scene 1

### **AMB-QUARRY-WINTER-01 (REUSE)**
**Prompt:** Winter wind moaning through marble canyon, vast outdoor space, twilight **Duration:** Loop **Used:** Scene 3
```

### SOUND EFFECTS (tables per scene)

```markdown
## **SOUND EFFECTS**

### Scene 1: Morrison's Diner

| Asset Name | Prompt | Placement |
| ----- | ----- | ----- |
| SFX-DOOR-BELL-01 (REUSE) | Classic diner door opening with small bell chiming | Karen's entrance |
| SFX-BOOTS-STAMP-01 (REUSE) | Snow being stamped off boots on doormat | Karen entering |
| SFX-COFFEE-POUR-01 (REUSE) | Coffee being poured into ceramic mug | Waitress refilling |
| SFX-WHISPER-ECHO-01 (NEW) | Ethereal whispered voice with unnatural reverb | The anomaly begins |
```

### Cues sheet rules

- **Asset ID format**: `TYPE-DESCRIPTION-NN` (e.g., `SFX-DOOR-BELL-01`, `MUS-THEME-MAIN-01`, `AMB-DINER-MORNING-01`)
- **`(REUSE)`**: Asset exists in the SFX library — will NOT be generated via API
- **`(NEW)`**: Asset needs to be generated — will cost API credits
- **Prompt**: The ElevenLabs generation prompt (kept for documentation even on REUSE assets)
- **Duration**: Seconds for one-shot effects, `Loop` for ambience that tiles continuously
- **Used**: Where in the episode this asset appears
- **API limit**: Maximum generation duration is 30 seconds. Assets longer than 30s will be capped

## Common Reusable Asset Categories

These categories of sounds are well-represented in the existing library. Always check the inventory before writing a `(NEW)` entry:

### Ambience
- Diner/cafe (morning, evening, quiet, busy)
- Radio booth/studio (various intimacy levels)
- Outdoor winter (wind, quarry, walking)
- Indoor (sitting room, fireplace, old building settling)
- City street (morning, rain, distant traffic)

### Foley / SFX
- Coffee (pouring, mug set down, machine gurgling)
- Doors (diner door with bell, wooden door, soft close, key turning)
- Footsteps (wood floor, snow, linoleum, stairs, heels, boots)
- Paper (rustling, sliding, envelope tear, unfolding)
- Phone (buzzing, vibrating, different tones)
- Furniture (chair creak, booth sliding, couch sitting)
- Fire (crackling, popping, continuous)
- Clock ticking
- Clothing (coat rustling, parka, fabric)

### Music
- Main theme variations (full, brief sting, fades under, warm version)
- Emotional swells (strings, piano)
- Contemplative underscore
- Tension/relief stings
- Cosmic/ambient tones
- Intro/outro themes

### Utility
- `beat.mp3` — standard 1-second silence/transition
- `long-beat.mp3` — extended silence

## Consistency Guidelines

1. **Reuse the same asset ID** when the same sound recurs within an episode (e.g., every diner scene uses `AMB-DINER-MORNING-01`)
2. **Reuse asset IDs across episodes** for recurring locations and effects — this is the primary credit-saving mechanism
3. **Keep direction text consistent** — `[SFX: DINER DOOR OPENS, BELL CHIMES]` should be the same text every time that sound is needed, across all episodes
4. **Match existing sfx config keys** — when a previous episode used a specific direction text, reuse it exactly so the `source` mapping carries over
5. **Prefer specific descriptions** — `[SFX: CERAMIC MUG SET DOWN - GENTLE]` is better than `[SFX: sound of a cup]` because it maps to a specific asset
