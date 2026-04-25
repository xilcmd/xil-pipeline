"""Tests for xil_gui helper functions (no Gradio dependency required)."""
import pytest

from xil_pipeline.xil_gui import _sanitize_extra_flags


class TestSanitizeExtraFlags:
    def test_empty_string_returns_empty_list(self):
        assert _sanitize_extra_flags("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert _sanitize_extra_flags("   ") == []

    def test_single_flag(self):
        assert _sanitize_extra_flags("--dry-run") == ["--dry-run"]

    def test_flag_with_value(self):
        assert _sanitize_extra_flags("--gap-ms 600") == ["--gap-ms", "600"]

    def test_multiple_flags(self):
        result = _sanitize_extra_flags("--dry-run --gap-ms 400")
        assert result == ["--dry-run", "--gap-ms", "400"]

    def test_quoted_path_with_spaces(self):
        result = _sanitize_extra_flags('"scripts/my script.md"')
        assert result == ["scripts/my script.md"]

    def test_plain_path(self):
        result = _sanitize_extra_flags("scripts/sample_S01E01.md")
        assert result == ["scripts/sample_S01E01.md"]

    def test_rejects_semicolon(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--dry-run; rm -rf /")

    def test_rejects_pipe(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--output /dev/stdout | cat")

    def test_rejects_ampersand(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--episode S01E01 && evil")

    def test_rejects_backtick(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--show `id`")

    def test_rejects_dollar_sign(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--show $SHELL")

    def test_rejects_subshell_parens(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--show $(whoami)")

    def test_rejects_redirect(self):
        with pytest.raises(ValueError, match="Unsafe character"):
            _sanitize_extra_flags("--output > /etc/passwd")

    def test_rejects_unbalanced_quote(self):
        with pytest.raises(ValueError, match="Invalid flag syntax"):
            _sanitize_extra_flags("--output 'unclosed")

    def test_flag_with_equals_value(self):
        result = _sanitize_extra_flags("--output=masters/ep.mp3")
        assert result == ["--output=masters/ep.mp3"]

    def test_numeric_value(self):
        result = _sanitize_extra_flags("--start-from 5 --stop-at 10")
        assert result == ["--start-from", "5", "--stop-at", "10"]
