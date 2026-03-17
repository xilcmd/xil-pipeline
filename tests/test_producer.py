"""Tests for XILP002_the413_producer.py — production pipeline (non-API functions)."""

import os
import json
import tempfile
import importlib.util
import pytest

# Import the producer module
spec = importlib.util.spec_from_file_location(
    "producer",
    os.path.join(os.path.dirname(__file__), "..", "XILP002_the413_producer.py")
)
producer = importlib.util.module_from_spec(spec)

# Patch out ElevenLabs client before loading module (no API key needed for these tests)
import unittest.mock
with unittest.mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "test_key"}):
    with unittest.mock.patch("elevenlabs.client.ElevenLabs"):
        spec.loader.exec_module(producer)


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
            "frank": {"full_name": "Frank", "voice_id": "TBD", "pan": 0.0, "filter": True, "role": "Minor"},
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
        assert config["frank"]["id"] == "TBD"

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
    def test_prints_all_lines(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries)
        output = capsys.readouterr().out
        assert "4 dialogue lines" in output
        assert "Hello listeners." in output
        assert "Something happened." in output

    def test_shows_tbd_warning(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries)
        output = capsys.readouterr().out
        assert "TBD" in output
        assert "frank" in output

    def test_start_from_filters_count(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries, start_from=6)
        output = capsys.readouterr().out
        assert "FROM 6:" in output
        # Only seq 6 and 7 are >= 6
        assert "2 lines" in output

    def test_stop_at_filters_count(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # entries at seq 3, 4, 6, 7 — stop_at=4 keeps seq 3 and 4 only
        producer.dry_run(config, entries, stop_at=4)
        output = capsys.readouterr().out
        assert "THRU 4:" in output
        assert "2 lines" in output

    def test_stop_at_and_start_from_combined(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # entries at seq 3, 4, 6, 7 — start_from=4, stop_at=6 keeps seq 4 and 6 only
        producer.dry_run(config, entries, start_from=4, stop_at=6)
        output = capsys.readouterr().out
        assert "FROM 4" in output
        assert "6" in output
        assert "2 lines" in output

    def test_stop_at_marks_out_of_range_skipped(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # stop_at=4: seq 6 and 7 should be marked [x]
        producer.dry_run(config, entries, stop_at=4)
        output = capsys.readouterr().out
        lines = output.splitlines()
        # Find lines with [x] markers for seq 006 and 007
        skipped = [l for l in lines if "[x]" in l and ("006" in l or "007" in l)]
        assert len(skipped) == 2

    def test_shows_stem_names(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries)
        output = capsys.readouterr().out
        assert "003_cold-open_adam.mp3" in output
        assert "006_act1-scene-1_dez.mp3" in output

    def test_shows_char_counts(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        producer.dry_run(config, entries)
        output = capsys.readouterr().out
        # "Hello listeners." = 16 chars
        assert "16 chars" in output


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

    def test_returns_remaining(self, capsys):
        sub = self._make_sub(1000, 10000, "free")
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

        result = producer.check_elevenlabs_quota()
        assert result == 9000

    def test_prints_status(self, capsys):
        sub = self._make_sub(500, 5000, "starter")
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

        producer.check_elevenlabs_quota()
        out = capsys.readouterr().out
        assert "ELEVENLABS API STATUS" in out
        assert "STARTER" in out

    def test_returns_none_on_exception(self, capsys):
        producer.client.user.get.side_effect = Exception("API error")
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

    def test_returns_false_when_insufficient(self, capsys):
        self._set_quota(5)
        assert producer.has_enough_characters("this is a much longer text than 5 chars") is False

    def test_returns_true_on_api_exception(self):
        producer.client.user.get.side_effect = Exception("no user_read")
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

    def test_returns_multilingual_v2_when_healthy(self):
        self._set_quota(50000)
        model = producer.get_best_model_for_budget()
        assert model == "eleven_multilingual_v2"

    def test_returns_flash_when_low(self):
        self._set_quota(100)
        model = producer.get_best_model_for_budget()
        assert model == "eleven_flash_v2_5"

    def test_returns_fallback_on_exception(self):
        producer.client.user.get.side_effect = Exception("fail")
        model = producer.get_best_model_for_budget()
        assert model == "eleven_multilingual_v2"
        producer.client.user.get.side_effect = None


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

    def test_skips_tbd_voice(self, config, entries, tmp_path, capsys):
        self._setup_api()
        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir)

        out = capsys.readouterr().out
        assert "No voice_id for dez" in out
        # dez stem should NOT exist
        assert not (tmp_path / "006_act1_dez.mp3").exists()

    def test_skips_existing_stem(self, config, entries, tmp_path, capsys):
        self._setup_api()
        # Pre-create the adam stem
        (tmp_path / "003_cold-open_adam.mp3").write_bytes(b"existing")
        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir)

        out = capsys.readouterr().out
        assert "skipping" in out

    def test_halts_when_quota_exhausted(self, config, entries, tmp_path, capsys):
        sub = unittest.mock.MagicMock()
        sub.character_limit = 1  # only 1 char left
        sub.character_count = 0
        user_info = unittest.mock.MagicMock()
        user_info.subscription = sub
        producer.client.user.get.return_value = user_info

        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir)

        out = capsys.readouterr().out
        assert "halted" in out

    def test_start_from_skips_earlier_entries(self, config, entries, tmp_path, capsys):
        self._setup_api()
        stems_dir = str(tmp_path)
        producer.generate_voices(config, entries, stems_dir, start_from=6)

        out = capsys.readouterr().out
        # adam (seq=3) should not appear in generation output
        assert "003" not in out

    def test_stop_at_skips_later_entries(self, config, entries, tmp_path, capsys):
        self._setup_api()
        stems_dir = str(tmp_path)
        # entries: seq 3 (adam, valid), seq 6 (dez, TBD) — stop at 4 excludes seq 6
        producer.generate_voices(config, entries, stems_dir, stop_at=4)

        out = capsys.readouterr().out
        # seq 6 (dez) should not appear in output at all
        assert "006" not in out
        # adam (seq=3) should have been processed
        assert (tmp_path / "003_cold-open_adam.mp3").exists()

    def test_stop_at_combined_with_start_from(self, config, entries, tmp_path, capsys):
        self._setup_api()
        stems_dir = str(tmp_path)
        # start_from=6 AND stop_at=4 → empty range, nothing to process
        producer.generate_voices(config, entries, stems_dir, start_from=6, stop_at=4)

        out = capsys.readouterr().out
        assert "Generating 0 voice stems" in out

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

_models_path = os.path.join(os.path.dirname(__file__), "..", "models.py")
_models_spec = importlib.util.spec_from_file_location("models", _models_path)
models = importlib.util.module_from_spec(_models_spec)
_models_spec.loader.exec_module(models)


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
    def test_dry_run_shows_truncated_text(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        terse_entries = [
            {**e, "text": producer.truncate_to_words(e["text"])} for e in entries
        ]
        producer.dry_run(config, terse_entries)
        output = capsys.readouterr().out
        # "Hello listeners." → "Hello listeners." (only 2 words, unchanged)
        # "Welcome to the" instead of "Welcome to the show."
        assert "Welcome to the" in output
        assert "Welcome to the show." not in output

    def test_dry_run_char_count_reduced(self, sample_script, sample_cast, capsys):
        config, entries, _tag = producer.load_production(sample_script, sample_cast)
        # Full run char count
        producer.dry_run(config, entries)
        full_out = capsys.readouterr().out
        # Terse run char count
        terse_entries = [
            {**e, "text": producer.truncate_to_words(e["text"])} for e in entries
        ]
        producer.dry_run(config, terse_entries)
        terse_out = capsys.readouterr().out
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
        producer.client.text_to_speech.convert.return_value = iter([b"fake_audio"])


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

    def test_skips_existing_sfx_stem(self, tmp_path, capsys):
        entries = [self._make_sfx_entries()[1]]  # BEAT
        config = self._make_sfx_config_dict()
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        os.makedirs(stems_dir, exist_ok=True)
        (tmp_path / "stems" / "004_cold-open_sfx.mp3").write_bytes(b"existing")
        producer.generate_sfx_stems(entries, config, stems_dir,
                                    client=None, sfx_dir=sfx_dir)
        out = capsys.readouterr().out
        assert "skipping" in out.lower() or "Exists" in out

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
    def test_dry_run_includes_sfx_entries(self, tmp_path, capsys):
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
        out = capsys.readouterr().out
        assert "AMBIENCE: RADIO STATION" in out
        assert "BEAT" in out
        assert "silence" in out.lower()

    def test_dry_run_shows_sfx_cost_estimate(self, tmp_path, capsys):
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
        out = capsys.readouterr().out
        # Should show duration or credit cost info
        assert "2.0" in out or "credits" in out.lower()


# ─── Tests: Preamble dry-run ───

class TestPreambleDryRun:
    """Verify [PREAMBLE] line appears in dry-run output when preamble is configured."""

    def _make_cast_file(self, tmp_path, with_preamble=True):
        cast = {
            "show": "TEST", "season": 1, "episode": 3, "title": "The Bridge",
            "season_title": "The Letters",
            "cast": {
                "tina": {
                    "full_name": "Tina", "voice_id": "voice_tina",
                    "pan": 0.0, "filter": False, "role": "Producer",
                },
            },
        }
        if with_preamble:
            cast["preamble"] = {
                "text": "Today on The 4 1 3, {season_title}, Episode {episode}, {title}.",
                "speaker": "tina",
            }
        f = tmp_path / "cast_the413_S01E03.json"
        f.write_text(json.dumps(cast), encoding="utf-8")
        return str(f)

    def _make_script_file(self, tmp_path):
        script = {
            "show": "TEST", "episode": 3, "title": "The Bridge",
            "entries": [],
            "stats": {"dialogue_lines": 0},
        }
        f = tmp_path / "parsed_the413_S01E03.json"
        f.write_text(json.dumps(script), encoding="utf-8")
        return str(f)

    def test_preamble_line_printed_before_main_dry_run(self, tmp_path, capsys):
        cast_file = self._make_cast_file(tmp_path)
        script_file = self._make_script_file(tmp_path)
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILP002", "--episode", "S01E03",
                "--script", script_file, "--dry-run",
            ]):
                producer.main()
        finally:
            os.chdir(original_cwd)
        out = capsys.readouterr().out
        assert "[PREAMBLE]" in out
        assert "tina" in out

    def test_preamble_shows_char_count(self, tmp_path, capsys):
        cast_file = self._make_cast_file(tmp_path)
        script_file = self._make_script_file(tmp_path)
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILP002", "--episode", "S01E03",
                "--script", script_file, "--dry-run",
            ]):
                producer.main()
        finally:
            os.chdir(original_cwd)
        out = capsys.readouterr().out
        # Preamble text has template vars replaced; should show char count
        assert "chars" in out

    def test_no_preamble_no_preamble_line(self, tmp_path, capsys):
        cast_file = self._make_cast_file(tmp_path, with_preamble=False)
        script_file = self._make_script_file(tmp_path)
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            with unittest.mock.patch("sys.argv", [
                "XILP002", "--episode", "S01E03",
                "--script", script_file, "--dry-run",
            ]):
                producer.main()
        finally:
            os.chdir(original_cwd)
        out = capsys.readouterr().out
        assert "[PREAMBLE]" not in out


class TestPreambleSegments:
    """Unit tests for the multi-segment preamble helpers."""

    def _make_cast_cfg(self, preamble_dict: dict):
        import json
        from models import CastConfiguration
        data = {
            "show": "TEST", "season": 2, "episode": 3,
            "title": "The Bridge", "season_title": "The Letters",
            "cast": {
                "tina": {
                    "full_name": "Tina", "voice_id": "voice_tina",
                    "pan": 0.0, "filter": False, "role": "Producer",
                },
            },
            "preamble": preamble_dict,
        }
        return CastConfiguration(**data)

    # ------------------------------------------------------------------
    # _resolve_preamble_text
    # ------------------------------------------------------------------

    def test_resolve_segments_joins_text(self):
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "segments": [
                {"text": "Stock intro, ", "shared_key": "preamble-intro"},
                {"text": "{season_title}, Episode {episode}, {title}.", "shared_key": None},
                {"text": " Stock outro.", "shared_key": "preamble-outro"},
            ],
        })
        result = producer._resolve_preamble_text(cfg)
        assert result == "Stock intro, The Letters, Episode 3, The Bridge. Stock outro."

    def test_resolve_legacy_text(self):
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "text": "Hello {season_title}, Episode {episode}, {title}.",
        })
        result = producer._resolve_preamble_text(cfg)
        assert result == "Hello The Letters, Episode 3, The Bridge."

    # ------------------------------------------------------------------
    # _dry_run_preamble — segments path
    # ------------------------------------------------------------------

    def test_dry_run_segments_shows_segment_count(self, tmp_path, capsys):
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "segments": [
                {"text": "Stock intro ", "shared_key": "preamble-intro"},
                {"text": "{season_title}, Episode {episode}.", "shared_key": None},
                {"text": " Stock outro.", "shared_key": "preamble-outro"},
            ],
        })
        stem = str(tmp_path / "n002_preamble_tina.mp3")
        producer._dry_run_preamble(cfg, stem)
        out = capsys.readouterr().out
        assert "3 segments" in out
        assert "tina" in out

    def test_dry_run_segments_cached_vs_new(self, tmp_path, capsys):
        # Create the intro cache file; outro is absent
        sfx_dir = tmp_path / "SFX"
        sfx_dir.mkdir()
        intro = sfx_dir / "preamble-intro.mp3"
        intro.write_bytes(b"\xff\xfb" + b"\x00" * 100)  # non-zero dummy MP3

        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "segments": [
                {"text": "Stock intro ", "shared_key": "preamble-intro"},
                {"text": "{season_title}.", "shared_key": None},
                {"text": " Outro.", "shared_key": "preamble-outro"},
            ],
        })
        stem = str(tmp_path / "n002_preamble_tina.mp3")
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            producer._dry_run_preamble(cfg, stem)
        finally:
            os.chdir(original_cwd)
        out = capsys.readouterr().out
        assert "CACHED" in out
        assert "NEW" in out

    def test_dry_run_stem_exists_skips(self, tmp_path, capsys):
        stem = tmp_path / "n002_preamble_tina.mp3"
        stem.write_bytes(b"\xff\xfb" + b"\x00" * 100)
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "segments": [
                {"text": "Intro ", "shared_key": "k"},
                {"text": "{title}.", "shared_key": None},
            ],
        })
        producer._dry_run_preamble(cfg, str(stem))
        out = capsys.readouterr().out
        assert "skip" in out.lower()

    # ------------------------------------------------------------------
    # _generate_preamble_voice — cache-hit path (no API call)
    # ------------------------------------------------------------------

    def test_generate_skips_existing_stem(self, tmp_path, capsys):
        stem = tmp_path / "n002_preamble_tina.mp3"
        stem.write_bytes(b"\xff\xfb" + b"\x00" * 100)
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "segments": [
                {"text": "Intro ", "shared_key": "k"},
                {"text": "{title}.", "shared_key": None},
            ],
        })
        config = {"tina": {"id": "voice_tina"}}
        producer._generate_preamble_voice(cfg, config, str(stem))
        out = capsys.readouterr().out
        assert "skipping" in out.lower()
        # stem must be unchanged
        assert stem.read_bytes()[:2] == b"\xff\xfb"

    def test_generate_missing_voice_id_skips(self, tmp_path, capsys):
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "segments": [{"text": "Hello.", "shared_key": None}],
        })
        config = {"tina": {"id": "TBD"}}
        stem = str(tmp_path / "n002_preamble_tina.mp3")
        producer._generate_preamble_voice(cfg, config, stem)
        out = capsys.readouterr().out
        assert "No voice_id" in out
        assert not os.path.exists(stem)


class TestPostambleHelpers:
    """Unit tests for postamble inject, resolve, dry-run, and generate helpers."""

    def _make_cast_cfg(self, postamble_dict: dict | None, preamble_dict: dict | None = None):
        from models import CastConfiguration
        data = {
            "show": "TEST", "season": 2, "episode": 3,
            "title": "The Bridge", "season_title": "The Letters",
            "cast": {
                "tina": {
                    "full_name": "Tina", "voice_id": "voice_tina",
                    "pan": 0.0, "filter": False, "role": "Producer",
                },
            },
        }
        if postamble_dict is not None:
            data["postamble"] = postamble_dict
        if preamble_dict is not None:
            data["preamble"] = preamble_dict
        return CastConfiguration(**data)

    def _make_parsed(self, tmp_path, n_entries=3):
        """Write a minimal parsed JSON with n_entries dialogue lines."""
        entries = [
            {
                "seq": i, "type": "dialogue", "section": "act-one",
                "scene": None, "speaker": "adam", "direction": None,
                "text": f"Line {i}", "direction_type": None,
            }
            for i in range(1, n_entries + 1)
        ]
        data = {
            "show": "TEST", "episode": 3, "title": "The Bridge",
            "entries": entries,
            "stats": {"dialogue_lines": n_entries},
        }
        p = tmp_path / "parsed_the413_S02E03.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return str(p)

    # ------------------------------------------------------------------
    # _resolve_postamble_text
    # ------------------------------------------------------------------

    def test_resolve_postamble_segments(self):
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "segments": [
                {"text": "Today you listened to {season_title}, Episode {episode}, {title}.", "shared_key": None},
                {"text": " Stock outro.", "shared_key": "postamble-outro"},
            ],
        })
        result = producer._resolve_postamble_text(cfg)
        assert result == "Today you listened to The Letters, Episode 3, The Bridge. Stock outro."

    def test_resolve_postamble_legacy_text(self):
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "text": "Bye from {season_title}.",
        })
        result = producer._resolve_postamble_text(cfg)
        assert result == "Bye from The Letters."

    # ------------------------------------------------------------------
    # inject_postamble_entries
    # ------------------------------------------------------------------

    def test_inject_postamble_appends_at_end(self, tmp_path):
        parsed = self._make_parsed(tmp_path, n_entries=5)
        m_seq, v_seq = producer.inject_postamble_entries(parsed, "Bye everyone.", "tina")
        assert m_seq == 6   # music precedes voice
        assert v_seq == 7
        with open(parsed) as f:
            data = json.load(f)
        seqs = [e["seq"] for e in data["entries"]]
        assert seqs[-2] == 6
        assert seqs[-1] == 7

    def test_inject_postamble_music_entry_fields(self, tmp_path):
        parsed = self._make_parsed(tmp_path, n_entries=2)
        producer.inject_postamble_entries(parsed, "Goodnight.", "tina")
        with open(parsed) as f:
            data = json.load(f)
        music = next(e for e in data["entries"] if e["seq"] == 3)
        assert music["type"] == "direction"
        assert music["section"] == "postamble"
        assert music["text"] == "OUTRO MUSIC"
        assert music["direction_type"] == "MUSIC"

    def test_inject_postamble_voice_entry_fields(self, tmp_path):
        parsed = self._make_parsed(tmp_path, n_entries=2)
        producer.inject_postamble_entries(parsed, "Goodnight.", "tina")
        with open(parsed) as f:
            data = json.load(f)
        voice = next(e for e in data["entries"] if e["seq"] == 4)
        assert voice["type"] == "dialogue"
        assert voice["section"] == "postamble"
        assert voice["speaker"] == "tina"
        assert voice["text"] == "Goodnight."

    def test_inject_postamble_idempotent(self, tmp_path):
        parsed = self._make_parsed(tmp_path, n_entries=2)
        producer.inject_postamble_entries(parsed, "Take 1.", "tina")
        producer.inject_postamble_entries(parsed, "Take 2.", "tina")
        with open(parsed) as f:
            data = json.load(f)
        postamble = [e for e in data["entries"] if e.get("section") == "postamble"]
        assert len(postamble) == 2
        voice = next(e for e in postamble if e["type"] == "dialogue")
        assert voice["text"] == "Take 2."

    def test_inject_postamble_music_gets_foreground_override(self, tmp_path):
        """OUTRO MUSIC entry (section=postamble) must be foreground in mix."""
        from mix_common import collect_stem_plans
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

    # ------------------------------------------------------------------
    # _dry_run_postamble
    # ------------------------------------------------------------------

    def test_dry_run_postamble_shows_label(self, tmp_path, capsys):
        cfg = self._make_cast_cfg({
            "speaker": "tina",
            "segments": [
                {"text": "Variable {title}.", "shared_key": None},
                {"text": " Static outro.", "shared_key": "postamble-outro"},
            ],
        })
        stem = str(tmp_path / "305_postamble_tina.mp3")
        producer._dry_run_postamble(cfg, stem)
        out = capsys.readouterr().out
        assert "POSTAMBLE" in out
        assert "tina" in out

    def test_dry_run_postamble_stem_exists_skips(self, tmp_path, capsys):
        stem = tmp_path / "305_postamble_tina.mp3"
        stem.write_bytes(b"\xff\xfb" + b"\x00" * 100)
        cfg = self._make_cast_cfg({"speaker": "tina", "text": "Bye."})
        producer._dry_run_postamble(cfg, str(stem))
        out = capsys.readouterr().out
        assert "skip" in out.lower()

    # ------------------------------------------------------------------
    # _generate_postamble_voice — guard paths (no API call)
    # ------------------------------------------------------------------

    def test_generate_postamble_skips_existing_stem(self, tmp_path, capsys):
        stem = tmp_path / "305_postamble_tina.mp3"
        stem.write_bytes(b"\xff\xfb" + b"\x00" * 100)
        cfg = self._make_cast_cfg({"speaker": "tina", "text": "Bye."})
        config = {"tina": {"id": "voice_tina"}}
        producer._generate_postamble_voice(cfg, config, str(stem))
        out = capsys.readouterr().out
        assert "skipping" in out.lower()

    def test_generate_postamble_missing_voice_id_skips(self, tmp_path, capsys):
        cfg = self._make_cast_cfg({"speaker": "tina", "text": "Bye."})
        config = {"tina": {"id": "TBD"}}
        stem = str(tmp_path / "305_postamble_tina.mp3")
        producer._generate_postamble_voice(cfg, config, stem)
        out = capsys.readouterr().out
        assert "No voice_id" in out
        assert not os.path.exists(stem)

    # ------------------------------------------------------------------
    # mix_common foreground_override uses section field
    # ------------------------------------------------------------------

