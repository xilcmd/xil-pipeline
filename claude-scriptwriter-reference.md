# xil-pipeline Script & Cues Writing Reference

You are helping write production scripts and sound cues sheets for an audio drama podcast. The scripts you produce will be processed by the xil-pipeline toolset, which generates voice audio via ElevenLabs TTS and sound effects via the ElevenLabs Sound Effects API.

**Critical constraint:** Every second of SFX generation costs ~40 API credits. The project has an existing SFX library with 250+ pre-generated assets. You MUST reuse these assets whenever possible instead of inventing new sounds. The SFX inventory is provided as a companion JSON file.

## Production Script Format

The pipeline parser expects this exact markdown format:

```text
Show Name Season N: Episode N: "Episode Title" Arc: "Season Arc Title"

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

- **Header line**: `Show Name Season N: Episode N: "Title" Arc: "Season Arc Title"` (first line, no markup; `Arc:` is optional but enables `{season_title}` in preamble/postamble)
- **Cast block**: `* NAME — description` (one per line, before first `===`)
- **Section dividers**: `===` on its own line
- **Section headers** (on own line, no brackets) — must be one of the recognised names below; unrecognised headers are silently ignored (run `xil scan <script.md>` to catch them):

  | Header | Notes |
  |--------|-------|
  | `PREAMBLE` | Broadcast intro — place before COLD OPEN; seq numbers are contiguous with the episode |
  | `COLD OPEN` | |
  | `OPENING CREDITS` | |
  | `ACT ONE` / `ACT 1` | Numeral variants accepted |
  | `ACT TWO` / `ACT 2` | |
  | `ACT THREE` / `ACT 3` | |
  | `ACT FOUR` / `ACT 4` | |
  | `MID-EPISODE BREAK` | |
  | `CLOSING` | Variants: `CLOSING — RADIO STATION`, `CLOSING — ADAM'S SIGN-OFF` |
  | `POST-INTERVIEW` | Bonus commentary after the episode closes |
  | `POST-CREDITS SCENE` | |
  | `PRODUCTION NOTES` | Internal notes; included in parsed output |
  | `POSTAMBLE` | Broadcast outro — place after the last section, before `END OF EPISODE` |
  | `CHAPTER ONE` / `CHAPTER 1` | Audiobook format |
  | `CHAPTER TWO` / `CHAPTER 2` | Audiobook format |
  | `CHAPTER THREE` / `CHAPTER 3` | Audiobook format |

- **Scene headers**: `SCENE N: LOCATION NAME` (on own line)
- **Dialogue**: Speaker name on one line, dialogue text on the next
- **Acting directions**: In parentheses after speaker name: `CHARACTER (whispering)`
- **Directions**: In square brackets: `[SFX: ...]`, `[AMBIENCE: ...]`, `[MUSIC: ...]`, `[BEAT]`, `[BEAT — N SECONDS]`, `[VINTAGE FILTER ENGAGES]`, `[VINTAGE FILTER DISENGAGES]`, `[FILM AUDIO ENGAGES]`, `[FILM AUDIO DISENGAGES]`, `[SPEAKERPHONE ENGAGES]`, `[SPEAKERPHONE DISENGAGES]`
- **End marker**: `END OF EPISODE` (stops parsing)
- **Ambience stop**: `[AMBIENCE: STOP]` or `[AMBIENCE: description FADES OUT]` to end a looping ambience

## Direction Text Is an SFX Config Key

Every `[SFX: ...]`, `[MUSIC: ...]`, `[AMBIENCE: ...]`, and `[BEAT]` direction becomes
the exact key in `sfx_<slug>_TAG.json`. Matching is case-sensitive, punctuation-sensitive,
and Unicode-sensitive.

**Rules:**

- **Reuse text verbatim across episodes.** If `[SFX: DINER DOOR OPENS, BELL CHIMES]`
  appeared in S02E01, use that exact string in every subsequent episode — the shared
  asset in `SFX/` is reused automatically, costing zero API credits.
- **Use commas, not em dashes, as separators in MUSIC/AMBIENCE descriptions.**
  Write `[MUSIC: SHOW THEME, FADES UNDER ADAM]` — not `[MUSIC: SHOW THEME — FADES UNDER ADAM]`.
  Em dashes (—) and hyphens (-) look similar in many editors but produce different config
  keys, silently orphaning the stem so the old file is never regenerated.
- **Avoid trailing punctuation differences** — `[SFX: DOOR OPENS]` and `[SFX: DOOR OPENS.]`
  are different keys.
- **Capitalisation matters** — `[SFX: PHONE BUZZ]` ≠ `[SFX: phone buzz]`.

**Audit after writing a new episode:**

```bash
xil csv-join --episode S03E02
```

The annotated CSV shows which direction entries matched an SFX config key and which
did not. Blank SFX prompt columns indicate a key mismatch — fix the script text or the
config before running `xil produce`.

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

### How to find matching assets — VERBATIM FILENAMES ONLY

**⛔ Never construct or guess a filename.** Even an obvious-seeming name like
`sfx_radio-static-click-off.mp3` may be wrong — the real file is `sfx_radio-click-off.mp3`.
Invented filenames cause production failures ("SFX source file missing" errors).

**Workflow — use this order:**
1. Open **`sfx_pipe_hints.md`** (included in this kit) and search for the sound you need.
   Each line is a ready-to-paste pipe-hint: `[DIRECTION TEXT | filename.mp3]`
2. Copy the matching line **verbatim** — both the direction text and the filename. Do not paraphrase.
3. If no match in `sfx_pipe_hints.md`: check `sfx_inventory.json` by searching the
   `filename` and `prompt` fields for keywords.
4. If still no match: write the direction **without a pipe-hint**. Mark the asset as `(NEW)` in
   the cues sheet. **Do NOT guess at a filename.**

**`sfx_pipe_hints.md` is the primary reference.** Use the JSON only for untitled assets
(those that appear in the "Untitled Assets" section of the cheatsheet).

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

## Preamble and Postamble

The episode open and close announcements are written directly in the production script as
`PREAMBLE` and `POSTAMBLE` section blocks. Place `PREAMBLE` before `COLD OPEN` and
`POSTAMBLE` after the final episode section, before `END OF EPISODE`.

```
===

PREAMBLE

TINA  This is the Berkshire Talking Chronicle…
TINA  Today on The 4 1 3, Season 4, Episode 4, Porch.
TINA  Thank you for listening.

[INTRO MUSIC]

===

COLD OPEN
…

===

POSTAMBLE

[OUTRO MUSIC]

TINA  This is Tina Brissette, your host for The 4 1 3…

===

END OF EPISODE
```

The cast config `preamble`/`postamble` blocks control **TTS speed only** — no text is stored
there. The block must specify `speaker` (a cast key) and optionally `speed` (default `1.0`):

```json
"preamble": { "speaker": "tina", "speed": 0.85 },
"postamble": { "speaker": "tina", "speed": 0.85 }
```

Use native v3 audio tags (`[pause]`, `[long pause]`) inline in the dialogue text within
the script section. SSML (`<break time="1s"/>`) is not supported in `eleven_v3`.

## Vintage Filter

Use `[VINTAGE FILTER: ENGAGES]` and `[VINTAGE FILTER: DISENGAGES]` span markers to route
a character's voice through the vintage filter DAW layer (telephone / radio effect):

```
[VINTAGE FILTER: ENGAGES]

DISPATCH
(through radio, clipped)
Marlowe. Second floor found something. You'll want to come up.

[VINTAGE FILTER: DISENGAGES]
```

In the cast config, mark the character with `"filter": "vintage"`:

```json
"dispatch": {
  "full_name": "Dispatch",
  "voice_id": "...",
  "pan": 0.0,
  "filter": "vintage",
  "role": "Voice only, heard through Marlowe's radio — VINTAGE FILTER"
}
```

Treated dialogue stays in the `dialogue` layer. The separate `vintage_filter` WAV layer
written by XILP005 / XILP011 carries record-player crackle for the span, not the voices.

**Note:** the pairing validator matches `[VINTAGE FILTER ENGAGES]` without the colon, which
is the spelling every production script uses. The colon form above is accepted by the mixer
but is invisible to `xil scan`, so prefer the colon-free spelling in new scripts.

## Film Audio

Use `[FILM AUDIO ENGAGES]` and `[FILM AUDIO DISENGAGES]` span markers for dialogue heard
from a film print — warm and reflective, with a rolled-off top end and an audible grain:

```
[FILM AUDIO ENGAGES]

MARGARET
(warm, at peace)
It was a good year. The best one, maybe.

[FILM AUDIO DISENGAGES]
```

Or mark a character whose every line is film audio with `"filter": "film"` in the cast
config, which needs no span markers at all:

```json
"margaret": {
  "full_name": "Margaret Ellis",
  "voice_id": "...",
  "pan": 0.0,
  "filter": "film",
  "role": "Heard only through the restoration print — FILM AUDIO"
}
```

Both spellings (`[FILM AUDIO ENGAGES]` and `[FILM AUDIO: ENGAGES]`) are accepted. Markers
must be paired — an unclosed span fails `xil scan`. They generate no audio and cost no API
credits.

Avoid doing both for the same character: a speaker with `"filter": "film"` speaking inside a
`FILM AUDIO` span is treated twice, which doubles the compression and grain.

## Speakerphone

Narrow-band with saturation, hard levelling and a short room slap — a handset lying on a
table. It is a separate chain from `phone`, not a stronger version of it.

Use `[SPEAKERPHONE ENGAGES]` / `[SPEAKERPHONE DISENGAGES]` around the lines coming *out of
the phone*. **The span treats every line it encloses**, so in a call scene bracket each
remote line rather than the whole call — otherwise the people in the room get filtered too:

```
[SPEAKERPHONE ENGAGES]

KAREN
Where are you? I've been calling.

[SPEAKERPHONE DISENGAGES]

ADAM
(to Maya, off the phone)
Don't say anything.

[SPEAKERPHONE ENGAGES]

KAREN
Adam. I can hear you.

[SPEAKERPHONE DISENGAGES]
```

This is the reason to prefer markers over the cast config: a character who is on the phone
in one scene and in the room in another needs **no second cast entry**. The script alone
decides, line by line.

Setting `"filter": "speakerphone"` on the character still works and is the better choice
when a voice is *only ever* heard through a phone for the whole episode.

Both spellings (`[SPEAKERPHONE ENGAGES]` and `[SPEAKERPHONE: ENGAGES]`) are accepted.
Markers must be paired — an unclosed span fails `xil scan`. They generate no audio and cost
no API credits.

## Consistency Guidelines

1. **Reuse the same asset ID** when the same sound recurs within an episode (e.g., every diner scene uses `AMB-DINER-MORNING-01`)
2. **Reuse asset IDs across episodes** for recurring locations and effects — this is the primary credit-saving mechanism
3. **Keep direction text consistent** — `[SFX: DINER DOOR OPENS, BELL CHIMES]` should be the same text every time that sound is needed, across all episodes
4. **Match existing sfx config keys** — when a previous episode used a specific direction text, reuse it exactly so the `source` mapping carries over
5. **Prefer specific descriptions** — `[SFX: CERAMIC MUG SET DOWN - GENTLE]` is better than `[SFX: sound of a cup]` because it maps to a specific asset
