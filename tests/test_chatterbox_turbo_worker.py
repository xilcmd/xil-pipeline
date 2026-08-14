# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the Chatterbox Turbo worker's pure-Python helpers.

Only ``filter_tags`` (paralinguistic-tag allowlist) and ``_resolve_device``
(CUDA/CPU fallback) are exercised here; the heavy model imports live inside
``main()`` and are not triggered by importing the module, so these run
without the chatterbox venv.
"""

from xil_pipeline.chatterbox_turbo_worker import ALLOWED_TAGS, _resolve_device, filter_tags


def test_keeps_native_paralinguistic_tags():
    assert filter_tags("Well [laugh] that's funny") == "Well [laugh] that's funny"
    assert filter_tags("[cough] excuse me") == "[cough] excuse me"


def test_strips_non_native_tags():
    # ElevenLabs-only tags are not in Turbo's native set — drop them.
    assert filter_tags("I'm so [exhausted] tired").replace("  ", " ") == "I'm so tired"
    assert filter_tags("wait [pause] for it") .replace("  ", " ") == "wait for it"


def test_tag_matching_is_case_insensitive():
    assert filter_tags("[LAUGH]") == "[LAUGH]"
    assert filter_tags("[Chuckle]") == "[Chuckle]"


def test_mixed_tags():
    out = filter_tags("[laugh] hello [exhausted] world [cough]")
    assert "[laugh]" in out
    assert "[cough]" in out
    assert "[exhausted]" not in out


def test_allowlist_matches_turbo_tokenizer_exactly():
    # The 19 dedicated tokens (IDs 50257-50275) in the Turbo tokenizer's
    # added_tokens.json. Any drift here means tags get spoken aloud or
    # silently dropped, so pin the whole set rather than spot-checking.
    assert ALLOWED_TAGS == {
        "angry", "fear", "surprised", "whispering", "advertisement",
        "dramatic", "narration", "crying", "happy", "sarcastic",
        "clear throat", "sigh", "shush", "cough", "groan",
        "sniff", "gasp", "chuckle", "laugh",
    }


def test_keeps_emotion_tags():
    assert filter_tags("[angry] Get out!") == "[angry] Get out!"
    assert filter_tags("[whispering] don't move") == "[whispering] don't move"
    assert filter_tags("[clear throat] as I was saying") == "[clear throat] as I was saying"


def test_strips_plural_forms_that_have_no_token():
    # Turbo has "[laugh]", not "[laughs]" — keeping the plural would put
    # literal text through the tokenizer and get it read aloud.
    assert filter_tags("he [laughs] loudly").replace("  ", " ") == "he loudly"
    assert filter_tags("she [coughs]").strip() == "she"


class TestResolveDevice:
    """CUDA requested but unavailable must degrade to CPU rather than fail
    to load the model — a real explicit "cpu" request must never be
    overridden either way."""

    def test_falls_back_to_cpu_when_cuda_unavailable(self):
        assert _resolve_device("cuda", cuda_available=False) == "cpu"

    def test_keeps_cuda_when_available(self):
        assert _resolve_device("cuda", cuda_available=True) == "cuda"

    def test_explicit_cpu_request_is_never_overridden(self):
        assert _resolve_device("cpu", cuda_available=True) == "cpu"
        assert _resolve_device("cpu", cuda_available=False) == "cpu"
