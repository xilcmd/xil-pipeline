# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for Pydantic data models (TDD — tests written before models)."""

import pytest

from xil_pipeline import models


# ---------------------------------------------------------------------------
# Phase 0 — Foundation
# ---------------------------------------------------------------------------

class TestFoundation:
    """Verify pydantic is installed and models module is importable."""

    def test_models_module_importable(self):
        assert models is not None
        assert hasattr(models, "__doc__")

    def test_pydantic_available(self):
        import pydantic
        major = int(pydantic.VERSION.split(".")[0])
        assert major >= 2, f"Pydantic v2+ required, got {pydantic.VERSION}"


# ---------------------------------------------------------------------------
# episode_tag helper and model .tag property
# ---------------------------------------------------------------------------


class TestEpisodeTag:
    """Tests for the episode_tag() function and model .tag properties."""

    def test_with_season_and_episode(self):
        assert models.episode_tag(1, 1) == "S01E01"

    def test_with_higher_numbers(self):
        assert models.episode_tag(2, 12) == "S02E12"

    def test_without_season(self):
        assert models.episode_tag(None, 3) == "E03"

    def test_zero_padded(self):
        assert models.episode_tag(1, 1) == "S01E01"
        assert models.episode_tag(None, 1) == "E01"


class TestParsedScriptTag:
    """Tests for ParsedScript.tag property."""

    def _make(self, season=1, episode=1):
        return models.ParsedScript(
            show="THE 413", season=season, episode=episode,
            title="Test", source_file="test.md",
            entries=[], stats=models.ScriptStats(
                total_entries=0, dialogue_lines=0, direction_lines=0,
                characters_for_tts=0, speakers=[], sections=[],
            ),
        )

    def test_tag_with_season(self):
        assert self._make(season=1, episode=1).tag == "S01E01"

    def test_tag_without_season(self):
        assert self._make(season=None, episode=2).tag == "E02"


class TestCastConfigurationTag:
    """Tests for CastConfiguration.tag property."""

    def test_tag_with_season(self):
        cc = models.CastConfiguration(
            show="THE 413", season=1, episode=1, title="Test", cast={},
        )
        assert cc.tag == "S01E01"

    def test_tag_without_season(self):
        cc = models.CastConfiguration(
            show="THE 413", season=None, episode=5, title="Test", cast={},
        )
        assert cc.tag == "E05"


# ---------------------------------------------------------------------------
# Phase 1 — Script Models
# ---------------------------------------------------------------------------

from pydantic import ValidationError


class TestScriptEntry:
    """Tests for the ScriptEntry model."""

    def _make(self, **overrides):
        defaults = {
            "seq": 1,
            "type": "dialogue",
            "section": "cold-open",
            "scene": None,
            "speaker": "adam",
            "direction": "on-air voice",
            "text": "Hello world.",
            "direction_type": None,
        }
        defaults.update(overrides)
        return models.ScriptEntry(**defaults)

    def test_valid_dialogue_entry(self):
        entry = self._make()
        assert entry.seq == 1
        assert entry.type == "dialogue"
        assert entry.speaker == "adam"

    def test_valid_direction_entry(self):
        entry = self._make(
            type="direction", speaker=None, direction=None,
            text="[SFX: phone rings]", direction_type="SFX",
        )
        assert entry.type == "direction"
        assert entry.direction_type == "SFX"

    def test_valid_section_header(self):
        entry = self._make(
            type="section_header", speaker=None, direction=None,
            text="ACT ONE",
        )
        assert entry.type == "section_header"

    def test_valid_scene_header(self):
        entry = self._make(
            type="scene_header", speaker=None, direction=None,
            text="SCENE 1: The Studio",
        )
        assert entry.type == "scene_header"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            self._make(type="unknown")

    def test_seq_allows_negative(self):
        # Negative seqs are used for preamble entries (-2, -1)
        entry_neg2 = self._make(seq=-2)
        assert entry_neg2.seq == -2
        entry_neg1 = self._make(seq=-1)
        assert entry_neg1.seq == -1

    def test_seq_allows_zero(self):
        entry = self._make(seq=0)
        assert entry.seq == 0

    def test_direction_type_literal_validated(self):
        for dt in ("SFX", "MUSIC", "AMBIENCE", "BEAT", None):
            entry = self._make(direction_type=dt)
            assert entry.direction_type == dt
        with pytest.raises(ValidationError):
            self._make(direction_type="INVALID")

    def test_model_dump_roundtrip(self):
        raw = {
            "seq": 3,
            "type": "dialogue",
            "section": "cold-open",
            "scene": None,
            "speaker": "adam",
            "direction": "on-air voice, warm",
            "text": "It's 2:47 AM on a Wednesday...",
            "direction_type": None,
        }
        assert models.ScriptEntry(**raw).model_dump() == raw


class TestScriptStats:
    """Tests for the ScriptStats model."""

    def _make(self, **overrides):
        defaults = {
            "total_entries": 127,
            "dialogue_lines": 80,
            "direction_lines": 47,
            "characters_for_tts": 12463,
            "speakers": ["adam", "dez"],
            "sections": ["cold-open", "act1"],
        }
        defaults.update(overrides)
        return models.ScriptStats(**defaults)

    def test_valid_stats(self):
        stats = self._make()
        assert stats.total_entries == 127
        assert stats.speakers == ["adam", "dez"]

    def test_total_entries_non_negative(self):
        with pytest.raises(ValidationError):
            self._make(total_entries=-1)

    def test_model_dump_roundtrip(self):
        raw = {
            "total_entries": 10,
            "dialogue_lines": 6,
            "direction_lines": 4,
            "characters_for_tts": 500,
            "speakers": ["adam"],
            "sections": ["act1"],
        }
        assert models.ScriptStats(**raw).model_dump() == raw


class TestParsedScript:
    """Tests for the ParsedScript model."""

    def _make_entry(self, **overrides):
        defaults = {
            "seq": 1, "type": "dialogue", "section": "cold-open",
            "scene": None, "speaker": "adam", "direction": None,
            "text": "Hello.", "direction_type": None,
        }
        defaults.update(overrides)
        return defaults

    def _make_stats(self):
        return {
            "total_entries": 1, "dialogue_lines": 1, "direction_lines": 0,
            "characters_for_tts": 6, "speakers": ["adam"], "sections": ["cold-open"],
        }

    def test_valid_parsed_script(self):
        ps = models.ParsedScript(
            show="THE 413", episode=1, title="Test",
            source_file="test.md",
            entries=[models.ScriptEntry(**self._make_entry())],
            stats=models.ScriptStats(**self._make_stats()),
        )
        assert ps.show == "THE 413"
        assert len(ps.entries) == 1

    def test_accepts_raw_dicts(self):
        """Pydantic should coerce raw dicts into nested models."""
        ps = models.ParsedScript(
            show="THE 413", episode=1, title="Test",
            source_file="test.md",
            entries=[self._make_entry()],
            stats=self._make_stats(),
        )
        assert isinstance(ps.entries[0], models.ScriptEntry)
        assert isinstance(ps.stats, models.ScriptStats)

    def test_model_dump_roundtrip(self):
        raw = {
            "show": "THE 413", "episode": 1, "season": 1, "title": "Test",
            "source_file": "test.md",
            "entries": [self._make_entry()],
            "stats": self._make_stats(),
        }
        assert models.ParsedScript(**raw).model_dump() == raw

    def test_season_field_valid(self):
        ps = models.ParsedScript(
            show="THE 413", episode=1, season=1, title="Test",
            source_file="test.md",
            entries=[self._make_entry()],
            stats=self._make_stats(),
        )
        assert ps.season == 1

    def test_season_can_be_none(self):
        ps = models.ParsedScript(
            show="THE 413", episode=1, title="Test",
            source_file="test.md",
            entries=[self._make_entry()],
            stats=self._make_stats(),
        )
        assert ps.season is None

    def test_season_in_model_dump(self):
        ps = models.ParsedScript(
            show="THE 413", episode=1, season=2, title="Test",
            source_file="test.md",
            entries=[self._make_entry()],
            stats=self._make_stats(),
        )
        assert ps.model_dump()["season"] == 2


# ---------------------------------------------------------------------------
# Phase 2 — Cast / Production Models
# ---------------------------------------------------------------------------


class TestCastMember:
    """Tests for the CastMember model."""

    def _make(self, **overrides):
        defaults = {
            "full_name": "Adam Santos",
            "voice_id": "onwK4e9ZLuTAKqWW03F9",
            "pan": 0.0,
            "filter": False,
            "role": "Host/Narrator",
        }
        defaults.update(overrides)
        return models.CastMember(**defaults)

    def test_valid_cast_member(self):
        cm = self._make()
        assert cm.full_name == "Adam Santos"
        assert cm.pan == 0.0

    def test_pan_range_validated(self):
        self._make(pan=-1.0)  # boundary OK
        self._make(pan=1.0)   # boundary OK
        with pytest.raises(ValidationError):
            self._make(pan=1.5)
        with pytest.raises(ValidationError):
            self._make(pan=-1.5)

    def test_voice_id_non_empty(self):
        with pytest.raises(ValidationError):
            self._make(voice_id="")

    def test_voice_id_tbd_accepted(self):
        cm = self._make(voice_id="TBD")
        assert cm.voice_id == "TBD"

    def test_new_tts_fields_default_to_none(self):
        cm = self._make()
        assert cm.stability is None
        assert cm.similarity_boost is None
        assert cm.style is None
        assert cm.use_speaker_boost is None
        assert cm.language_code is None

    def test_stability_range_validated(self):
        self._make(stability=0.0)   # boundary OK
        self._make(stability=1.0)   # boundary OK
        with pytest.raises(ValidationError):
            self._make(stability=-0.1)
        with pytest.raises(ValidationError):
            self._make(stability=1.1)

    def test_similarity_boost_range_validated(self):
        self._make(similarity_boost=0.0)
        self._make(similarity_boost=1.0)
        with pytest.raises(ValidationError):
            self._make(similarity_boost=1.5)

    def test_style_range_validated(self):
        self._make(style=0.0)
        self._make(style=1.0)
        with pytest.raises(ValidationError):
            self._make(style=-0.5)

    def test_use_speaker_boost_accepted(self):
        cm = self._make(use_speaker_boost=True)
        assert cm.use_speaker_boost is True

    def test_language_code_accepted(self):
        cm = self._make(language_code="en")
        assert cm.language_code == "en"

    def test_model_dump_roundtrip(self):
        raw = {
            "full_name": "Dez Williams",
            "voice_id": "JBFqnCBsd6RMkjVDRZzb",
            "pan": -0.15,
            "filter": False,
            "role": "Supporting",
            "stability": None,
            "similarity_boost": None,
            "style": None,
            "use_speaker_boost": None,
            "language_code": None,
        }
        assert models.CastMember(**raw).model_dump() == raw

    def test_model_dump_roundtrip_with_tts_fields(self):
        raw = {
            "full_name": "Adam Santos",
            "voice_id": "onwK4e9ZLuTAKqWW03F9",
            "pan": 0.0,
            "filter": False,
            "role": "Host/Narrator",
            "stability": 0.6,
            "similarity_boost": 0.8,
            "style": 0.1,
            "use_speaker_boost": True,
            "language_code": "en",
        }
        assert models.CastMember(**raw).model_dump() == raw


class TestPreamble:
    """Tests for the Preamble model."""

    def _make(self, **overrides):
        defaults = {
            "text": "This is Tina Brissette, the producer of The 413. Today on The 4 1 3, {season_title}, Episode {episode}, {title}.",
            "speaker": "tina",
        }
        defaults.update(overrides)
        return models.Preamble(**defaults)

    def test_valid_preamble(self):
        p = self._make()
        assert p.speaker == "tina"

    def test_intro_music_source_field_absent(self):
        # intro_music_source was removed; INTRO MUSIC is now in sfx config
        p = self._make()
        assert not hasattr(p, "intro_music_source")

    def test_text_with_template_vars(self):
        p = self._make()
        formatted = p.text.format(season_title="The Letters", episode=3, title="The Bridge")
        assert "The Letters" in formatted
        assert "3" in formatted
        assert "The Bridge" in formatted

    def test_model_dump_roundtrip(self):
        raw = {
            "text": "Today on The 4 1 3, {season_title}, Episode {episode}, {title}.",
            "segments": None,
            "speaker": "tina",
            "speed": 0.85,
        }
        assert models.Preamble(**raw).model_dump() == raw

    def test_speed_default_is_none(self):
        p = self._make()
        assert p.speed is None

    def test_speed_accepted(self):
        p = self._make(speed=0.85)
        assert p.speed == 0.85

    def test_speed_range_validated(self):
        self._make(speed=0.7)   # boundary OK
        self._make(speed=1.2)   # boundary OK
        with pytest.raises(ValidationError):
            self._make(speed=0.6)
        with pytest.raises(ValidationError):
            self._make(speed=1.3)

    def test_model_dump_roundtrip_no_speed(self):
        raw = {
            "text": "Hello, listeners.",
            "segments": None,
            "speaker": "tina",
            "speed": None,
        }
        assert models.Preamble(**raw).model_dump() == raw


class TestCastConfiguration:
    """Tests for the CastConfiguration model."""

    def _make_cast(self):
        return {
            "adam": {
                "full_name": "Adam Santos",
                "voice_id": "onwK4e9ZLuTAKqWW03F9",
                "pan": 0.0,
                "filter": False,
                "role": "Host/Narrator",
                "stability": None,
                "similarity_boost": None,
                "style": None,
                "use_speaker_boost": None,
                "language_code": None,
            },
        }

    def test_valid_cast_config(self):
        cc = models.CastConfiguration(
            show="THE 413", episode=1, title="Test",
            cast=self._make_cast(),
        )
        assert cc.show == "THE 413"
        assert "adam" in cc.cast

    def test_accepts_raw_dicts_for_cast_members(self):
        cc = models.CastConfiguration(
            show="THE 413", episode=1, title="Test",
            cast=self._make_cast(),
        )
        assert isinstance(cc.cast["adam"], models.CastMember)

    def test_model_dump_roundtrip(self):
        raw = {
            "show": "THE 413", "episode": 1, "season": None, "title": "Test",
            "season_title": None,
            "artist": "Tina Brissette for Berkshire Talking Chronicles",
            "preamble": None,
            "postamble": None,
            "cast": self._make_cast(),
        }
        assert models.CastConfiguration(**raw).model_dump() == raw

    def test_season_title_optional(self):
        cc = models.CastConfiguration(
            show="THE 413", episode=1, cast=self._make_cast(),
        )
        assert cc.season_title is None

    def test_season_title_accepted(self):
        cc = models.CastConfiguration(
            show="THE 413", episode=1, cast=self._make_cast(),
            season_title="The Letters",
        )
        assert cc.season_title == "The Letters"

    def test_preamble_optional(self):
        cc = models.CastConfiguration(
            show="THE 413", episode=1, cast=self._make_cast(),
        )
        assert cc.preamble is None

    def test_preamble_accepted(self):
        cc = models.CastConfiguration(
            show="THE 413", episode=1, cast=self._make_cast(),
            preamble={"text": "Hello, listeners.", "speaker": "tina"},
        )
        assert isinstance(cc.preamble, models.Preamble)
        assert cc.preamble.speaker == "tina"

    def test_season_optional_in_cast_config(self):
        """Existing cast files without season still validate."""
        cc = models.CastConfiguration(
            show="THE 413", episode=1, title="Test",
            cast=self._make_cast(),
        )
        assert cc.season is None

    def test_season_captured_in_cast_config(self):
        cc = models.CastConfiguration(
            show="THE 413", episode=1, season=1, title="Test",
            cast=self._make_cast(),
        )
        assert cc.season == 1


class TestVoiceConfig:
    """Tests for the VoiceConfig model (simplified cast for production)."""

    def _make(self, **overrides):
        defaults = {"id": "onwK4e9ZLuTAKqWW03F9", "pan": 0.0, "filter": False}
        defaults.update(overrides)
        return models.VoiceConfig(**defaults)

    def test_valid_voice_config(self):
        vc = self._make()
        assert vc.id == "onwK4e9ZLuTAKqWW03F9"

    def test_pan_range_validated(self):
        self._make(pan=-1.0)
        self._make(pan=1.0)
        with pytest.raises(ValidationError):
            self._make(pan=2.0)

    def test_model_dump_uses_id_not_voice_id(self):
        d = self._make().model_dump()
        assert "id" in d
        assert "voice_id" not in d


class TestDialogueEntry:
    """Tests for the DialogueEntry model."""

    def _make(self, **overrides):
        defaults = {
            "speaker": "adam",
            "text": "Hello world.",
            "stem_name": "003_cold-open_adam",
            "seq": 3,
            "direction": None,
        }
        defaults.update(overrides)
        return models.DialogueEntry(**defaults)

    def test_valid_dialogue_entry(self):
        de = self._make()
        assert de.speaker == "adam"
        assert de.stem_name == "003_cold-open_adam"

    def test_seq_allows_negative_for_preamble(self):
        self._make(seq=0)
        self._make(seq=-1)   # preamble INTRO MUSIC
        self._make(seq=-2)   # preamble voice stem

    def test_model_dump_roundtrip(self):
        raw = {
            "speaker": "dez",
            "text": "What's up?",
            "stem_name": "005_act1_dez",
            "seq": 5,
            "direction": "excited",
        }
        assert models.DialogueEntry(**raw).model_dump() == raw


# ---------------------------------------------------------------------------
# Phase 3 — SFX Configuration Models
# ---------------------------------------------------------------------------


class TestSfxEntry:
    """Tests for the SfxEntry model (sound effect mapping)."""

    def _make(self, **overrides):
        defaults = {
            "prompt": "Phone vibrating buzz notification sound on a table",
            "duration_seconds": 2.0,
        }
        defaults.update(overrides)
        return models.SfxEntry(**defaults)

    def test_valid_sfx_entry(self):
        entry = self._make()
        assert entry.type == "sfx"
        assert entry.prompt == "Phone vibrating buzz notification sound on a table"
        assert entry.duration_seconds == 2.0

    def test_default_type_is_sfx(self):
        entry = self._make()
        assert entry.type == "sfx"

    def test_silence_type(self):
        entry = models.SfxEntry(type="silence", duration_seconds=1.0)
        assert entry.type == "silence"
        assert entry.prompt is None

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            self._make(type="unknown")

    def test_duration_range_min(self):
        self._make(duration_seconds=0.5)   # well above minimum
        self._make(duration_seconds=0.1)   # valid: ge=0.0 allows sub-0.5 (stop-marker support)
        # type=sfx with duration_seconds=0.0 is still rejected (would produce empty API call)
        with pytest.raises(ValidationError):
            models.SfxEntry(type="sfx", prompt="x", duration_seconds=0.0)

    def test_duration_range_max(self):
        self._make(duration_seconds=30.0)  # boundary OK for API effects
        with pytest.raises(ValidationError):
            self._make(duration_seconds=31.0)  # fails: API-generated, no source

    def test_source_bypasses_api_duration_cap(self):
        # Pre-existing file: duration_seconds > 30 is valid when source is set
        entry = self._make(duration_seconds=90.0, source="SFX/theme.mp3")
        assert entry.duration_seconds == 90.0
        assert entry.source == "SFX/theme.mp3"

    def test_source_default_is_none(self):
        entry = self._make()
        assert entry.source is None

    def test_prompt_influence_range(self):
        self._make(prompt_influence=0.0)  # boundary OK
        self._make(prompt_influence=1.0)  # boundary OK
        with pytest.raises(ValidationError):
            self._make(prompt_influence=-0.1)
        with pytest.raises(ValidationError):
            self._make(prompt_influence=1.5)

    def test_prompt_influence_default_none(self):
        entry = self._make()
        assert entry.prompt_influence is None

    def test_loop_default_false(self):
        entry = self._make()
        assert entry.loop is False

    def test_loop_true_for_ambience(self):
        entry = self._make(
            prompt="Late night diner ambience, coffee percolating",
            duration_seconds=30.0,
            loop=True,
        )
        assert entry.loop is True

    def test_volume_percentage_default_none(self):
        entry = self._make()
        assert entry.volume_percentage is None

    def test_ramp_in_seconds_default_none(self):
        entry = self._make()
        assert entry.ramp_in_seconds is None

    def test_ramp_out_seconds_default_none(self):
        entry = self._make()
        assert entry.ramp_out_seconds is None

    def test_volume_percentage_accepted(self):
        entry = self._make(volume_percentage=80.0)
        assert entry.volume_percentage == 80.0

    def test_volume_percentage_zero_accepted(self):
        entry = self._make(volume_percentage=0.0)
        assert entry.volume_percentage == 0.0

    def test_volume_percentage_200_accepted(self):
        entry = self._make(volume_percentage=200.0)
        assert entry.volume_percentage == 200.0

    def test_volume_percentage_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            self._make(volume_percentage=-1.0)
        with pytest.raises(ValidationError):
            self._make(volume_percentage=201.0)

    def test_ramp_in_seconds_accepted(self):
        entry = self._make(ramp_in_seconds=1.0)
        assert entry.ramp_in_seconds == 1.0

    def test_ramp_out_seconds_accepted(self):
        entry = self._make(ramp_out_seconds=2.5)
        assert entry.ramp_out_seconds == 2.5

    def test_ramp_in_seconds_zero_accepted(self):
        entry = self._make(ramp_in_seconds=0.0)
        assert entry.ramp_in_seconds == 0.0

    def test_ramp_out_seconds_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            self._make(ramp_out_seconds=-0.1)
        with pytest.raises(ValidationError):
            self._make(ramp_out_seconds=30.1)

    def test_play_duration_default_none(self):
        entry = self._make()
        assert entry.play_duration is None

    def test_play_duration_accepted(self):
        entry = self._make(play_duration=50.0)
        assert entry.play_duration == 50.0

    def test_play_duration_zero_accepted(self):
        entry = self._make(play_duration=0.0)
        assert entry.play_duration == 0.0

    def test_play_duration_100_accepted(self):
        entry = self._make(play_duration=100.0)
        assert entry.play_duration == 100.0

    def test_play_duration_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            self._make(play_duration=-1.0)
        with pytest.raises(ValidationError):
            self._make(play_duration=100.1)

    def test_model_dump_roundtrip_sfx(self):
        raw = {
            "prompt": "Door opening with bell chime",
            "type": "sfx",
            "duration_seconds": 4.0,
            "prompt_influence": 0.5,
            "loop": False,
            "source": None,
            "volume_percentage": None,
            "ramp_in_seconds": None,
            "ramp_out_seconds": None,
            "play_duration": None,
        }
        assert models.SfxEntry(**raw).model_dump() == raw

    def test_model_dump_roundtrip_silence(self):
        raw = {
            "prompt": None,
            "type": "silence",
            "duration_seconds": 1.0,
            "prompt_influence": None,
            "loop": False,
            "source": None,
            "volume_percentage": None,
            "ramp_in_seconds": None,
            "ramp_out_seconds": None,
            "play_duration": None,
        }
        assert models.SfxEntry(**raw).model_dump() == raw

    def test_model_dump_roundtrip_source(self):
        raw = {
            "prompt": "Eerie indie folk theme",
            "type": "sfx",
            "duration_seconds": 90.0,
            "prompt_influence": None,
            "loop": False,
            "source": "SFX/theme.mp3",
            "volume_percentage": None,
            "ramp_in_seconds": None,
            "ramp_out_seconds": None,
            "play_duration": None,
        }
        assert models.SfxEntry(**raw).model_dump() == raw

    def test_model_dump_roundtrip_with_volume_ramp(self):
        raw = {
            "prompt": "Ambience background hum",
            "type": "sfx",
            "duration_seconds": 30.0,
            "prompt_influence": None,
            "loop": True,
            "source": None,
            "volume_percentage": 20.0,
            "ramp_in_seconds": 1.0,
            "ramp_out_seconds": 2.0,
            "play_duration": None,
        }
        assert models.SfxEntry(**raw).model_dump() == raw


class TestSfxConfiguration:
    """Tests for the SfxConfiguration model."""

    def _make_effects(self):
        return {
            "SFX: PHONE BUZZING – TEXT MESSAGE": {
                "prompt": "Phone vibrating buzz notification",
                "duration_seconds": 2.0,
                "prompt_influence": 0.5,
            },
            "BEAT": {
                "type": "silence",
                "duration_seconds": 1.0,
            },
        }

    def test_valid_sfx_config(self):
        sc = models.SfxConfiguration(
            show="THE 413", episode=1, effects=self._make_effects(),
        )
        assert sc.show == "THE 413"
        assert len(sc.effects) == 2

    def test_accepts_raw_dicts_for_entries(self):
        sc = models.SfxConfiguration(
            show="THE 413", episode=1, effects=self._make_effects(),
        )
        assert isinstance(sc.effects["BEAT"], models.SfxEntry)
        assert sc.effects["BEAT"].type == "silence"

    def test_tag_with_season(self):
        sc = models.SfxConfiguration(
            show="THE 413", season=1, episode=1,
            effects=self._make_effects(),
        )
        assert sc.tag == "S01E01"

    def test_tag_without_season(self):
        sc = models.SfxConfiguration(
            show="THE 413", episode=3, effects=self._make_effects(),
        )
        assert sc.tag == "E03"

    def test_season_optional(self):
        sc = models.SfxConfiguration(
            show="THE 413", episode=1, effects=self._make_effects(),
        )
        assert sc.season is None

    def test_defaults_field(self):
        sc = models.SfxConfiguration(
            show="THE 413", episode=1,
            defaults={"prompt_influence": 0.3, "output_format": "mp3_44100_128"},
            effects=self._make_effects(),
        )
        assert sc.defaults["prompt_influence"] == 0.3

    def test_defaults_empty_by_default(self):
        sc = models.SfxConfiguration(
            show="THE 413", episode=1, effects=self._make_effects(),
        )
        assert sc.defaults == {}

    def test_effects_lookup_by_direction_text(self):
        sc = models.SfxConfiguration(
            show="THE 413", episode=1, effects=self._make_effects(),
        )
        sfx = sc.effects.get("SFX: PHONE BUZZING – TEXT MESSAGE")
        assert sfx is not None
        assert sfx.prompt == "Phone vibrating buzz notification"

    def test_model_dump_roundtrip(self):
        raw = {
            "show": "THE 413", "season": 1, "episode": 1,
            "defaults": {"prompt_influence": 0.3},
            "effects": {
                "BEAT": {
                    "prompt": None, "type": "silence",
                    "duration_seconds": 1.0,
                    "prompt_influence": None, "loop": False,
                    "source": None,
                    "volume_percentage": None,
                    "ramp_in_seconds": None,
                    "ramp_out_seconds": None,
                    "play_duration": None,
                },
            },
        }
        assert models.SfxConfiguration(**raw).model_dump() == raw


# ---------------------------------------------------------------------------
# Phase 4 — Show slug, derive_paths, resolve_slug
# ---------------------------------------------------------------------------

import json
import tempfile


class TestShowSlug:
    """Tests for the show_slug() function."""

    def test_the413(self):
        assert models.show_slug("THE 413") == "the413"

    def test_lowercase(self):
        assert models.show_slug("Night Owls") == "nightowls"

    def test_punctuation_stripped(self):
        assert models.show_slug("Dr. Fate's Hour") == "drfateshour"

    def test_numbers_preserved(self):
        assert models.show_slug("Channel 5 News") == "channel5news"

    def test_empty_string(self):
        assert models.show_slug("") == ""

    def test_all_special_chars(self):
        assert models.show_slug("!!!") == ""

    def test_already_slug(self):
        assert models.show_slug("the413") == "the413"


class TestDerivePaths:
    """Tests for the derive_paths() function."""

    def test_all_keys_present(self):
        paths = models.derive_paths("the413", "S01E01")
        expected_keys = {
            "cast", "sfx", "parsed", "parsed_csv", "annotated_csv",
            "master", "cues", "cues_manifest", "orig_parsed", "revised_script",
        }
        assert set(paths.keys()) == expected_keys

    def test_the413_paths_match_legacy(self):
        paths = models.derive_paths("the413", "S01E01")
        assert paths["cast"] == "cast_the413_S01E01.json"
        assert paths["sfx"] == "sfx_the413_S01E01.json"
        assert paths["parsed"] == "parsed/parsed_the413_S01E01.json"
        assert paths["master"] == "the413_S01E01_master.mp3"

    def test_different_show(self):
        paths = models.derive_paths("nightowls", "S02E05")
        assert paths["cast"] == "cast_nightowls_S02E05.json"
        assert paths["sfx"] == "sfx_nightowls_S02E05.json"
        assert paths["parsed"] == "parsed/parsed_nightowls_S02E05.json"

    def test_cues_manifest_has_no_slug(self):
        paths = models.derive_paths("the413", "S01E01")
        assert paths["cues_manifest"] == "cues/cues_manifest_S01E01.json"


class TestResolveSlug:
    """Tests for the resolve_slug() function."""

    def test_explicit_arg_wins(self):
        assert models.resolve_slug("Night Owls") == "nightowls"

    def test_project_json_fallback(self, tmp_path):
        pj = tmp_path / "project.json"
        pj.write_text(json.dumps({"show": "Night Owls"}))
        assert models.resolve_slug(None, str(pj)) == "nightowls"

    def test_default_when_no_project_json(self, tmp_path):
        missing = tmp_path / "nonexistent.json"
        assert models.resolve_slug(None, str(missing)) == "sample"

    def test_explicit_arg_overrides_project_json(self, tmp_path):
        pj = tmp_path / "project.json"
        pj.write_text(json.dumps({"show": "Night Owls"}))
        assert models.resolve_slug("Channel 5", str(pj)) == "channel5"

    def test_project_json_without_show_key(self, tmp_path):
        pj = tmp_path / "project.json"
        pj.write_text(json.dumps({"other": "value"}))
        assert models.resolve_slug(None, str(pj)) == "sample"
