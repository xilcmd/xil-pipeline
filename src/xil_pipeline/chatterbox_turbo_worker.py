# SPDX-FileCopyrightText: 2026 John Brissette <xilcmd@gmail.com>
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Persistent Chatterbox Turbo TTS worker process.

Run with the chatterbox venv Python, not the main pipeline venv::

    venv-chatterbox/bin/python3 chatterbox_turbo_worker.py [cuda|cpu]

Loads ``ChatterboxTurboTTS`` (HuggingFace repo ``ResembleAI/chatterbox-turbo``).
Classic Chatterbox (``chatterbox_worker.py``) was removed in #62.

If ``cuda`` is requested but unavailable, the worker falls back to ``cpu``
automatically (slower, but functional) rather than failing to load the model —
see :func:`_resolve_device`.

Protocol (newline-delimited JSON on stdin/stdout):

  Startup:  worker prints  {"ready": true, "sr": <int>, "device": "cuda"|"cpu"}
  Request:  {"text": "...", "out_path": "...", "ref_audio": "<path>|null",
             "cond_path": "<path>|null"}
  Response: {"done": true} | {"done": true, "skipped": true} | {"error": "..."}

Paralinguistic tags
-------------------
Unlike the classic worker (which strips *every* ``[...]`` token), Turbo natively
renders a set of paralinguistic cues — ``[laugh]``, ``[cough]``, ``[chuckle]``,
etc. This worker keeps bracketed tokens whose name is in ``ALLOWED_TAGS`` and
strips all others (e.g. ElevenLabs-only tags like ``[exhausted]``/``[pause]``),
so unsupported tags are never read aloud or mis-tokenized.

cond_path caching
-----------------
Same ``.conds.pt`` fast/slow-path caching as the classic worker. Turbo
conditionals are **not** interchangeable with classic ones, so the producer/
sampler point ``cond_path`` at a Turbo-specific file (``.turbo.conds.pt``).

Turbo's ``generate()`` ignores ``exaggeration``/``cfg_weight``/``min_p`` (it logs
a warning if they are non-zero), so this worker does not accept or forward them.

Long lines
----------
Turbo stops sampling after 1000 speech tokens (``max_gen_len`` in
``T3.inference_turbo``), and it samples at 25 tokens per second, so one
``generate()`` call can never return more than 40 seconds of audio. A longer
line does not fail: the model crams it into the cap and the stem comes out
fast, garbled and repetitive. So the worker splits text longer than
:data:`MAX_CHUNK_CHARS` at sentence boundaries (see :func:`split_text`),
renders each chunk with the same voice conditionals, and joins the chunks
with :data:`CHUNK_GAP_S` of silence.
"""

import contextlib
import json
import os
import re
import sys
import tempfile

# Native paralinguistic tags Chatterbox Turbo renders as audio cues. Keep these
# in the text; strip every other bracketed token.
#
# This is the exact set of dedicated tokens (IDs 50257-50275) in the Turbo
# tokenizer's added_tokens.json — not a guess from prose docs. Anything outside
# it has no token and would be tokenized as ordinary text, i.e. spoken aloud.
# Note there are no plural forms: "[laughs]" is not a token, "[laugh]" is.
# Re-derive after a model bump with::
#
#     python -c "import json,glob; print(sorted(json.load(open(glob.glob(
#       '~/.cache/huggingface/hub/models--ResembleAI--chatterbox-turbo'
#       '/snapshots/*/added_tokens.json')[0]))))"
ALLOWED_TAGS = {
    "angry",
    "fear",
    "surprised",
    "whispering",
    "advertisement",
    "dramatic",
    "narration",
    "crying",
    "happy",
    "sarcastic",
    "clear throat",
    "sigh",
    "shush",
    "cough",
    "groan",
    "sniff",
    "gasp",
    "chuckle",
    "laugh",
}

# Longest chunk sent to one generate() call. Turbo's hard cap is 40 s of audio;
# the pipeline's dialogue runs at about 10.5 characters per second, so 250
# characters (about 24 s) leaves room for slow delivery and inline tags.
MAX_CHUNK_CHARS = 250

# Silence inserted between chunks of one split line, in seconds. Turbo already
# ends each chunk with three silence tokens (about 120 ms).
CHUNK_GAP_S = 0.1

# Sentence end: . ! ? or … (optionally followed by closing quotes/brackets),
# then whitespace.
_SENTENCE_END_RE = re.compile(r'(?:(?<=[.!?…])|(?<=[.!?…]["\'”’)\]]))\s+')

# Weaker break points, tried when a single sentence is still too long.
_CLAUSE_END_RE = re.compile(r'(?<=[,;:—])\s+')

# Matches any [...] token; the replacement callback decides keep vs. drop.
_TAG_RE = re.compile(r'\[([^\]]*)\]')


def filter_tags(text: str) -> str:
    """Drop bracketed tokens not in :data:`ALLOWED_TAGS` (case-insensitive)."""
    def _repl(match: "re.Match[str]") -> str:
        name = match.group(1).strip().lower()
        return match.group(0) if name in ALLOWED_TAGS else ""

    return _TAG_RE.sub(_repl, text)


def _pack(pieces: list[str], limit: int) -> list[str]:
    """Greedily join *pieces* with spaces into chunks of at most *limit* chars."""
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 1 + len(piece) > limit:
            chunks.append(current)
            current = piece
        else:
            current = f"{current} {piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks


def _split_long(sentence: str, limit: int) -> list[str]:
    """Break one over-long sentence at clause marks, then at spaces."""
    pieces: list[str] = []
    for clause in _CLAUSE_END_RE.split(sentence):
        if len(clause) <= limit:
            pieces.append(clause)
        else:
            pieces.extend(clause.split())
    return _pack(pieces, limit)


def split_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split *text* into chunks Turbo can render without hitting its 40 s cap.

    Text within *limit* is returned whole. Longer text is cut at sentence
    ends and the sentences are packed back together up to *limit*; a sentence
    that is longer than *limit* on its own is cut at commas, semicolons,
    colons or dashes, and as a last resort between words. A single word longer
    than *limit* is kept intact. Chunks never start or end with whitespace.
    """
    text = " ".join(text.split())
    if len(text) <= limit:
        return [text] if text else []
    pieces: list[str] = []
    for sentence in _SENTENCE_END_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) <= limit:
            pieces.append(sentence)
        else:
            pieces.extend(_split_long(sentence, limit))
    return _pack(pieces, limit)


def _claim_protocol_stdout():
    """Take private ownership of stdout, pointing ``sys.stdout`` at stderr.

    Turbo's s3gen prints progress to stdout mid-generation ("S3 Token -> Mel
    Inference…"), which lands in the middle of the JSON protocol stream and
    makes the client fail to decode the response. Duplicating the fd keeps a
    clean channel for protocol writes while library output goes to stderr,
    where the producer already forwards it.

    Returns the write handle for protocol messages.
    """
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "w")
    sys.stdout = sys.stderr
    return proto


def _send(stream, msg: dict) -> None:
    """Write *msg* to *stream* as one newline-delimited JSON protocol line."""
    stream.write(json.dumps(msg) + "\n")
    stream.flush()


def _resolve_device(requested: str, cuda_available: bool) -> str:
    """Return "cpu" if *requested* is "cuda" but no CUDA device is available.

    Passes through unchanged otherwise — an explicit "cpu" request is never
    overridden, and "cuda" is kept when it's actually usable.
    """
    if requested == "cuda" and not cuda_available:
        return "cpu"
    return requested


def main() -> None:
    """Load ChatterboxTurboTTS and serve generation requests via JSON protocol."""
    device = sys.argv[1] if len(sys.argv) > 1 else "cuda"

    # Claim stdout before importing the model libs — they print on import.
    proto = _claim_protocol_stdout()

    # Suppress noisy deprecation warnings from diffusers / torch internals
    import warnings
    warnings.filterwarnings("ignore", category=FutureWarning)

    import torch  # type: ignore[import]
    import torchaudio  # type: ignore[import]
    from chatterbox.tts_turbo import ChatterboxTurboTTS  # type: ignore[import]
    from pydub import AudioSegment  # type: ignore[import]

    resolved = _resolve_device(device, torch.cuda.is_available())
    if resolved != device:
        print(f"[chatterbox-turbo] CUDA requested but not available, "
              f"falling back to {resolved}", file=sys.stderr, flush=True)
    device = resolved

    model = ChatterboxTurboTTS.from_pretrained(device=device)

    # chatterbox-tts 0.1.7 bug: Turbo's norm_loudness() upcasts the reference
    # wav to float64 (pyloudnorm), but the S3 tokenizer's mel filters are
    # float32 -> "expected scalar type Double but found Float" on every ref
    # clip. Restore float32 so loudness normalization still applies. No-op
    # once upstream fixes it.
    _norm_loudness = model.norm_loudness

    def _norm_loudness_f32(wav, sr, *args, **kwargs):
        return _norm_loudness(wav, sr, *args, **kwargs).astype("float32")

    model.norm_loudness = _norm_loudness_f32

    _send(proto, {"ready": True, "sr": model.sr, "device": device})

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except json.JSONDecodeError as exc:
            _send(proto, {"error": f"JSON decode: {exc}"})
            continue

        text = filter_tags(req["text"]).strip()
        out_path = req["out_path"]
        ref_audio = req.get("ref_audio") or None
        cond_path = req.get("cond_path") or None

        if not text:
            _send(proto, {"done": True, "skipped": True})
            continue

        tmp_wav = None
        tmp_mp3 = None
        try:
            chunks = split_text(text)
            if len(chunks) > 1:
                print(f"[split] {len(text)} chars → {len(chunks)} chunks", file=sys.stderr, flush=True)

            if cond_path and os.path.exists(cond_path):
                # Fast path: pre-computed conditioning — skip ref audio processing
                from chatterbox.tts_turbo import Conditionals  # type: ignore[import]
                model.conds = Conditionals.load(cond_path, map_location=device)
                print(f"[conds] loaded ← {os.path.basename(cond_path)}", file=sys.stderr, flush=True)
                parts = [model.generate(chunks[0])]
            elif ref_audio:
                # Slow path: compute from ref audio, save conds for next session
                parts = [model.generate(chunks[0], audio_prompt_path=ref_audio)]
                if cond_path and model.conds is not None:
                    os.makedirs(os.path.dirname(os.path.abspath(cond_path)), exist_ok=True)
                    model.conds.save(cond_path)
                    print(f"[conds] saved  → {os.path.basename(cond_path)}", file=sys.stderr, flush=True)
            else:
                # No ref, no cache: use model default voice
                parts = [model.generate(chunks[0])]

            # Later chunks reuse model.conds, which the first call set (or
            # the default voice already holds), so every chunk shares a voice.
            parts.extend(model.generate(chunk) for chunk in chunks[1:])
            gap = torch.zeros(1, int(CHUNK_GAP_S * model.sr), dtype=parts[0].dtype)
            joined = [parts[0]]
            for part in parts[1:]:
                joined.extend((gap, part))
            wav = torch.cat(joined, dim=1)

            # WAV → temp file → MP3 → final path (atomic replace)
            tmp_fd, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(tmp_fd)
            torchaudio.save(tmp_wav, wav, model.sr)

            stem_dir = os.path.dirname(out_path) or "."
            tmp_fd2, tmp_mp3 = tempfile.mkstemp(suffix=".mp3", dir=stem_dir)
            os.close(tmp_fd2)
            AudioSegment.from_wav(tmp_wav).export(
                tmp_mp3,
                format="mp3",
                bitrate="128k",
                parameters=["-ar", "44100"],
            )
            os.replace(tmp_mp3, out_path)
            tmp_mp3 = None  # replaced — don't clean up

            _send(proto, {"done": True})

        except Exception as exc:  # noqa: BLE001
            _send(proto, {"error": str(exc)})

        finally:
            for p in (tmp_wav, tmp_mp3):
                if p is not None:
                    with contextlib.suppress(FileNotFoundError):
                        os.unlink(p)


if __name__ == "__main__":
    main()
