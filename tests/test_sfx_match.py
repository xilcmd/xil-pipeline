# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILU022_sfx_match.py — reconciling drifted cues against the library.

The fixtures write real MP3s with real ID3 tags rather than stubbing the record
layer, because the whole premise of the tool is that ``TIT2`` describes an asset
better than its filename does — and in this library the two routinely disagree.
"""

import json

import pytest

from xil_pipeline.sfx_common import sfx_edits_path, slugify_effect_key
from xil_pipeline.XILU022_sfx_match import (
    analyze,
    apply_match,
    build_exact_index,
    build_pool,
    load_assets,
    match_cue,
    render_hints,
    tokenize,
)


def _make_mp3(path, title=None, prompt=None, duration_ms=80):
    """Write a real MP3 at *path*, optionally tagged like ``tag_mp3`` would."""
    from mutagen.id3 import ID3, TIT2, USLT, ID3NoHeaderError
    from pydub import AudioSegment

    path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.silent(duration=duration_ms).export(str(path), format="mp3")
    if title is None and prompt is None:
        return path
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()
    if title:
        tags.add(TIT2(encoding=3, text=title))
    if prompt:
        tags.add(USLT(encoding=3, lang="eng", desc="", text=prompt))
    tags.save(str(path))
    return path


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A workspace whose SFX library mirrors the real one's quirks."""
    monkeypatch.setenv("XIL_PROJECTROOT", str(tmp_path))
    sfx = tmp_path / "SFX"

    # The exact-slug case, present under its canonical slug name in the shared
    # root AND as a differently named twin, same title, inside the cue's own
    # show.  Scope preference hands the dedupe group to the twin, so the
    # canonical copy is the one that disappears from the pool — exactly the
    # arrangement that made a real EXACT hit report as REVIEW.
    _make_mp3(sfx / "sfx_phone-screen-tap.mp3", title="SFX: PHONE SCREEN TAP")
    _make_mp3(sfx / "myshow" / "SFX-_PHONE_TAP,_SEND_TONE.mp3", title="SFX: PHONE SCREEN TAP")

    # A filename that disagrees with its own title — the drift the tool exists for.
    _make_mp3(sfx / "sibling" / "SFX-_CHAIRS_—_SOFT_SCRAPING.mp3",
              title="SFX: CHAIR SCRAPING, MAYA STANDING")

    # A three-way tie at perfect coverage: nothing here should ever auto-apply.
    for name in ("SFX-_FOOTSTEPS_APPROACHING_IN_HALL.mp3",
                 "SFX-_FOOTSTEPS_APPROACHING_ON_GRAVEL.mp3",
                 "SFX-_FOOTSTEPS_APPROACHING_SLOWLY.mp3"):
        _make_mp3(sfx / "sibling" / name,
                  title=f"SFX: {name[5:-4].replace('_', ' ')}")

    # The mis-rank regression: "bag" outscores "coffee mug" on raw token count.
    _make_mp3(sfx / "bag_being_set_down.mp3", title="SFX: BAG BEING SET DOWN")
    _make_mp3(sfx / "sfx_coffee-mug-set-down-ceramic.mp3",
              title="SFX: COFFEE MUG SET DOWN, CERAMIC")

    # Category bait: a MUSIC asset whose words would otherwise cover an AMBIENCE cue.
    _make_mp3(sfx / "music_tense-room-hum-bed.mp3", title="MUSIC: TENSE ROOM HUM BED")

    # An untitled asset, matched on filename alone.
    _make_mp3(sfx / "sfx_typewriter-carriage-return.mp3")

    # A prompt-only asset: no title, but USLT describes it.
    _make_mp3(sfx / "ELEV-9931.mp3", prompt="a kettle whistling on a gas hob")

    (tmp_path / "configs" / "myshow").mkdir(parents=True)
    return tmp_path


def _write_config(workspace, tag, effects, show="myshow"):
    path = workspace / "configs" / show / f"sfx_{tag}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"effects": effects}), encoding="utf-8")
    return path


@pytest.fixture
def pool(workspace):
    assets = load_assets(workspace)
    return assets, build_pool(assets, own_show="myshow"), build_exact_index(assets, "myshow")


# ── tokenising ────────────────────────────────────────────────────────────────

class TestTokenize:
    def test_strips_category_prefix_and_connectives(self):
        assert tokenize("SFX: DOOR OPENS, BELL CHIMES") == {"door", "open", "bell", "chime"}

    def test_folds_plurals_so_chairs_matches_chair(self):
        assert tokenize("CHAIRS") == tokenize("CHAIR")

    def test_keeps_direction_words_that_carry_meaning(self):
        # "up"/"out"/"down" read like filler but distinguish real cues:
        # "THEME, UP BRIEFLY, THEN OUT" is not "THEME".
        assert {"up", "out"} <= tokenize("MUSIC: THEME, UP BRIEFLY, THEN OUT")
        assert "down" in tokenize("SFX: MUG SET DOWN")

    def test_double_s_words_are_not_mangled(self):
        assert "glass" in tokenize("SFX: GLASS BREAKING")


# ── matching ──────────────────────────────────────────────────────────────────

class TestMatchCue:
    def test_exact_slug_short_circuits(self, pool):
        _, assets_pool, index = pool
        tier, cands = match_cue("SFX: PHONE SCREEN TAP", assets_pool, index)
        assert tier == "EXACT"
        assert cands[0].asset.filename == "sfx_phone-screen-tap.mp3"

    def test_exact_hit_survives_dedupe_collapsing_its_group(self, pool):
        """The canonical copy shares a title with a twin, so dedupe drops one.

        Searching only the deduplicated pool reported REVIEW for a cue the
        library answers exactly — the exact index exists to prevent that.
        """
        _, assets_pool, index = pool
        names = {a.filename for a in assets_pool}
        assert "sfx_phone-screen-tap.mp3" not in names, \
            "fixture no longer exercises the collapse this test guards"
        assert match_cue("SFX: PHONE SCREEN TAP", assets_pool, None)[0] != "EXACT"
        assert match_cue("SFX: PHONE SCREEN TAP", assets_pool, index)[0] == "EXACT"

    def test_matches_on_title_when_filename_disagrees(self, pool):
        _, assets_pool, index = pool
        tier, cands = match_cue("SFX: MAYA STANDING, CHAIR SCRAPING", assets_pool, index)
        assert tier in ("STRONG", "REVIEW")
        assert cands[0].asset.filename == "SFX-_CHAIRS_—_SOFT_SCRAPING.mp3"
        assert cands[0].coverage == 1.0

    def test_verbose_title_wins_on_coverage(self, pool):
        """A longer title containing every cue word is a good match.

        Jaccard alone rejects these — it penalises the extra words — and that is
        why coverage leads the ranking.
        """
        _, assets_pool, index = pool
        _, cands = match_cue("SFX: COFFEE MUG SET DOWN", assets_pool, index)
        best = cands[0]
        assert best.asset.filename == "sfx_coffee-mug-set-down-ceramic.mp3"
        assert best.coverage > best.jaccard

    def test_category_gate_blocks_cross_category_match(self, pool):
        """An AMBIENCE cue must never be answered by a MUSIC asset."""
        _, assets_pool, index = pool
        _, cands = match_cue("AMBIENCE: TENSE ROOM HUM", assets_pool, index)
        assert all(c.asset.filename != "music_tense-room-hum-bed.mp3" for c in cands)

    def test_margin_rule_demotes_a_tie_to_review(self, pool):
        """Three candidates at coverage 1.00 is a human decision, not an auto-apply."""
        _, assets_pool, index = pool
        tier, cands = match_cue("SFX: FOOTSTEPS APPROACHING", assets_pool, index)
        assert len([c for c in cands if c.coverage == 1.0]) >= 2
        assert tier == "REVIEW"

    def test_mis_rank_is_not_auto_applied(self, pool):
        """"bag being set down" outranks the coffee mug on token count alone."""
        _, assets_pool, index = pool
        tier, cands = match_cue("SFX: COFFEE CUP BEING SET DOWN", assets_pool, index)
        assert tier != "STRONG"
        assert any(c.asset.filename == "bag_being_set_down.mp3" for c in cands)

    def test_untitled_asset_falls_back_to_filename(self, pool):
        _, assets_pool, index = pool
        _, cands = match_cue("SFX: TYPEWRITER CARRIAGE RETURN", assets_pool, index)
        assert cands and cands[0].asset.filename == "sfx_typewriter-carriage-return.mp3"

    def test_prompt_is_used_when_there_is_no_title(self, pool):
        _, assets_pool, index = pool
        _, cands = match_cue("SFX: KETTLE WHISTLING", assets_pool, index)
        assert cands and cands[0].asset.filename == "ELEV-9931.mp3"

    def test_no_candidate_above_the_floor_is_none(self, pool):
        _, assets_pool, index = pool
        tier, cands = match_cue("SFX: DISTANT WHALE SONG UNDERWATER", assets_pool, index)
        assert tier == "NONE"
        assert cands == []


class TestBuildPool:
    def test_dedupe_collapses_copies_and_records_their_scopes(self, workspace):
        sfx = workspace / "SFX"
        for scope in ("", "myshow", "sibling"):
            _make_mp3(sfx / scope / "sfx_owl-hoot.mp3", title="SFX: OWL HOOT")
        assets = load_assets(workspace)
        deduped = [a for a in build_pool(assets, own_show="myshow")
                   if a.filename == "sfx_owl-hoot.mp3"]
        assert len(deduped) == 1
        assert deduped[0].scope == "myshow", "the cue's own show should win"
        assert deduped[0].also_in == ["SFX/", "sibling"]

    def test_also_in_is_not_shared_between_shows(self, workspace):
        """also_in is per-show, so building one pool must not corrupt another."""
        assets = load_assets(workspace)
        mine = build_pool(assets, own_show="myshow")
        theirs = build_pool(assets, own_show="sibling")
        assert {a.filename for a in mine} and {a.filename for a in theirs}
        # Rebuilding for myshow must reproduce the original result exactly.
        again = build_pool(assets, own_show="myshow")
        assert {(a.filename, tuple(a.also_in)) for a in mine} == \
               {(a.filename, tuple(a.also_in)) for a in again}


# ── sweep ─────────────────────────────────────────────────────────────────────

class TestAnalyze:
    def test_only_unresolvable_sources_are_reported(self, workspace):
        _write_config(workspace, "S01E01", {
            "SFX: PHONE SCREEN TAP": {"source": "NEW STEM NEEDED: sfx_phone-tap.mp3"},
            "SFX: RESOLVES FINE": {"source": "SFX/bag_being_set_down.mp3"},
            "SFX: NO SOURCE AT ALL": {"prompt": "a thing"},
        })
        matches, scanned = analyze(workspace, show="myshow")
        assert scanned == 1
        assert [m.cue for m in matches] == ["SFX: PHONE SCREEN TAP"]

    def test_library_is_not_scanned_when_nothing_is_broken(self, workspace, monkeypatch):
        _write_config(workspace, "S01E01",
                      {"SFX: FINE": {"source": "SFX/bag_being_set_down.mp3"}})

        def _boom(*a, **k):
            raise AssertionError("the library must not be indexed for a clean config")

        monkeypatch.setattr("xil_pipeline.XILU022_sfx_match.load_assets", _boom)
        matches, scanned = analyze(workspace, show="myshow")
        assert matches == [] and scanned == 1


# ── repair ────────────────────────────────────────────────────────────────────

class TestApplyMatch:
    def _one(self, workspace, cue, source="NEW STEM NEEDED: nope.mp3"):
        _write_config(workspace, "S01E01", {cue: {"source": source}})
        matches, _ = analyze(workspace, show="myshow")
        return matches[0]

    def test_copies_under_the_cue_slug_and_journals_the_source(self, workspace):
        cue = "SFX: MAYA STANDING, CHAIR SCRAPING"
        match = self._one(workspace, cue)
        rel = apply_match(match, workspace)

        dest = workspace / "SFX" / "myshow" / f"{slugify_effect_key(cue)}.mp3"
        assert dest.is_file()
        assert rel == f"SFX/myshow/{slugify_effect_key(cue)}.mp3"

        records = [json.loads(x) for x in
                   open(sfx_edits_path(match.config_path), encoding="utf-8")]
        assert [(r["key"], r["fields"]) for r in records] == [(cue, {"source": rel})]

    def test_config_is_written_too_so_produce_is_actually_unblocked(self, workspace):
        """The journal alone leaves produce blocked — it reads the config."""
        cue = "SFX: MAYA STANDING, CHAIR SCRAPING"
        match = self._one(workspace, cue)
        rel = apply_match(match, workspace)

        config = json.loads(open(match.config_path, encoding="utf-8").read())
        assert config["effects"][cue]["source"] == rel

    def test_config_write_preserves_the_cue_s_other_fields(self, workspace):
        cue = "SFX: MAYA STANDING, CHAIR SCRAPING"
        _write_config(workspace, "S01E01",
                      {cue: {"source": "NEW STEM NEEDED: nope.mp3",
                             "duration_seconds": 5.0, "volume_percentage": 40}})
        match = analyze(workspace, show="myshow")[0][0]
        apply_match(match, workspace)

        effect = json.loads(open(match.config_path, encoding="utf-8").read())["effects"][cue]
        assert effect["duration_seconds"] == 5.0
        assert effect["volume_percentage"] == 40

    def test_copy_is_retitled_to_the_cue_so_the_cheatsheet_picks_it_up(self, workspace):
        from mutagen.id3 import ID3

        cue = "SFX: MAYA STANDING, CHAIR SCRAPING"
        match = self._one(workspace, cue)
        apply_match(match, workspace)

        dest = workspace / "SFX" / "myshow" / f"{slugify_effect_key(cue)}.mp3"
        tags = ID3(str(dest))
        assert str(tags["TIT2"]) == cue
        assert "copied from" in str(tags.getall("COMM")[0])

    def test_retitling_preserves_the_generation_prompt(self, workspace):
        from mutagen.id3 import ID3

        cue = "SFX: KETTLE WHISTLING"
        match = self._one(workspace, cue)
        apply_match(match, workspace)

        dest = workspace / "SFX" / "myshow" / f"{slugify_effect_key(cue)}.mp3"
        lyrics = ID3(str(dest)).getall("USLT")
        assert lyrics and "kettle whistling" in str(lyrics[0])

    def test_second_run_reports_exact_and_writes_nothing_new(self, workspace):
        """The cue-slug destination is what makes the repair stick."""
        cue = "SFX: MAYA STANDING, CHAIR SCRAPING"
        match = self._one(workspace, cue)
        rel = apply_match(match, workspace)

        # Re-point the config at the applied source, as replay would.
        _write_config(workspace, "S01E01", {cue: {"source": rel}})
        again, _ = analyze(workspace, show="myshow")
        assert again == [], "the source now resolves, so the cue is no longer reported"

        # And a cue still pointing at a placeholder now resolves exactly.
        _write_config(workspace, "S01E01", {cue: {"source": "NEW STEM NEEDED: x.mp3"}})
        rematch, _ = analyze(workspace, show="myshow")
        assert rematch[0].tier == "EXACT"

    def test_dry_run_writes_nothing_at_all(self, workspace):
        cue = "SFX: MAYA STANDING, CHAIR SCRAPING"
        match = self._one(workspace, cue)
        before = open(match.config_path, encoding="utf-8").read()
        apply_match(match, workspace, dry_run=True)

        dest = workspace / "SFX" / "myshow" / f"{slugify_effect_key(cue)}.mp3"
        assert not dest.exists()
        assert not (workspace / "configs" / "myshow" / "sfx_S01E01_edits.jsonl").exists()
        assert open(match.config_path, encoding="utf-8").read() == before

    def test_nothing_to_apply_when_there_are_no_candidates(self, workspace):
        match = self._one(workspace, "SFX: DISTANT WHALE SONG UNDERWATER")
        assert match.tier == "NONE"
        assert apply_match(match, workspace) is None


# ── hints ─────────────────────────────────────────────────────────────────────

class TestRenderHints:
    def test_emits_the_pipe_hint_form_with_alternates(self, workspace):
        _write_config(workspace, "S01E01", {
            "SFX: FOOTSTEPS APPROACHING": {"source": "NEW STEM NEEDED: nope.mp3"},
        })
        matches, _ = analyze(workspace, show="myshow")
        text = render_hints(matches, workspace)

        cue = "SFX: FOOTSTEPS APPROACHING"
        assert f"[{cue} | {slugify_effect_key(cue)}.mp3]" in text
        # Alternates are commented so the block pastes in cleanly either way.
        assert text.count("#   cov ") >= 2

    def test_lists_cues_that_need_generation_separately(self, workspace):
        _write_config(workspace, "S01E01", {
            "SFX: DISTANT WHALE SONG UNDERWATER": {"source": "NEW STEM NEEDED: w.mp3"},
        })
        matches, _ = analyze(workspace, show="myshow")
        text = render_hints(matches, workspace)
        assert "needs generation" in text
        assert "SFX: DISTANT WHALE SONG UNDERWATER" in text
