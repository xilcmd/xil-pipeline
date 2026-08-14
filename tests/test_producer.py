# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for XILP002_producer.py — production pipeline (non-API functions)."""

import json
import os
import unittest.mock

import pytest
from elevenlabs.core.api_error import ApiError

# Patch out ElevenLabs client before loading module (no API key needed for these tests)
with unittest.mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key"}):
    with unittest.mock.patch("elevenlabs.client.ElevenLabs"):
        from xil_pipeline import XILP002_producer as producer

from xil_pipeline import models

# ─── Fixtures ───

@pytest.fixture
def sample_cast(tmp_path):
    cast = {
        "show": "TEST SHOW",
        "season": 1,
        "episode": 1,
        "cast": {
            "adam": {"full_name": "Adam Santos", "voice_id": "voice_adam_123", "pan": 0.0, "filter": False, "role": "Host"},
            "dez": {"full_name": "Dez Williams", "voice_id": "voice_dez_456", "pan": -0.15, "filter": False, "role": "Supporting"},
            "frank": {"full_name": "Frank", "voice_id": "voice_frank_789", "pan": 0.0, "filter": True, "role": "Minor"},
        }
    }
    cast_file = tmp_path / "cast.json"
    cast_file.write_text(json.dumps(cast), encoding="utf-8")
    return str(cast_file)


@pytest.fixture
def sample_script(tmp_path):
    script = {
        "show": "TEST SHOW",
        "episode": 1,
        "title": "Test Episode",
        "entries": [
            {"seq": 1, "type": "section_header", "section": "cold-open", "scene": None,
             "speaker": None, "direction": None, "text": "COLD OPEN", "direction_type": None},
            {"seq": 2, "type": "direction", "section": "cold-open", "scene": None,
             "speaker": None, "direction": None, "text": "AMBIENCE: RADIO STATION", "direction_type": "AMBIENCE"},
            {"seq": 3, "type": "dialogue", "section": "cold-open", "scene": None,
             "speaker": "adam", "direction": "on-air voice", "text": "Hello listeners.", "direction_type": None},
            {"seq": 4, "type": "dialogue", "section": "cold-open", "scene": None,
             "speaker": "adam", "direction": None, "text": "Welcome to the show.", "direction_type": None},
            {"seq": 5, "type": "scene_header", "section": "act1", "scene": "scene-1",
             "speaker": None, "direction": None, "text": "SCENE 1: THE DINER", "direction_type": None},
            {"seq": 6, "type": "dialogue", "section": "act1", "scene": "scene-1",
             "speaker": "dez", "direction": "uneasy", "text": "Something happened.", "direction_type": None},
            {"seq": 7, "type": "dialogue", "section": "act1", "scene": "scene-1",
             "speaker": "frank", "direction": None, "text": "Put a fresh pot on.", "direction_type": None},
        ],
        "stats": {"dialogue_lines": 4}
    }
    script_file = tmp_path / "script.json"
    script_file.write_text(json.dumps(script), encoding="utf-8")
    return str(script_file)


# ─── Tests: load_production ───

class TestLoadProduction:
    def test_returns_config_and_entries(self, sample_script, sample_cast):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        assert isinstance(config, dict)
        assert isinstance(entries, list)

    def test_config_has_voice_ids(self, sample_script, sample_cast):
        config, _, _tag = producer.load_production(sample_script, sample_cast)
        assert config["adam"]["id"] == "voice_adam_123"
        assert config["dez"]["id"] == "voice_dez_456"
        assert config["frank"]["id"] == "voice_frank_789"

    def test_config_has_pan_and_filter(self, sample_script, sample_cast):
        config, _, _tag = producer.load_production(sample_script, sample_cast)
        assert config["adam"]["pan"] == 0.0
        assert config["adam"]["filter"] is False
        assert config["frank"]["filter"] is True

    def test_only_dialogue_entries_returned(self, sample_script, sample_cast):
        _, entries, _tag = producer.load_production(sample_script, sample_cast)
        assert len(entries) == 4  # Only dialogue, not headers/directions

    def test_entry_has_stem_name(self, sample_script, sample_cast):
        _, entries, _tag = producer.load_production(sample_script, sample_cast)
        assert entries[0]["stem_name"] == "003_cold-open_adam"
        assert entries[2]["stem_name"] == "006_act1-scene-1_dez"

    def test_entry_preserves_speaker_and_text(self, sample_script, sample_cast):
        _, entries, _tag = producer.load_production(sample_script, sample_cast)
        assert entries[0]["speaker"] == "adam"
        assert entries[0]["text"] == "Hello listeners."

    def test_entry_preserves_direction(self, sample_script, sample_cast):
        _, entries, _tag = producer.load_production(sample_script, sample_cast)
        assert entries[0]["direction"] == "on-air voice"
        assert entries[1]["direction"] is None

    def test_entry_seq_preserved(self, sample_script, sample_cast):
        _, entries, _tag = producer.load_production(sample_script, sample_cast)
        seqs = [e["seq"] for e in entries]
        assert seqs == [3, 4, 6, 7]


# ─── Tests: dry_run ───

class TestDryRun:
    def test_prints_all_lines(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries)
        assert "4 dialogue lines" in caplog.text
        assert "Hello listeners." in caplog.text
        assert "Something happened." in caplog.text

    def test_shows_tbd_warning(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # Inject a TBD voice to verify the warning fires
        config["frank"]["id"] = "TBD"
        producer.dry_run(config, entries)
        assert "TBD" in caplog.text
        assert "frank" in caplog.text

    def test_start_from_filters_count(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries, start_from=6)
        assert "FROM 6:" in caplog.text
        # Only seq 6 and 7 are >= 6
        assert "2 lines" in caplog.text

    def test_stop_at_filters_count(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # entries at seq 3, 4, 6, 7 — stop_at=4 keeps seq 3 and 4 only
        producer.dry_run(config, entries, stop_at=4)
        assert "THRU 4:" in caplog.text
        assert "2 lines" in caplog.text

    def test_stop_at_and_start_from_combined(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # entries at seq 3, 4, 6, 7 — start_from=4, stop_at=6 keeps seq 4 and 6 only
        producer.dry_run(config, entries, start_from=4, stop_at=6)
        assert "FROM 4" in caplog.text
        assert "6" in caplog.text
        assert "2 lines" in caplog.text

    def test_stop_at_marks_out_of_range_skipped(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # stop_at=4: seq 6 and 7 should be marked [x]
        producer.dry_run(config, entries, stop_at=4)
        lines = caplog.text.splitlines()
        # Find lines with [x] markers for seq 006 and 007
        skipped = [l for l in lines if "[x]" in l and ("006" in l or "007" in l)]
        assert len(skipped) == 2

    def test_shows_stem_names(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries)
        assert "003_cold-open_adam.mp3" in caplog.text
        assert "006_act1-scene-1_dez.mp3" in caplog.text

    def test_shows_char_counts(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries)
        # "Hello listeners." = 16 chars
        assert "16 chars" in caplog.text


# ─── Integration: load from actual project files ───

ACTUAL_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "parsed", "parsed_the413_ep01.json")
ACTUAL_CAST = os.path.join(os.path.dirname(__file__), "..", "cast_the413_S01E01.json")


@pytest.mark.skipif(
    not (os.path.exists(ACTUAL_SCRIPT) and os.path.exists(ACTUAL_CAST)),
    reason="Actual parsed script or cast config not present"
)
class TestLoadActualProduction:
    def test_loads_without_error(self):
        config, entries, _tag = producer.load_production(ACTUAL_SCRIPT, ACTUAL_CAST)
        assert len(entries) > 100
        assert "adam" in config

    def test_all_speakers_in_config(self):
        config, entries, _tag = producer.load_production(ACTUAL_SCRIPT, ACTUAL_CAST)
        speakers_in_script = set(e["speaker"] for e in entries)
        for speaker in speakers_in_script:
            assert speaker in config, f"Speaker '{speaker}' missing from cast config"

    def test_stem_names_are_unique(self):
        _, entries, _tag = producer.load_production(ACTUAL_SCRIPT, ACTUAL_CAST)
        stem_names = [e["stem_name"] for e in entries]
        assert len(stem_names) == len(set(stem_names)), "Duplicate stem names found"


# ─── Tests: check_elevenlabs_quota ───

class TestCheckElevenLabsQuota:
    def _make_sub(self, used, limit, tier="free"):
        sub = unittest.mock.MagicMock()
        sub.character_count = used
        sub.character_limit = limit
        sub.tier = tier
        return sub

    def test_returns_remaining(self, caplog):
        sub = self._make_sub(1000, 10000, "free")
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

        result = producer.check_elevenlabs_quota()
        assert result == 9000

    def test_prints_status(self, caplog):
        sub = self._make_sub(500, 5000, "starter")
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

        producer.check_elevenlabs_quota()
        assert "ELEVENLABS API STATUS" in caplog.text
        assert "STARTER" in caplog.text

    def test_returns_none_on_exception(self, caplog):
        producer.client.user.get.side_effect = ApiError(status_code=500, body="API error")
        result = producer.check_elevenlabs_quota()
        assert result is None
        producer.client.user.get.side_effect = None


# ─── Tests: has_enough_characters ───

class TestHasEnoughCharacters:
    def _set_quota(self, remaining):
        sub = unittest.mock.MagicMock()
        sub.character_limit = 10000
        sub.character_count = 10000 - remaining
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

    def test_returns_true_when_enough(self):
        self._set_quota(1000)
        assert producer.has_enough_characters("short text") is True

    def test_returns_false_when_insufficient(self, caplog):
        self._set_quota(5)
        assert producer.has_enough_characters("this is a much longer text than 5 chars") is False

    def test_returns_true_on_api_exception(self):
        producer.client.user.get.side_effect = ApiError(status_code=403, body="no user_read")
        assert producer.has_enough_characters("any text") is True
        producer.client.user.get.side_effect = None


# ─── Tests: get_best_model_for_budget ───

class TestGetBestModelForBudget:
    def _set_quota(self, remaining):
        sub = unittest.mock.MagicMock()
        sub.character_limit = 100000
        sub.character_count = 100000 - remaining
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

    def test_returns_v3_when_healthy(self):
        self._set_quota(50000)
        model = producer.get_best_model_for_budget()
        assert model == "eleven_v3"

    def test_returns_v3_when_low(self):
        # Low balance no longer falls back to flash — v3 is always used so that
        # native audio tags like [pause] are honoured.
        self._set_quota(100)
        model = producer.get_best_model_for_budget()
        assert model == "eleven_v3"

    def test_returns_fallback_on_exception(self):
        producer.client.user.get.side_effect = ApiError(status_code=500, body="fail")
        model = producer.get_best_model_for_budget()
        assert model == "eleven_v3"
        producer.client.user.get.side_effect = None


# ─── Tests: _select_model ───

class TestSelectModel:
    def _set_quota(self, remaining):
        sub = unittest.mock.MagicMock()
        sub.character_limit = 100000
        sub.character_count = 100000 - remaining
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

    def test_uses_v3_for_plain_text(self):
        self._set_quota(50000)
        model = producer._select_model("Hello there, this is plain text.")
        assert model == "eleven_v3"

    def test_ssml_text_still_uses_v3(self):
        # SSML fallback to eleven_multilingual_v2 is removed — v3 is always used.
        # A warning is logged instead so the operator can replace SSML with [pause].
        self._set_quota(50000)
        model = producer._select_model('Hello <break time="1s"/> world.')
        assert model == "eleven_v3"

    def test_ssml_with_low_budget_still_uses_v3(self):
        self._set_quota(100)
        model = producer._select_model('Hello <break time="1s"/> world.')
        assert model == "eleven_v3"

    def test_bare_less_than_does_not_trigger_ssml_fallback(self):
        self._set_quota(50000)
        model = producer._select_model("Score was 3 < 5, no SSML here.")
        assert model == "eleven_v3"


# ─── Tests: generate_voices ───

class TestGenerateVoices:
    @pytest.fixture
    def config(self):
        return {
            "adam": {"id": "voice_adam_123", "pan": 0.0, "filter": False},
            "dez": {"id": "TBD", "pan": -0.15, "filter": False},
        }

    @pytest.fixture
    def entries(self):
        return [
            {"seq": 3, "speaker": "adam", "text": "Hello listeners.", "stem_name": "003_cold-open_adam"},
            {"seq": 6, "speaker": "dez", "text": "Something happened.", "stem_name": "006_act1_dez"},
        ]

    def _setup_api(self, fake_audio=b"\xff\xfb\x10\x00" * 100):
        """Set up quota and TTS mocks."""
        sub = unittest.mock.MagicMock()
        sub.character_limit = 100000
        sub.character_count = 0
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info
        producer.client.text_to_speech.convert.return_value = [fake_audio]

    def test_skips_tbd_voice(self, config, entries, tmp_path, caplog):
        self._setup_api()
        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir)

        # generate_voices now blocks and logs an error when any speaker in range has TBD
        assert "Cannot generate" in caplog.text
        assert "dez" in caplog.text
        assert not (tmp_path / "006_act1_dez.mp3").exists()

    def test_skips_existing_stem(self, config, entries, tmp_path, caplog):
        self._setup_api()
        # Use only the adam entry (valid voice) to avoid the TBD block
        adam_only = [e for e in entries if e["speaker"] == "adam"]
        (tmp_path / "003_cold-open_adam.mp3").write_bytes(b"existing")
        stems_dir = str(tmp_path)
        producer.generate_voices(config, adam_only, stems_dir)

        assert "skipping" in caplog.text

    def test_halts_when_quota_exhausted(self, config, entries, tmp_path, caplog):
        sub = unittest.mock.MagicMock()
        sub.character_limit = 1  # only 1 char left
        sub.character_count = 0
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

        # Use only the adam entry (valid voice) to avoid the TBD block
        adam_only = [e for e in entries if e["speaker"] == "adam"]
        stems_dir = str(tmp_path)
        producer.generate_voices(config, adam_only, stems_dir)

        assert "halted" in caplog.text

    def test_start_from_skips_earlier_entries(self, config, entries, tmp_path, caplog):
        self._setup_api()
        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir, start_from=6)

        # adam (seq=3) should not appear in generation output
        assert "003" not in caplog.text

    def test_stop_at_skips_later_entries(self, config, entries, tmp_path, caplog):
        self._setup_api()
        stems_dir = str(tmp_path)
        # entries: seq 3 (adam, valid), seq 6 (dez, TBD) — stop at 4 excludes seq 6
        producer.generate_voices(config, entries, stems_dir, stop_at=4)

        # seq 6 (dez) should not appear in output at all
        assert "006" not in caplog.text
        # adam (seq=3) should have been processed
        assert (tmp_path / "003_cold-open_adam.mp3").exists()

    def test_stop_at_combined_with_start_from(self, config, entries, tmp_path, caplog):
        self._setup_api()
        stems_dir = str(tmp_path)
        # start_from=6 AND stop_at=4 → empty range, nothing to process
        producer.generate_voices(config, entries, stems_dir, start_from=6, stop_at=4)

        assert "Generating 0 voice stems" in caplog.text

    def test_skips_tag_only_text(self, tmp_path, caplog):
        """Entries whose text is only a speaker tag (e.g. '[sighs]') are skipped with a warning."""
        self._setup_api()
        config = {"dez": {"id": "voice_dez_456", "pan": 0.0, "filter": False}}
        entries = [
            {"seq": 117, "speaker": "dez", "text": "[sighs]", "stem_name": "117_act2-scene-2_dez"},
        ]
        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir)

        assert not (tmp_path / "117_act2-scene-2_dez.mp3").exists()
        assert "SKIP" in caplog.text
        assert "117" in caplog.text
        assert "empty after stripping" in caplog.text

    def test_skips_emoji_only_text(self, tmp_path, caplog):
        """Entries whose text is only an emoji are skipped with a warning."""
        self._setup_api()
        config = {"adam": {"id": "voice_adam_123", "pan": 0.0, "filter": False}}
        entries = [
            {"seq": 50, "speaker": "adam", "text": "\U0001f600", "stem_name": "050_cold-open_adam"},
        ]
        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir)

        assert not (tmp_path / "050_cold-open_adam.mp3").exists()
        assert "SKIP" in caplog.text

    def test_tags_dialogue_stem(self, sample_script, sample_cast, tmp_path):
        """Generated stems carry ID3 tags: title (song), artist, and lyrics."""
        self._setup_api()
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir)

        from mutagen.id3 import ID3
        stem_path = tmp_path / "003_cold-open_adam.mp3"
        assert stem_path.exists()
        tags = ID3(str(stem_path))

        # TIT2 (song): full_name + first five words of spoken text
        assert str(tags.get("TIT2")) == "Adam Santos: Hello listeners."
        # TPE1 (artist): speaker's full name
        assert str(tags.get("TPE1")) == "Adam Santos"
        # USLT (lyrics): full dialogue text
        uslt_frames = tags.getall("USLT")
        assert any(f.text == "Hello listeners." for f in uslt_frames)


# ─── Contract Tests: load_production output validates against Pydantic models ───


class TestLoadProductionModelContract:
    """Verify load_production output is valid against Pydantic models."""

    def test_config_values_are_valid_voice_configs(self, sample_script, sample_cast):
        config, _, _tag = producer.load_production(sample_script, sample_cast)
        for key, val in config.items():
            models.VoiceConfig(**val)

    def test_entries_are_valid_dialogue_entries(self, sample_script, sample_cast):
        _, entries, _tag = producer.load_production(sample_script, sample_cast)
        for entry in entries:
            models.DialogueEntry(**entry)


# ─── Tests: truncate_to_words ───

class TestTruncateToWords:
    def test_three_words_from_long_line(self):
        result = producer.truncate_to_words("Hello listeners, welcome to the show.")
        assert result == "Hello listeners, welcome"

    def test_exactly_three_words(self):
        assert producer.truncate_to_words("One two three") == "One two three"

    def test_fewer_than_three_words(self):
        assert producer.truncate_to_words("Hello there") == "Hello there"

    def test_single_word(self):
        assert producer.truncate_to_words("Hello.") == "Hello."

    def test_empty_string(self):
        assert producer.truncate_to_words("") == ""

    def test_custom_word_count(self):
        assert producer.truncate_to_words("one two three four five", n=2) == "one two"


# ─── Tests: --terse mode ───

class TestTerseMode:
    def test_dry_run_shows_truncated_text(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        terse_entries = [
            {**e, "text": producer.truncate_to_words(e["text"])} for e in entries
        ]
        producer.dry_run(config, terse_entries)
        # "Hello listeners." → "Hello listeners." (only 2 words, unchanged)
        # "Welcome to the" instead of "Welcome to the show."
        assert "Welcome to the" in caplog.text
        assert "Welcome to the show." not in caplog.text

    def test_dry_run_char_count_reduced(self, sample_script, sample_cast, caplog):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # Full run char count
        producer.dry_run(config, entries)
        full_out = caplog.text
        offset = len(caplog.text)
        # Terse run char count
        terse_entries = [
            {**e, "text": producer.truncate_to_words(e["text"])} for e in entries
        ]
        producer.dry_run(config, terse_entries)
        terse_out = caplog.text[offset:]
        # Extract total chars from each output
        import re
        full_total = int(re.search(r"(\d+) TTS characters", full_out).group(1).replace(",", ""))
        terse_total = int(re.search(r"(\d+) TTS characters", terse_out).group(1).replace(",", ""))
        assert terse_total < full_total

    def test_generate_voices_sends_truncated_text(self, sample_script, sample_cast, tmp_path):
        """--terse entries reach the ElevenLabs API call with truncated text."""
        self._setup_api()
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        terse_entries = [
            {**e, "text": producer.truncate_to_words(e["text"])}
            for e in entries
            if e["speaker"] != "frank"  # skip TBD voice
        ]
        stems_dir = str(tmp_path)
        producer.generate_voices(config, terse_entries, stems_dir)

        calls = producer.client.text_to_speech.convert.call_args_list
        for call in calls:
            text_sent = call.kwargs.get("text") or call.args[0] if call.args else None
            if text_sent:
                assert len(text_sent.split()) <= 3

    def _setup_api(self):
        sub = unittest.mock.MagicMock()
        sub.character_limit = 100000
        sub.character_count = 0
        sub.tier = "free"
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info
        # Fresh iterator per call — the real API returns a new stream each time,
        # and generate_voices now treats an empty stream as a failed render.
        producer.client.text_to_speech.convert.side_effect = (
            lambda *a, **k: iter([b"fake_audio"])
        )


# ─── Tests: SFX entry loading ───

@pytest.fixture
def sample_sfx_config(tmp_path):
    sfx = {
        "show": "TEST SHOW",
        "season": 1,
        "episode": 1,
        "defaults": {"prompt_influence": 0.3},
        "effects": {
            "AMBIENCE: RADIO STATION": {
                "prompt": "Late night radio station ambience",
                "duration_seconds": 30.0,
                "loop": True,
            },
            "SFX: PHONE BUZZING": {
                "prompt": "Phone vibrating buzz",
                "duration_seconds": 2.0,
                "prompt_influence": 0.5,
            },
            "BEAT": {
                "type": "silence",
                "duration_seconds": 1.0,
            },
        },
    }
    sfx_file = tmp_path / "sfx.json"
    sfx_file.write_text(json.dumps(sfx), encoding="utf-8")
    return str(sfx_file)


@pytest.fixture
def sample_script_with_sfx(tmp_path):
    script = {
        "show": "TEST SHOW",
        "episode": 1,
        "title": "Test Episode",
        "entries": [
            {"seq": 1, "type": "section_header", "section": "cold-open", "scene": None,
             "speaker": None, "direction": None, "text": "COLD OPEN", "direction_type": None},
            {"seq": 2, "type": "direction", "section": "cold-open", "scene": None,
             "speaker": None, "direction": None, "text": "AMBIENCE: RADIO STATION", "direction_type": "AMBIENCE"},
            {"seq": 3, "type": "dialogue", "section": "cold-open", "scene": None,
             "speaker": "adam", "direction": "on-air voice", "text": "Hello listeners.", "direction_type": None},
            {"seq": 4, "type": "direction", "section": "cold-open", "scene": None,
             "speaker": None, "direction": None, "text": "BEAT", "direction_type": "BEAT"},
            {"seq": 5, "type": "direction", "section": "cold-open", "scene": None,
             "speaker": None, "direction": None, "text": "SFX: PHONE BUZZING", "direction_type": "SFX"},
            {"seq": 6, "type": "dialogue", "section": "cold-open", "scene": None,
             "speaker": "adam", "direction": None, "text": "Welcome to the show.", "direction_type": None},
        ],
        "stats": {"dialogue_lines": 2},
    }
    script_file = tmp_path / "script.json"
    script_file.write_text(json.dumps(script), encoding="utf-8")
    return str(script_file)


class TestLoadSfxEntries:
    def test_returns_list_of_sfx_entries(self, sample_script_with_sfx, sample_sfx_config):
        sfx_entries = producer.load_sfx_entries(sample_script_with_sfx, sample_sfx_config)
        assert isinstance(sfx_entries, list)
        assert len(sfx_entries) == 3  # AMBIENCE + BEAT + SFX

    def test_only_direction_entries_with_config_match(self, sample_script_with_sfx, sample_sfx_config):
        sfx_entries = producer.load_sfx_entries(sample_script_with_sfx, sample_sfx_config)
        texts = [e["text"] for e in sfx_entries]
        assert "AMBIENCE: RADIO STATION" in texts
        assert "BEAT" in texts
        assert "SFX: PHONE BUZZING" in texts

    def test_skips_direction_without_config_match(self, tmp_path):
        script = {
            "show": "TEST", "episode": 1, "title": "T",
            "entries": [
                {"seq": 1, "type": "direction", "section": "cold-open", "scene": None,
                 "speaker": None, "direction": None, "text": "SFX: UNKNOWN SOUND", "direction_type": "SFX"},
            ],
            "stats": {},
        }
        sfx = {
            "show": "TEST", "episode": 1,
            "effects": {"BEAT": {"type": "silence", "duration_seconds": 1.0}},
        }
        script_file = tmp_path / "script.json"
        script_file.write_text(json.dumps(script), encoding="utf-8")
        sfx_file = tmp_path / "sfx.json"
        sfx_file.write_text(json.dumps(sfx), encoding="utf-8")
        sfx_entries = producer.load_sfx_entries(str(script_file), str(sfx_file))
        assert len(sfx_entries) == 0

    def test_entry_has_stem_name(self, sample_script_with_sfx, sample_sfx_config):
        sfx_entries = producer.load_sfx_entries(sample_script_with_sfx, sample_sfx_config)
        # seq 2, section cold-open, no scene → "002_cold-open_sfx"
        ambience = [e for e in sfx_entries if e["text"] == "AMBIENCE: RADIO STATION"][0]
        assert ambience["stem_name"] == "002_cold-open_sfx"

    def test_entry_has_sfx_type(self, sample_script_with_sfx, sample_sfx_config):
        sfx_entries = producer.load_sfx_entries(sample_script_with_sfx, sample_sfx_config)
        beat = [e for e in sfx_entries if e["text"] == "BEAT"][0]
        assert beat["sfx_type"] == "silence"
        ambience = [e for e in sfx_entries if e["text"] == "AMBIENCE: RADIO STATION"][0]
        assert ambience["sfx_type"] == "sfx"

    def test_entry_seq_preserved(self, sample_script_with_sfx, sample_sfx_config):
        sfx_entries = producer.load_sfx_entries(sample_script_with_sfx, sample_sfx_config)
        seqs = [e["seq"] for e in sfx_entries]
        assert seqs == [2, 4, 5]


# ─── Tests: generate_sfx_stems ───

class TestGenerateSfxStems:
    """Tests that producer.generate_sfx_stems delegates to sfx_common.generate_sfx."""

    def _make_sfx_entries(self):
        return [
            {"seq": 2, "text": "AMBIENCE: RADIO STATION", "stem_name": "002_cold-open_sfx",
             "sfx_type": "sfx", "section": "cold-open", "scene": None},
            {"seq": 4, "text": "BEAT", "stem_name": "004_cold-open_sfx",
             "sfx_type": "silence", "section": "cold-open", "scene": None},
            {"seq": 5, "text": "SFX: PHONE BUZZING", "stem_name": "005_cold-open_sfx",
             "sfx_type": "sfx", "section": "cold-open", "scene": None},
        ]

    def _make_sfx_config_dict(self):
        return {
            "show": "TEST", "season": 1, "episode": 1,
            "defaults": {"prompt_influence": 0.3},
            "effects": {
                "AMBIENCE: RADIO STATION": {
                    "prompt": "Late night radio station ambience",
                    "duration_seconds": 30.0, "loop": True,
                },
                "BEAT": {"type": "silence", "duration_seconds": 1.0},
                "SFX: PHONE BUZZING": {
                    "prompt": "Phone vibrating buzz",
                    "duration_seconds": 2.0, "prompt_influence": 0.5,
                },
            },
        }

    def test_silence_stem_created_without_api(self, tmp_path):
        entries = [self._make_sfx_entries()[1]]  # BEAT only
        config = self._make_sfx_config_dict()
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        producer.generate_sfx_stems(entries, config, stems_dir,
                                    client=None, sfx_dir=sfx_dir)
        assert (tmp_path / "stems" / "004_cold-open_sfx.mp3").exists()
        # Shared asset also created
        assert (tmp_path / "SFX" / "beat.mp3").exists()

    def test_sfx_stem_calls_api(self, tmp_path):
        entries = [self._make_sfx_entries()[2]]  # SFX: PHONE BUZZING
        config = self._make_sfx_config_dict()
        mock_client = unittest.mock.MagicMock()
        mock_client.text_to_sound_effects.convert.return_value = iter([b"\xff\xfb" * 50])
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        producer.generate_sfx_stems(entries, config, stems_dir,
                                    client=mock_client, sfx_dir=sfx_dir)
        mock_client.text_to_sound_effects.convert.assert_called_once()
        assert (tmp_path / "stems" / "005_cold-open_sfx.mp3").exists()

    def test_skips_existing_sfx_stem(self, tmp_path, caplog):
        entries = [self._make_sfx_entries()[1]]  # BEAT
        config = self._make_sfx_config_dict()
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        os.makedirs(stems_dir, exist_ok=True)
        (tmp_path / "stems" / "004_cold-open_sfx.mp3").write_bytes(b"existing")
        producer.generate_sfx_stems(entries, config, stems_dir,
                                    client=None, sfx_dir=sfx_dir)
        assert "skipping" in caplog.text.lower() or "Exists" in caplog.text

    def test_start_from_filters_entries(self, tmp_path):
        entries = self._make_sfx_entries()
        config = self._make_sfx_config_dict()
        mock_client = unittest.mock.MagicMock()
        mock_client.text_to_sound_effects.convert.return_value = iter([b"\xff\xfb" * 50])
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        producer.generate_sfx_stems(entries, config, stems_dir,
                                    client=mock_client, start_from=5,
                                    sfx_dir=sfx_dir)
        # Only seq 5 should be processed (seq 2 and 4 skipped)
        assert not (tmp_path / "stems" / "002_cold-open_sfx.mp3").exists()
        assert not (tmp_path / "stems" / "004_cold-open_sfx.mp3").exists()


# ─── Tests: dry_run with SFX ───

class TestDryRunWithSfx:
    def test_dry_run_includes_sfx_entries(self, tmp_path, caplog):
        config = {"adam": {"id": "voice_123", "pan": 0.0, "filter": False}}
        dialogue = [
            {"seq": 3, "speaker": "adam", "text": "Hello.", "stem_name": "003_cold-open_adam", "direction": None},
        ]
        sfx = [
            {"seq": 2, "text": "AMBIENCE: RADIO STATION", "stem_name": "002_cold-open_sfx",
             "sfx_type": "sfx", "section": "cold-open", "scene": None},
            {"seq": 4, "text": "BEAT", "stem_name": "004_cold-open_sfx",
             "sfx_type": "silence", "section": "cold-open", "scene": None},
        ]
        sfx_config = {
            "show": "TEST", "episode": 1,
            "defaults": {"prompt_influence": 0.3},
            "effects": {
                "AMBIENCE: RADIO STATION": {
                    "prompt": "Radio ambience", "duration_seconds": 30.0,
                },
                "BEAT": {"type": "silence", "duration_seconds": 1.0},
            },
        }
        producer.dry_run(config, dialogue, sfx_entries=sfx, sfx_config=sfx_config,
                         stems_dir=str(tmp_path))
        assert "AMBIENCE: RADIO STATION" in caplog.text
        assert "BEAT" in caplog.text
        assert "silence" in caplog.text.lower()

    def test_dry_run_shows_sfx_cost_estimate(self, tmp_path, caplog):
        config = {}
        dialogue = []
        sfx = [
            {"seq": 2, "text": "SFX: PHONE BUZZING", "stem_name": "002_cold-open_sfx",
             "sfx_type": "sfx", "section": "cold-open", "scene": None},
        ]
        sfx_config = {
            "show": "TEST", "episode": 1, "defaults": {},
            "effects": {
                "SFX: PHONE BUZZING": {
                    "prompt": "Phone buzzing", "duration_seconds": 2.0,
                },
            },
        }
        producer.dry_run(config, dialogue, sfx_entries=sfx, sfx_config=sfx_config,
                         stems_dir=str(tmp_path))
        # Should show duration or credit cost info
        assert "2.0" in caplog.text or "credits" in caplog.text.lower()





def test_postamble_music_gets_foreground_override(tmp_path):
    """OUTRO MUSIC entry (section=postamble) must be foreground in the mix."""
    from xil_pipeline.mix_common import collect_stem_plans
    stems = tmp_path / "stems"
    stems.mkdir()
    sfx_stem = stems / "306_postamble_sfx.mp3"
    sfx_stem.write_bytes(b"\xff\xfb" + b"\x00" * 100)
    entries_index = {
        306: {
            "seq": 306, "type": "direction", "section": "postamble",
            "text": "OUTRO MUSIC", "direction_type": "MUSIC",
        }
    }
    plans = collect_stem_plans(str(stems), entries_index)
    music_plan = next((p for p in plans if p.seq == 306), None)
    assert music_plan is not None
    assert music_plan.foreground_override is True


# ── dry_run() per-speaker cost breakdown ──────────────────────────────────────

def _make_config(*speakers):
    """Minimal cast config dict with voice IDs assigned."""
    return {spk: {"id": f"voice_{spk}", "full_name": spk.title()} for spk in speakers}


def _make_entry(seq, speaker, text, section="act1"):
    stem_name = f"{seq:03d}_{section}_{speaker}"
    return {"seq": seq, "type": "dialogue", "section": section, "scene": None,
            "speaker": speaker, "text": text, "direction": None, "stem_name": stem_name}


class TestDryRunSpeakerTable:
    """Per-speaker cost breakdown table is printed when stems need generating."""

    def test_speaker_table_present_when_stems_missing(self, tmp_path, caplog):
        """Table is printed when stems_dir has no pre-existing stems."""
        entries = [
            _make_entry(1, "adam", "Hello there.", "act1"),
            _make_entry(2, "beth", "Hi.", "act1"),
        ]
        config = _make_config("adam", "beth")
        with caplog.at_level("INFO", logger="xil_pipeline.XILP002_producer"):
            producer.dry_run(config, entries, stems_dir=str(tmp_path))
        assert "SPEAKER COST BREAKDOWN" in caplog.text
        assert "adam" in caplog.text
        assert "beth" in caplog.text

    def test_speaker_table_absent_when_all_stems_exist(self, tmp_path, caplog):
        """Table is omitted when all stems already exist on disk."""
        entries = [
            _make_entry(1, "adam", "Hello there.", "act1"),
        ]
        config = _make_config("adam")
        # Pre-create the stem
        stem = tmp_path / "001_act1_adam.mp3"
        stem.write_bytes(b"audio")
        with caplog.at_level("INFO", logger="xil_pipeline.XILP002_producer"):
            producer.dry_run(config, entries, stems_dir=str(tmp_path))
        assert "SPEAKER COST BREAKDOWN" not in caplog.text

    def test_speaker_rows_sorted_by_chars_descending(self, tmp_path, caplog):
        """Speaker with more chars to generate appears before speaker with fewer."""
        entries = [
            _make_entry(1, "adam", "Short.", "act1"),
            _make_entry(2, "beth", "This is a much longer line with many more characters.", "act1"),
        ]
        config = _make_config("adam", "beth")
        with caplog.at_level("INFO", logger="xil_pipeline.XILP002_producer"):
            producer.dry_run(config, entries, stems_dir=str(tmp_path))
        # Search only within the table section to avoid matching per-entry log lines
        table_start = caplog.text.index("SPEAKER COST BREAKDOWN")
        table_text = caplog.text[table_start:]
        idx_adam = table_text.index("adam")
        idx_beth = table_text.index("beth")
        assert idx_beth < idx_adam  # beth has more chars, appears first

    def test_skip_count_reflects_existing_stems(self, tmp_path, caplog):
        """Skip column shows the count of stems already present on disk."""
        entries = [
            _make_entry(1, "adam", "Generate this.", "act1"),
            _make_entry(2, "adam", "Skip this.", "act1"),
        ]
        config = _make_config("adam")
        # Pre-create only the second stem
        (tmp_path / "002_act1_adam.mp3").write_bytes(b"audio")
        with caplog.at_level("INFO", logger="xil_pipeline.XILP002_producer"):
            producer.dry_run(config, entries, stems_dir=str(tmp_path))
        assert "SPEAKER COST BREAKDOWN" in caplog.text
        # caplog lines include the logger prefix; find the TOTAL data row by content
        total_line = next(
            l for l in caplog.text.splitlines()
            if "TOTAL" in l and "lines" not in l.lower() and "characters" not in l.lower()
        )
        # Format: "... TOTAL     1       14          1        10"
        parts = total_line.split()
        total_idx = parts.index("TOTAL")
        assert parts[total_idx + 1] == "1"   # gen lines
        assert parts[total_idx + 3] == "1"   # skip lines


# ─── Tests: atomic stem writes + size-aware resume (regression) ───

_ATOMIC_CONFIG = {
    "narrator": {
        "id": "voice_narrator", "speed": 1.0,
        "stability": 0.5, "similarity_boost": 0.75, "full_name": "Narrator",
    }
}


def _atomic_entry():
    return {
        "seq": 1, "speaker": "narrator", "text": "Hello there.",
        "stem_name": "001_intro_narrator", "section": "intro", "direction": None,
    }


class TestAtomicStemWrites:
    """A failed/interrupted render must never leave a 0-byte stem at the final
    path, and a 0-byte leftover must be regenerated (not skipped) on resume.

    Exercised via the gtts backend (no API/quota) since generate_voices writes
    every backend through the same atomic temp-file path.
    """

    @pytest.fixture(autouse=True)
    def _no_api_no_tagging(self, monkeypatch):
        # Keep the test off the network and off real mp3 tag parsing.
        monkeypatch.setattr(producer, "get_best_model_for_budget", lambda: "eleven_multilingual_v2")
        monkeypatch.setattr(producer, "tag_mp3", lambda *a, **k: None)

    def test_zero_byte_leftover_is_regenerated(self, tmp_path, monkeypatch):
        stem = tmp_path / "001_intro_narrator.mp3"
        stem.write_bytes(b"")  # corrupt 0-byte leftover from a prior failed run
        monkeypatch.setattr(
            producer, "_gtts_generate",
            lambda text, path: open(path, "wb").write(b"\xff\xfb\x90fresh-audio"),
        )
        producer.generate_voices(_ATOMIC_CONFIG, [_atomic_entry()], str(tmp_path), backend="gtts")
        assert stem.stat().st_size > 0

    def test_valid_existing_stem_is_skipped(self, tmp_path, monkeypatch):
        stem = tmp_path / "001_intro_narrator.mp3"
        stem.write_bytes(b"\xff\xfb\x90existing-audio")

        def _must_not_run(text, path):
            raise AssertionError("a valid existing stem must be skipped, not re-rendered")

        monkeypatch.setattr(producer, "_gtts_generate", _must_not_run)
        producer.generate_voices(_ATOMIC_CONFIG, [_atomic_entry()], str(tmp_path), backend="gtts")
        assert stem.read_bytes() == b"\xff\xfb\x90existing-audio"

    def test_empty_render_raises_and_leaves_no_file(self, tmp_path, monkeypatch):
        stem = tmp_path / "001_intro_narrator.mp3"
        # backend writes nothing → temp stays 0 bytes → must raise, leave no stem
        monkeypatch.setattr(producer, "_gtts_generate", lambda text, path: None)
        with pytest.raises(RuntimeError, match="empty file"):
            producer.generate_voices(_ATOMIC_CONFIG, [_atomic_entry()], str(tmp_path), backend="gtts")
        assert not stem.exists()
        assert list(tmp_path.glob("*.tmp")) == []  # temp cleaned up

    def test_failed_render_leaves_no_final_file(self, tmp_path, monkeypatch):
        stem = tmp_path / "001_intro_narrator.mp3"

        def _partial_then_fail(text, path):
            with open(path, "wb") as f:
                f.write(b"partial")
            raise RuntimeError("network drop mid-stream")

        monkeypatch.setattr(producer, "_gtts_generate", _partial_then_fail)
        with pytest.raises(RuntimeError, match="network drop"):
            producer.generate_voices(_ATOMIC_CONFIG, [_atomic_entry()], str(tmp_path), backend="gtts")
        assert not stem.exists()
        assert list(tmp_path.glob("*.tmp")) == []


# ─── Tests: Chatterbox worker protocol tolerance (regression) ───

class _FakeProc:
    """Minimal stand-in for the worker subprocess: canned stdout, captured stdin."""

    def __init__(self, stdout_lines):
        self._lines = list(stdout_lines)
        self.stdin = unittest.mock.MagicMock()
        self.stdout = unittest.mock.MagicMock()
        self.stdout.readline.side_effect = lambda: self._lines.pop(0) if self._lines else ""


class TestChatterboxClientProtocol:
    """Turbo's s3gen prints progress to stdout mid-generation; a bare
    json.loads on the first line crashed the whole run (JSONDecodeError)."""

    def _client(self, stdout_lines, tmp_path):
        client = producer._ChatterboxClient(
            python_path="/nonexistent/python",
            voice_refs_dir=str(tmp_path),
        )
        client._proc = _FakeProc(stdout_lines)
        return client

    def test_generate_skips_non_json_progress_lines(self, tmp_path):
        client = self._client(
            ["S3 Token -> Mel Inference...\n", '{"done": true}\n'], tmp_path
        )
        client.generate("hello", str(tmp_path / "out.mp3"), "narrator")  # must not raise

    def test_generate_surfaces_worker_error_after_noise(self, tmp_path):
        client = self._client(
            ["loaded PerthNet (Implicit)\n", '{"error": "boom"}\n'], tmp_path
        )
        with pytest.raises(RuntimeError, match="boom"):
            client.generate("hello", str(tmp_path / "out.mp3"), "narrator")

    def test_generate_raises_when_pipe_closes(self, tmp_path):
        client = self._client(["noise with no json\n"], tmp_path)
        with pytest.raises(RuntimeError, match="closed pipe"):
            client.generate("hello", str(tmp_path / "out.mp3"), "narrator")


class TestChatterboxAliasRemoval:
    """Classic Chatterbox was removed in #62; `chatterbox` aliases to Turbo.

    The alias must resolve *before* anything reads args.backend. `backend` is
    part of the stem manifest's dedup key, so writing "chatterbox" onto a new
    stem would record a value that can never be requested again — the stem
    could never be matched or reused.
    """

    def test_chatterbox_is_still_an_accepted_choice(self):
        """argparse must accept it, or the alias never gets a chance to run."""
        args = producer.get_parser().parse_args(["--episode", "S01E01", "--backend", "chatterbox"])
        assert args.backend == "chatterbox"

    def test_classic_only_flags_are_gone(self):
        parser = producer.get_parser()
        opts = {a for action in parser._actions for a in action.option_strings}
        for dead in ("--exaggeration", "--cfg-weight",
                     "--audioldm2-python", "--audioldm2-guidance",
                     "--stableaudio-python", "--stableaudio-seed"):
            assert dead not in opts, f"{dead} should have been removed"

    def test_sfx_backend_never_offers_the_removed_backends(self):
        """The removed trials must not reappear as choices.

        Pinning an exact list was wrong — mmaudio was added in #64. The durable
        invariant is that audioldm2/stableaudio stay gone while elevenlabs stays.
        """
        parser = producer.get_parser()
        action = next(a for a in parser._actions if "--sfx-backend" in a.option_strings)
        assert "elevenlabs" in action.choices
        assert "audioldm2" not in action.choices
        assert "stableaudio" not in action.choices

    def test_backend_choices_drop_classic_chatterbox_but_keep_the_alias(self):
        parser = producer.get_parser()
        action = next(a for a in parser._actions if "--backend" in a.option_strings)
        assert "chatterbox-turbo" in action.choices
        assert "chatterbox" in action.choices, "alias must remain parseable"

    def test_client_targets_the_turbo_worker(self):
        """Only the Turbo worker ships now."""
        assert producer._ChatterboxClient._WORKER.endswith("chatterbox_turbo_worker.py")

    def test_conditionals_keep_the_turbo_suffix(self):
        """Turbo conds are NOT interchangeable with classic ones.

        Plain .conds.pt files from the old backend are still on disk; collapsing
        the suffix would make Turbo load incompatible caches.
        """
        client = producer._ChatterboxClient(python_path="/x/python3", voice_refs_dir="voice_refs")
        assert client._cond_for("adam").endswith("adam.turbo.conds.pt")


class TestChatterboxDeviceFlag:
    """--device lets a user force cpu even when cuda would work (e.g. GPU busy
    with another job); the worker itself already auto-falls back to cpu when
    cuda is unavailable, so this flag is an override, not the fallback path."""

    def test_device_flag_defaults_to_cuda(self):
        parser = producer.get_parser()
        action = next(a for a in parser._actions if "--device" in a.option_strings)
        assert action.default == "cuda"
        assert set(action.choices) == {"cuda", "cpu"}

    def test_client_default_device_is_cuda(self):
        client = producer._ChatterboxClient(python_path="/x/python3", voice_refs_dir="voice_refs")
        assert client._device == "cuda"

    def test_client_accepts_explicit_cpu(self):
        client = producer._ChatterboxClient(
            python_path="/x/python3", voice_refs_dir="voice_refs", device="cpu",
        )
        assert client._device == "cpu"
