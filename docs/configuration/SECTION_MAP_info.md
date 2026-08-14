# SECTION_MAP — Reference and Pipeline Impact

## Definition

Defined in `XILP001_script_parser.py` at module level:

```python
SECTION_MAP = {
    "COLD OPEN":                        "cold-open",
    "OPENING CREDITS":                  "opening-credits",
    "ACT ONE":                          "act1",
    "ACT 1":                            "act1",              # numeral variant
    "ACT TWO":                          "act2",
    "ACT 2":                            "act2",              # numeral variant
    "ACT THREE":                        "act3",
    "ACT 3":                            "act3",              # S02E02 three-act structure
    "ACT FOUR":                         "act4",
    "ACT 4":                            "act4",
    "MID-EPISODE BREAK":                "mid-break",
    "CLOSING":                          "closing",
    "CLOSING — RADIO STATION":          "closing",           # S02E01 variant
    "CLOSING — ADAM'S SIGN-OFF":        "closing",           # S02E02 straight apostrophe
    "CLOSING — ADAM\u2019S SIGN-OFF":   "closing",           # S02E02 curly apostrophe
    "POST-INTERVIEW":                   "post-interview",
    "POST-INTERVIEW: ADAM & TINA":      "post-interview",    # S02E02 variant
    "POST-CREDITS SCENE":              "post-credits",       # S01E03
    "DEZ'S CLOSING NARRATION":          "dez-closing",       # S02E03 straight apostrophe
    "DEZ\u2019S CLOSING NARRATION":     "dez-closing",       # S02E03 curly apostrophe
    "PRODUCTION NOTES":                 "production-notes",  # S02E03 preamble
    "PREAMBLE":                          "preamble",
    "POSTAMBLE":                         "postamble",
}
```

Keys are the exact strings that appear in the production script (after markdown formatting is stripped). Values are URL-safe slugs stamped onto every parsed entry and embedded in every stem filename.

Multiple keys may map to the same slug (e.g. `"ACT ONE"` and `"ACT 1"` both produce `"act1"`; several CLOSING variants all produce `"closing"`). This handles script-to-script variation without requiring authors to use a single canonical form.

Note: Section headers may appear as plain text (`COLD OPEN`) in S01E01-style scripts or as markdown headings (`## **COLD OPEN**`) in S01E02+ scripts. The `strip_markdown_formatting()` pass normalizes both to bare text before `is_section_header()` checks against these keys.

### Known slugs by episode

| Slug | Episodes | Display variants |
|------|----------|-----------------|
| `cold-open` | S01E01–S02E03 | COLD OPEN |
| `opening-credits` | S01E01 | OPENING CREDITS |
| `act1` | S01E01–S02E03 | ACT ONE, ACT 1 |
| `act2` | S01E01–S02E03 | ACT TWO, ACT 2 |
| `act3` | S02E02–S02E03 | ACT THREE, ACT 3 |
| `act4` | (reserved) | ACT FOUR, ACT 4 |
| `mid-break` | S01E01 | MID-EPISODE BREAK |
| `closing` | S01E01–S02E03 | CLOSING, CLOSING — RADIO STATION, CLOSING — ADAM'S SIGN-OFF |
| `post-interview` | S02E02–S02E03 | POST-INTERVIEW, POST-INTERVIEW: ADAM & TINA |
| `post-credits` | S01E03 | POST-CREDITS SCENE |
| `dez-closing` | S02E03 | DEZ'S CLOSING NARRATION |
| `production-notes` | S02E03 | PRODUCTION NOTES |
| `preamble` | all | PREAMBLE |
| `postamble` | all | POSTAMBLE |

---

## Pre-Flight Detection — XILP000

`XILP000_script_scanner.py` imports `SECTION_MAP` directly from XILP001 and scans the raw script for recognized vs unrecognized section headers **before** parsing. This is the primary defence against the silent failure risk described below.

- Exit code 0 = all sections recognized (safe to run XILP001)
- Exit code 1 = unrecognized section headers found (action needed: add to `SECTION_MAP`)
- No side effects — reads only the script file

```bash
python XILP000_script_scanner.py "scripts/<script>.md"
```

---

## Effect in XILP001 — Three Roles

### 1. Gate-keeping

```python
def is_section_header(line: str) -> bool:
    return line.strip() in SECTION_MAP
```

A line is only recognized as a section header if its stripped text exactly matches a key. Unrecognized headers fall through the classifier loop and are **silently skipped** — they generate no entry and do not update the section state.

### 2. State machine reset

```python
if is_section_header(line):
    current_section = SECTION_MAP[line.strip()]
    current_scene = None
```

When a section header is matched:
- `current_section` is set to the slug value
- `current_scene` is reset to `None`

Every subsequent entry inherits `current_section` until the next section header is encountered. Lines appearing before the first section header carry `"section": None`.

### 3. Slug stamped on every entry

All four entry types receive the current section slug:

| Entry type | Section field set? |
|------------|-------------------|
| `section_header` | Yes (the header itself) |
| `scene_header` | Yes |
| `direction` | Yes |
| `dialogue` | Yes |

The stats collector uses this to build `stats["sections"]` — a sorted list of unique section slugs seen in the script.

---

## Effect in XILP002 — Stem Filename Composition

```python
stem_name = f"{entry['seq']:03d}_{entry['section']}"
if entry.get("scene"):
    stem_name += f"-{entry['scene']}"
stem_name += f"_{entry['speaker']}"
```

The section slug becomes the **middle segment of every stem filename**:

```
003_cold-open_adam.mp3
028_act1-scene-1_rian.mp3
102_act2-scene-5_mr_patterson.mp3
285_closing_sfx.mp3
310_dez-closing_sfx.mp3
```

This has a critical consequence for **duplicate stem prevention**: `generate_voices()` skips a stem if its file already exists on disk. If SECTION_MAP is changed after stems have been generated, the path changes and existing stems will not be found — XILP002 will re-generate them and consume additional TTS quota.

---

## Effect in XILP003 — None (Indirect Only)

XILP003 loads stems alphabetically via `sorted(glob(...))`. Since stems are prefixed with the zero-padded sequence number (`003_`, `028_`), the section slug in the middle does not affect sort order. XILP003 never reads section information directly — it extracts only the speaker from the filename suffix via `rsplit("_", 1)[-1]`.

---

## Effect in XILP005 — Label Files and Timeline

XILP005 (DAW layer export) uses `collect_stem_plans()` from `mix_common.py`, which cross-references stem filenames against the parsed JSON entries index. The section slug in the filename is used for stale stem detection: when a stem's filename section segment doesn't match the parsed entry's section, it may be flagged as stale or as a duplicate.

Label track files (`{TAG}_labels_dialogue.txt`, etc.) and the timeline HTML use speaker and direction text from the parsed JSON — not the section slug directly — but the section slug determines which stems are loaded.

---

## Effect in XILP007 — Stem Migration

XILP007 (stem migrator) copies unchanged stems to new seq-numbered filenames when a script is revised. The new filename includes the **new** section slug from the revised parsed JSON. If a section boundary shifted (e.g. a line moved from `act1` to `act2`), the migrated stem gets the updated section slug in its filename automatically.

---

## Effect in XILP008 — Stale Stem Cleanup

XILP008 builds an expected filename for each parsed entry using `{seq}_{section}[-{scene}]_{speaker|sfx}`. When multiple stems exist for the same seq number, it keeps only the one whose basename matches the expected pattern — stems with a stale section slug are deleted.

---

## Effect in XILP009 — Reverse Script Generator

XILP009 reverses the section slug back to a display header using an inverted `SECTION_MAP`. When multiple display keys map to the same slug, the longest key is preferred (e.g. `act1` → `ACT ONE` rather than `ACT 1`). The reverse mapping is built at import time from XILP001's `SECTION_MAP`.

---

## Adding a New Section

To add a new section (e.g., for an episode with an `"EPILOGUE"`):

1. Add the entry to `SECTION_MAP` in `XILP001_script_parser.py`:
   ```python
   "EPILOGUE": "epilogue",
   ```
2. Run `XILP000_script_scanner.py` to verify the new section is now recognized.
3. Re-run XILP001 to regenerate the parsed JSON.
4. Re-run XILP002 — stems for the new section will have new filenames and will be generated fresh.

**Do not rename existing section slugs** after stems have been generated without also deleting the affected stems from `stems/`, or you will pay double TTS quota for those lines. Use XILP007 (stem migrator) if the script has been revised and section boundaries shifted.

---

## Silent Failure Risk

An unrecognized section header causes **silent mislabeling**: all entries after it carry the slug of the *previous* section. This is invisible in normal output — `print_summary()` only shows which sections were found, not which lines were expected to be in which section.

**Mitigation:** Always run XILP000 (pre-flight scanner) before XILP001 when onboarding a new script. XILP000 explicitly reports unrecognized section headers and exits with code 1 if any are found.

```bash
# Pre-flight scan — catches unrecognized sections before parsing
python XILP000_script_scanner.py "scripts/<script>.md"

# If all sections pass, parse
python XILP001_script_parser.py "scripts/<script>.md" --episode S01E01
```

After parsing, you can also verify `stats["sections"]` contains all expected slugs:

```bash
python -c "import json; d=json.load(open('parsed/parsed_sample_S01E01.json')); print(d['stats']['sections'])"
```
