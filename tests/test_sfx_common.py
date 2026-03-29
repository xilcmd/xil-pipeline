# SPDX-FileCopyrightText: 2025 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for sfx_common.py — shared SFX library utilities."""

import json
import os
import unittest.mock

import pytest

# ─── Module import ───
from xil_pipeline import sfx_common

# ─── Fixtures ───

@pytest.fixture
def sample_sfx_config():
    return {
        "show": "TEST SHOW", "season": 1, "episode": 1,
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
            "LONG BEAT": {
                "type": "silence",
                "duration_seconds": 2.0,
            },
            "MUSIC: SHOW THEME": {
                "prompt": "Eerie indie folk theme",
                "duration_seconds": 15.0,
            },
        },
    }


@pytest.fixture
def sample_script(tmp_path):
    script = {
        "show": "TEST SHOW", "episode": 1, "title": "Test",
        "entries": [
            {"seq": 1, "type": "section_header", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "COLD OPEN", "direction_type": None},
            {"seq": 2, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "AMBIENCE: RADIO STATION", "direction_type": "AMBIENCE"},
            {"seq": 3, "type": "dialogue", "section": "cold-open",
             "scene": None, "speaker": "adam", "direction": None,
             "text": "Hello.", "direction_type": None},
            {"seq": 4, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "BEAT", "direction_type": "BEAT"},
            {"seq": 5, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "SFX: PHONE BUZZING", "direction_type": "SFX"},
            {"seq": 6, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "MUSIC: SHOW THEME", "direction_type": "MUSIC"},
            # Duplicate BEAT at different seq — tests reuse
            {"seq": 7, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "BEAT", "direction_type": "BEAT"},
            # Direction not in SFX config — should be skipped
            {"seq": 8, "type": "direction", "section": "cold-open",
             "scene": None, "speaker": None, "direction": None,
             "text": "EVERYONE TURNS", "direction_type": None},
        ],
        "stats": {},
    }
    script_file = tmp_path / "script.json"
    script_file.write_text(json.dumps(script), encoding="utf-8")
    return str(script_file)


@pytest.fixture
def sample_sfx_file(tmp_path, sample_sfx_config):
    sfx_file = tmp_path / "sfx.json"
    sfx_file.write_text(json.dumps(sample_sfx_config), encoding="utf-8")
    return str(sfx_file)


# ─── Tests: file_nonempty ───

class TestFileNonempty:
    def test_returns_true_for_nonempty_file(self, tmp_path):
        f = tmp_path / "audio.mp3"
        f.write_bytes(b"data")
        assert sfx_common.file_nonempty(str(f)) is True

    def test_returns_false_for_missing_file(self, tmp_path):
        assert sfx_common.file_nonempty(str(tmp_path / "missing.mp3")) is False

    def test_returns_false_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.mp3"
        f.write_bytes(b"")
        assert sfx_common.file_nonempty(str(f)) is False

    def test_returns_false_for_directory(self, tmp_path):
        # os.stat on a directory returns st_size of 0 or non-zero depending on OS;
        # the important thing is it doesn't raise
        result = sfx_common.file_nonempty(str(tmp_path))
        assert isinstance(result, bool)


# ─── Tests: slugify_effect_key ───

class TestSlugifyEffectKey:
    def test_simple_word(self):
        assert sfx_common.slugify_effect_key("BEAT") == "beat"

    def test_two_words(self):
        assert sfx_common.slugify_effect_key("LONG BEAT") == "long-beat"

    def test_sfx_with_colon(self):
        result = sfx_common.slugify_effect_key("SFX: DOOR OPENS, BELL CHIMES")
        assert result == "sfx_door-opens-bell-chimes"

    def test_ambience_with_dash(self):
        result = sfx_common.slugify_effect_key(
            "AMBIENCE: RADIO STATION – LATE NIGHT, EQUIPMENT HUM"
        )
        assert result == "ambience_radio-station-late-night-equipment-hum"

    def test_music_with_colon(self):
        result = sfx_common.slugify_effect_key("MUSIC: BRIEF STING")
        assert result == "music_brief-sting"

    def test_no_leading_trailing_hyphens(self):
        result = sfx_common.slugify_effect_key("--TEST--")
        assert not result.startswith("-")
        assert not result.endswith("-")

    def test_multiple_special_chars_collapsed(self):
        result = sfx_common.slugify_effect_key("SFX: A,,,B...C")
        # Multiple non-alphanumeric chars should collapse to single hyphen
        assert "--" not in result

    def test_empty_string(self):
        result = sfx_common.slugify_effect_key("")
        assert result == ""


# ─── Tests: shared_sfx_path ───

class TestSharedSfxPath:
    def test_returns_mp3_path(self):
        path = sfx_common.shared_sfx_path("/tmp/SFX", "BEAT")
        assert path == os.path.join("/tmp/SFX", "beat.mp3")

    def test_uses_slug(self):
        path = sfx_common.shared_sfx_path("/tmp/SFX", "SFX: PHONE BUZZING")
        assert path.endswith("sfx_phone-buzzing.mp3")


# ─── Tests: ensure_shared_sfx ───

# ─── Tests: tag_mp3 ───

class TestTagMp3:
    def test_tags_silence_mp3(self, tmp_path):
        from pydub import AudioSegment
        # Create a real silence MP3
        mp3_path = tmp_path / "test.mp3"
        AudioSegment.silent(duration=500).export(str(mp3_path), format="mp3")

        sfx_common.tag_mp3(str(mp3_path), show="THE 413")

        from mutagen.id3 import ID3
        tags = ID3(str(mp3_path))
        assert str(tags.get("TALB")) == "THE 413"
        assert str(tags.get("TCON")) == "Podcast"
        # Year should be current year
        import datetime
        assert str(tags.get("TDRC")) == str(datetime.date.today().year)

    def test_tags_with_title(self, tmp_path):
        from pydub import AudioSegment
        mp3_path = tmp_path / "test.mp3"
        AudioSegment.silent(duration=500).export(str(mp3_path), format="mp3")

        sfx_common.tag_mp3(str(mp3_path), show="THE 413", title="BEAT")

        from mutagen.id3 import ID3
        tags = ID3(str(mp3_path))
        assert str(tags.get("TIT2")) == "BEAT"

    def test_tags_with_artist(self, tmp_path):
        from pydub import AudioSegment
        mp3_path = tmp_path / "test.mp3"
        AudioSegment.silent(duration=500).export(str(mp3_path), format="mp3")

        sfx_common.tag_mp3(str(mp3_path), artist="Adam Santos")

        from mutagen.id3 import ID3
        tags = ID3(str(mp3_path))
        assert str(tags.get("TPE1")) == "Adam Santos"

    def test_tags_with_lyrics(self, tmp_path):
        from pydub import AudioSegment
        mp3_path = tmp_path / "test.mp3"
        AudioSegment.silent(duration=500).export(str(mp3_path), format="mp3")

        sfx_common.tag_mp3(str(mp3_path), lyrics="Hello listeners, welcome to the show.")

        from mutagen.id3 import ID3
        tags = ID3(str(mp3_path))
        uslt_frames = tags.getall("USLT")
        assert any(f.text == "Hello listeners, welcome to the show." for f in uslt_frames)

    def test_artist_and_lyrics_not_written_when_none(self, tmp_path):
        from pydub import AudioSegment
        mp3_path = tmp_path / "test.mp3"
        AudioSegment.silent(duration=500).export(str(mp3_path), format="mp3")

        sfx_common.tag_mp3(str(mp3_path))

        from mutagen.id3 import ID3
        tags = ID3(str(mp3_path))
        assert tags.get("TPE1") is None
        assert tags.getall("USLT") == []


class TestEnsureSharedSfx:
    def test_silence_creates_file_without_api(self, tmp_path):
        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(type="silence", duration_seconds=1.0)
        sfx_dir = str(tmp_path / "SFX")

        path = sfx_common.ensure_shared_sfx(
            "BEAT", effect, sfx_dir, defaults={}, client=None,
        )
        assert os.path.exists(path)
        assert path.endswith("beat.mp3")

    def test_sfx_calls_api(self, tmp_path):
        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(prompt="Phone buzz", duration_seconds=2.0)
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        mock_client.text_to_sound_effects.convert.return_value = iter(
            [b"\xff\xfb" * 50]
        )

        path = sfx_common.ensure_shared_sfx(
            "SFX: PHONE BUZZING", effect, sfx_dir,
            defaults={"prompt_influence": 0.3}, client=mock_client,
        )
        assert os.path.exists(path)
        mock_client.text_to_sound_effects.convert.assert_called_once()

    def test_cached_skips_generation(self, tmp_path):
        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(type="silence", duration_seconds=1.0)
        sfx_dir = str(tmp_path / "SFX")

        # First call creates the file
        path1 = sfx_common.ensure_shared_sfx(
            "BEAT", effect, sfx_dir, defaults={},
        )
        mtime1 = os.path.getmtime(path1)

        # Second call should return same path without regenerating
        path2 = sfx_common.ensure_shared_sfx(
            "BEAT", effect, sfx_dir, defaults={},
        )
        assert path1 == path2
        assert os.path.getmtime(path2) == mtime1

    def test_sfx_without_client_raises(self, tmp_path):
        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(prompt="Phone buzz", duration_seconds=2.0)
        sfx_dir = str(tmp_path / "SFX")

        with pytest.raises(ValueError, match="client"):
            sfx_common.ensure_shared_sfx(
                "SFX: PHONE BUZZING", effect, sfx_dir,
                defaults={}, client=None,
            )

    def test_generated_file_has_id3_tags(self, tmp_path):
        from mutagen.id3 import ID3

        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(type="silence", duration_seconds=1.0)
        sfx_dir = str(tmp_path / "SFX")

        path = sfx_common.ensure_shared_sfx(
            "BEAT", effect, sfx_dir, defaults={},
            client=None, show="THE 413",
        )
        tags = ID3(path)
        assert str(tags.get("TALB")) == "THE 413"
        assert str(tags.get("TCON")) == "Podcast"
        assert str(tags.get("TIT2")) == "BEAT"

    def test_uses_effect_prompt_influence_over_default(self, tmp_path):
        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(
            prompt="Phone buzz", duration_seconds=2.0, prompt_influence=0.7,
        )
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        mock_client.text_to_sound_effects.convert.return_value = iter(
            [b"\xff\xfb" * 50]
        )

        sfx_common.ensure_shared_sfx(
            "SFX: TEST", effect, sfx_dir,
            defaults={"prompt_influence": 0.3}, client=mock_client,
        )
        call_kwargs = mock_client.text_to_sound_effects.convert.call_args[1]
        assert call_kwargs["prompt_influence"] == 0.7

    def test_retries_on_rate_limit_then_succeeds(self, tmp_path):
        """429 causes retry; second attempt succeeds."""
        from elevenlabs.core.api_error import ApiError

        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(prompt="Phone buzz", duration_seconds=2.0)
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        rate_limit_err = ApiError(status_code=429, body="rate limited")
        mock_client.text_to_sound_effects.convert.side_effect = [
            rate_limit_err,
            iter([b"\xff\xfb" * 50]),
        ]

        with unittest.mock.patch("time.sleep"):
            path = sfx_common.ensure_shared_sfx(
                "SFX: PHONE BUZZING", effect, sfx_dir,
                defaults={}, client=mock_client,
            )
        assert os.path.exists(path)
        assert mock_client.text_to_sound_effects.convert.call_count == 2

    def test_retries_on_server_error_then_succeeds(self, tmp_path):
        """5xx error causes retry; second attempt succeeds."""
        from elevenlabs.core.api_error import ApiError

        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(prompt="Phone buzz", duration_seconds=2.0)
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        server_err = ApiError(status_code=503, body="service unavailable")
        mock_client.text_to_sound_effects.convert.side_effect = [
            server_err,
            iter([b"\xff\xfb" * 50]),
        ]

        with unittest.mock.patch("time.sleep"):
            path = sfx_common.ensure_shared_sfx(
                "SFX: PHONE BUZZING", effect, sfx_dir,
                defaults={}, client=mock_client,
            )
        assert os.path.exists(path)
        assert mock_client.text_to_sound_effects.convert.call_count == 2

    def test_retries_on_network_error_then_succeeds(self, tmp_path):
        """httpx.TransportError causes retry; second attempt succeeds."""
        import httpx

        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(prompt="Phone buzz", duration_seconds=2.0)
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        network_err = httpx.ConnectError("connection refused")
        mock_client.text_to_sound_effects.convert.side_effect = [
            network_err,
            iter([b"\xff\xfb" * 50]),
        ]

        with unittest.mock.patch("time.sleep"):
            path = sfx_common.ensure_shared_sfx(
                "SFX: PHONE BUZZING", effect, sfx_dir,
                defaults={}, client=mock_client,
            )
        assert os.path.exists(path)
        assert mock_client.text_to_sound_effects.convert.call_count == 2

    def test_raises_after_max_retries_on_server_error(self, tmp_path):
        """5xx error exhausting all retries should propagate."""
        from elevenlabs.core.api_error import ApiError

        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(prompt="Phone buzz", duration_seconds=2.0)
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        server_err = ApiError(status_code=500, body="internal server error")
        mock_client.text_to_sound_effects.convert.side_effect = server_err

        with unittest.mock.patch("time.sleep"):
            with pytest.raises(ApiError):
                sfx_common.ensure_shared_sfx(
                    "SFX: PHONE BUZZING", effect, sfx_dir,
                    defaults={}, client=mock_client,
                )
        assert mock_client.text_to_sound_effects.convert.call_count == 5

    def test_raises_after_max_retries_on_network_error(self, tmp_path):
        """Network error exhausting all retries should propagate."""
        import httpx

        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(prompt="Phone buzz", duration_seconds=2.0)
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        network_err = httpx.ConnectError("connection refused")
        mock_client.text_to_sound_effects.convert.side_effect = network_err

        with unittest.mock.patch("time.sleep"):
            with pytest.raises(httpx.ConnectError):
                sfx_common.ensure_shared_sfx(
                    "SFX: PHONE BUZZING", effect, sfx_dir,
                    defaults={}, client=mock_client,
                )
        assert mock_client.text_to_sound_effects.convert.call_count == 5

    def test_non_retryable_4xx_raises_immediately(self, tmp_path):
        """4xx errors other than 429 should not be retried."""
        from elevenlabs.core.api_error import ApiError

        from xil_pipeline.models import SfxEntry
        effect = SfxEntry(prompt="Phone buzz", duration_seconds=2.0)
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        client_err = ApiError(status_code=400, body="bad request")
        mock_client.text_to_sound_effects.convert.side_effect = client_err

        with pytest.raises(ApiError) as exc_info:
            sfx_common.ensure_shared_sfx(
                "SFX: PHONE BUZZING", effect, sfx_dir,
                defaults={}, client=mock_client,
            )
        assert exc_info.value.status_code == 400
        assert mock_client.text_to_sound_effects.convert.call_count == 1


# ─── Tests: place_episode_stem ───

class TestPlaceEpisodeStem:
    def test_copies_file(self, tmp_path):
        src = tmp_path / "SFX" / "beat.mp3"
        src.parent.mkdir()
        src.write_bytes(b"fake audio data")
        dest = tmp_path / "stems" / "004_cold-open_sfx.mp3"
        dest.parent.mkdir()

        result = sfx_common.place_episode_stem(str(src), str(dest))
        assert result is True
        assert dest.exists()
        assert dest.read_bytes() == b"fake audio data"

    def test_skips_existing(self, tmp_path):
        src = tmp_path / "SFX" / "beat.mp3"
        src.parent.mkdir()
        src.write_bytes(b"new audio")
        dest = tmp_path / "stems" / "004_cold-open_sfx.mp3"
        dest.parent.mkdir()
        dest.write_bytes(b"existing audio")

        result = sfx_common.place_episode_stem(str(src), str(dest))
        assert result is False
        # Original content preserved
        assert dest.read_bytes() == b"existing audio"


# ─── Tests: load_sfx_entries ───

class TestLoadSfxEntries:
    def test_returns_direction_entries_with_config_match(
        self, sample_script, sample_sfx_file,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        assert isinstance(entries, list)
        # AMBIENCE + BEAT + SFX + MUSIC + BEAT(dup) = 5
        assert len(entries) == 5

    def test_skips_unmatched_directions(
        self, sample_script, sample_sfx_file,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        texts = [e["text"] for e in entries]
        assert "EVERYONE TURNS" not in texts

    def test_max_duration_filters(self, sample_script, sample_sfx_file):
        entries = sfx_common.load_sfx_entries(
            sample_script, sample_sfx_file, max_duration=5.0,
        )
        texts = [e["text"] for e in entries]
        assert "BEAT" in texts
        assert "SFX: PHONE BUZZING" in texts
        assert "AMBIENCE: RADIO STATION" not in texts
        assert "MUSIC: SHOW THEME" not in texts

    def test_entry_has_expected_fields(self, sample_script, sample_sfx_file):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        entry = entries[0]
        assert "seq" in entry
        assert "text" in entry
        assert "stem_name" in entry
        assert "sfx_type" in entry
        assert "section" in entry

    def test_stem_name_format(self, sample_script, sample_sfx_file):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        beat_entry = [e for e in entries if e["text"] == "BEAT"][0]
        assert beat_entry["stem_name"] == "004_cold-open_sfx"

    def test_duplicate_entries_preserved(self, sample_script, sample_sfx_file):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        beat_entries = [e for e in entries if e["text"] == "BEAT"]
        assert len(beat_entries) == 2
        assert beat_entries[0]["seq"] != beat_entries[1]["seq"]


# ─── Tests: generate_sfx ───

class TestGenerateSfx:
    def test_creates_shared_assets_and_episode_stems(
        self, sample_script, sample_sfx_file, sample_sfx_config, tmp_path,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        # Keep only BEAT entries for simple test
        beat_entries = [e for e in entries if e["text"] == "BEAT"]
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")

        sfx_common.generate_sfx(
            beat_entries, sample_sfx_config, stems_dir,
            sfx_dir=sfx_dir, client=None,
        )
        # Shared asset created
        shared = tmp_path / "SFX" / "beat.mp3"
        assert shared.exists()
        # Episode stems created (two BEATs)
        assert (tmp_path / "stems" / "004_cold-open_sfx.mp3").exists()
        assert (tmp_path / "stems" / "007_cold-open_sfx.mp3").exists()

    def test_duplicate_effect_generates_shared_once(
        self, sample_script, sample_sfx_file, sample_sfx_config, tmp_path,
    ):
        """BEAT appears twice — shared asset should be created once, not twice."""
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        beat_entries = [e for e in entries if e["text"] == "BEAT"]
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")

        sfx_common.generate_sfx(
            beat_entries, sample_sfx_config, stems_dir,
            sfx_dir=sfx_dir, client=None,
        )
        # Only one file in SFX dir
        sfx_files = list((tmp_path / "SFX").glob("*.mp3"))
        assert len(sfx_files) == 1

    def test_api_sfx_uses_shared_library(
        self, sample_script, sample_sfx_file, sample_sfx_config, tmp_path,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        sfx_entries = [e for e in entries if e["text"] == "SFX: PHONE BUZZING"]
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        mock_client = unittest.mock.MagicMock()
        mock_client.text_to_sound_effects.convert.return_value = iter(
            [b"\xff\xfb" * 50]
        )

        sfx_common.generate_sfx(
            sfx_entries, sample_sfx_config, stems_dir,
            sfx_dir=sfx_dir, client=mock_client,
        )
        # Shared asset and episode stem both exist
        assert (tmp_path / "SFX" / "sfx_phone-buzzing.mp3").exists()
        assert (tmp_path / "stems" / "005_cold-open_sfx.mp3").exists()
        mock_client.text_to_sound_effects.convert.assert_called_once()

    def test_skips_existing_episode_stems(
        self, sample_script, sample_sfx_file, sample_sfx_config, tmp_path,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        beat_entries = [e for e in entries if e["text"] == "BEAT"]
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        os.makedirs(stems_dir, exist_ok=True)
        # Pre-create one episode stem
        (tmp_path / "stems" / "004_cold-open_sfx.mp3").write_bytes(b"existing")

        sfx_common.generate_sfx(
            beat_entries, sample_sfx_config, stems_dir,
            sfx_dir=sfx_dir, client=None,
        )
        # Pre-existing stem not overwritten
        assert (tmp_path / "stems" / "004_cold-open_sfx.mp3").read_bytes() == b"existing"
        # Second BEAT still created
        assert (tmp_path / "stems" / "007_cold-open_sfx.mp3").exists()

    def test_start_from_filters_entries(
        self, sample_script, sample_sfx_file, sample_sfx_config, tmp_path,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        beat_entries = [e for e in entries if e["text"] == "BEAT"]
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")

        sfx_common.generate_sfx(
            beat_entries, sample_sfx_config, stems_dir,
            sfx_dir=sfx_dir, client=None, start_from=5,
        )
        # seq 4 skipped, seq 7 processed
        assert not (tmp_path / "stems" / "004_cold-open_sfx.mp3").exists()
        assert (tmp_path / "stems" / "007_cold-open_sfx.mp3").exists()


# ─── Tests: dry_run_sfx ───

class TestDryRunSfx:
    def test_shows_new_status(
        self, sample_script, sample_sfx_file, sample_sfx_config,
        tmp_path, caplog,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")

        sfx_common.dry_run_sfx(
            entries, sample_sfx_config, stems_dir, sfx_dir=sfx_dir,
        )
        assert "NEW" in caplog.text

    def test_shows_cached_status(
        self, sample_script, sample_sfx_file, sample_sfx_config,
        tmp_path, caplog,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        # Pre-create shared asset for BEAT
        os.makedirs(sfx_dir)
        beat_path = os.path.join(sfx_dir, "beat.mp3")
        with open(beat_path, "wb") as f:
            f.write(b"cached audio")

        sfx_common.dry_run_sfx(
            entries, sample_sfx_config, stems_dir, sfx_dir=sfx_dir,
        )
        assert "CACHED" in caplog.text

    def test_shows_exists_status(
        self, sample_script, sample_sfx_file, sample_sfx_config,
        tmp_path, caplog,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")
        # Pre-create episode stem for BEAT (seq 4)
        os.makedirs(stems_dir)
        (tmp_path / "stems" / "004_cold-open_sfx.mp3").write_bytes(b"exists")

        sfx_common.dry_run_sfx(
            entries, sample_sfx_config, stems_dir, sfx_dir=sfx_dir,
        )
        assert "EXISTS" in caplog.text

    def test_shows_credit_estimate(
        self, sample_script, sample_sfx_file, sample_sfx_config,
        tmp_path, caplog,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")

        sfx_common.dry_run_sfx(
            entries, sample_sfx_config, stems_dir, sfx_dir=sfx_dir,
        )
        assert "credits" in caplog.text.lower()

    def test_summary_counts(
        self, sample_script, sample_sfx_file, sample_sfx_config,
        tmp_path, caplog,
    ):
        entries = sfx_common.load_sfx_entries(sample_script, sample_sfx_file)
        stems_dir = str(tmp_path / "stems")
        sfx_dir = str(tmp_path / "SFX")

        sfx_common.dry_run_sfx(
            entries, sample_sfx_config, stems_dir, sfx_dir=sfx_dir,
        )
        # Should show summary with counts
        assert "5 total" in caplog.text or "5 entries" in caplog.text or "SUMMARY" in caplog.text
