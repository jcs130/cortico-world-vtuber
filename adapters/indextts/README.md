# IndexTTS Adapter — `indextts_adapter.py`, `corti_speech_style.py`, `spoken_text.py`, `pronunciation.py`

This adapter exposes `POST /v1/audio/speech`, `POST /v1/audio/speech/stream`
and a read-only `GET /health`, using an already-running IndexTTS gateway.
It does not load a second speech model or play audio itself.

Both synthesis endpoints accept request-scoped `voice`, `speed` (0.5–2),
`emotion_mix` (0–0.45) and `emotion_min_confidence` (0–1). Missing fields use
the deployment preference file; an empty or `default` voice also keeps that
preference. Overrides do not modify the preference file or other clients.

## Text-segment streaming

The stream returns a fixed 44-byte WAV header followed by mono PCM16LE.
It first uses the gateway's `/tts_stream` generator endpoint. An older gateway
returning 404/405 uses `/tts_raw` for successive punctuation-delimited text
segments; each segment is delivered before the next one finishes synthesis.
This is generation by complete text segments. A single short segment still
waits for its synthesis; no token-level latency is promised.

Voice and emotion settings are fixed for the whole request. A continuous
`atempo` filter changes speed without changing pitch or resetting at segment
boundaries. The first PCM is required before HTTP 200; later failure aborts
the response instead of reporting successful EOF or replaying heard speech.
Cancelling closes upstream and the converter; a running model finishes its
current text segment before cancellation takes effect.

The adapter controls both native and compatibility synthesis at sentence ends,
retaining punctuation runs, closing quotes, decimals and phonetic atoms.
Short comma clauses stay together so the model can generate one continuous
intonation. `stream_segment_chars` defaults to 40 readable characters (16–120):
longer sentences split at complete comma clauses, with prefixes shorter than six
readable characters attached to their following clause. This is a soft target;
long unpunctuated clauses stay intact up to the model's position capacity.
`stream_segment_tokens` defaults to 120 (16–120), leaving room for these phrases
and their pronunciation hints. A smaller explicit native budget can cause extra
model-side segmentation. It is independent of the adapter's readable-length target.
Native clause streams are joined as one PCM stream with one WAV header, retaining
voice and emotion parameters. Cancellation never opens a later clause. Streaming
utterances keep their clause requests together at the adapter queue, so later
prefetch cannot interrupt the current sentence. This also works on an older
gateway without a model-process reload. Health reports `native-clause-segments`,
the actual clause count, and the adapter's queue wait in the last stream record.
The VTuber script parser also emits complete clauses after 24 spoken characters
instead of waiting for 40 and a sentence end. Emotion metadata does not count
toward that minimum, and short openings remain together.

`X-TTS-Request-First-Audio-Ms` includes text preparation and upstream waiting.
`X-TTS-Preparation-Ms` measures preparation alone; the existing
`X-TTS-First-Audio-Ms` measures upstream waiting and tempo conversion.
Native requests also ask for one sampling beam, retaining the voice and emotion
settings. A gateway must forward `num_beams` to the model for this optimization.
`patches/index-tts-streaming.patch` supplies forwarding, queue timing and
clause-preserving native splitting for the existing IndexTTS 2.5 layout with
`src/indextts/infer_v2_5.py` and `indextts/gateway/`. From that installation root,
run `git apply --unidiff-zero --check` on the patch, apply it with
`git apply --unidiff-zero`, then run the gateway's offline tests.
Reload the model gateway separately after in-flight speech ends.
The adapter does not apply patches or restart the gateway automatically.
The patched native splitter keeps short prefixes with a following complete
clause, providing enough initial audio for the next segment to be generated.
Health reports the selected token budget, preparation phase times and the last
completed stream's first-audio, total generation and audio durations. These
measure arrival at the adapter, before the player's audio output.
When supported by the gateway, `upstream_queue_ms` separates time waiting for
the shared model from synthesis. Missing queue timing is reported as null.

## Reference prosody and pronunciation

- Reviewed proper nouns can use one complete-word phonetic atom. The pronunciation
  lexicon renders `苦力怕` as `<苦力怕|KU3 LI4 PA4>` for synthesis, preserving its
  readable text for subtitles and dedup. This avoids treating the final syllable
  as an independent word. Ordinary words remain natural Chinese; existing caller
  hints, code and URLs are preserved. Health reports `term_policy` and `term_count`.
- Calm and low-confidence automatic emotion labels keep reference prosody.
  Both `mood` and `emo_vector` are omitted, because the gateway interprets a
  lone `mood` field as a full-strength preset.
- Other emotions mix at most 0.45 of a preset with at least 0.55 of reference
  prosody. The default maximum is 0.35. Explicit stage tags retain their mood
  and requested intensity within this mixing budget. Unassigned weight is
  no longer filled with the calm preset.
- Chinese enchantment and explicit level labels read canonical Roman levels
  aloud: `锋利 II` → `锋利二级`, `耐久 Ⅲ` → `耐久三级`, `第 XV 层` → `第十五层`.
  Adjacent labels are all converted: `效率V耐久II` → `效率五级耐久二级`.
  `经验修补 I` and its `修补 I` alias also read their level.
  English words, model names, player IDs and bare Roman tokens are retained.
- Synthesis uses readable Chinese numeric tokens (`1、2、3` → `一、二、三`).
  Counts, signed coordinates and decimals keep their values. Leading-zero
  strings are read digit by digit; numeric parts of IDs and model names are retained.
  Literal code, URLs and explicit pronunciation hints are retained.
- Known Minecraft fields use spoken labels. `age 5/7` becomes
  `生长进度五，成熟需要到七`; it does not predict a growth time.
  `health` / `food` / `mana` ratios retain current and maximum values;
  `cooldownRemainingMs=1250` becomes `冷却还剩一点二五秒`.
  Other known labels include `moisture`, `durability` and `cooldown`.
  Unknown fields retain their text. The agent still needs to explain raw tool
  results in ordinary language; this deterministic fallback adds no model call.
- Recognized stage directions are removed. Ordinary parentheses containing
  facts, item properties and coordinates are retained. Existing pronunciation
  markup such as `<行|XING2>` is retained.
- Prepared local G2PW resources resolve polyphonic characters in the complete
  utterance, after dictionary phrase matching. Context-dependent homographs such
  as `长得` and `一行` bypass a dictionary's default reading. Only readings in the character's
  Mandarin lexicon are accepted. Explicit hints, code and URLs remain intact.
  Ordinary, native streaming and compatibility synthesis share the resolved
  input; compatibility segments retain the selected readings from earlier
  clauses. Emotion classification, duplicate checks and subtitles use plain text.
  Context analysis uses CPU inference and no conversational LLM request.
  Optional `CORTICO_PRONUNCIATION_DECISION_URL` enables a local SystemOne choice
  classifier for unresolved common homographs. `polyphonic_readings.json` gives
  each candidate's meaning. Each query marks its own target in a clause, with
  preceding context available; different uses of the same character do not share
  one classifier state. Dictionary readings
  remain authoritative. Choices below 0.8 confidence, invalid choices or a
  0.6-second total review budget retain G2PW's reading; `/health` exposes selector failures.
  A dependency or inference failure uses the small unambiguous phrase fallback;
  `/health` reports availability, last analysis time and fallback count.
  Syntax follows [IndexTTS pronunciation control](https://github.com/index-tts/index-tts).
- Automatic hints leave neutral-tone syllables as Chinese characters, including
  `了`, `的` and auxiliary `着`/`得`. Common contextual homographs retain explicit
  non-neutral readings only when they differ from the dictionary's ordinary
  first reading. For example, `种子` and `看看` stay in natural Chinese while
  `种下` and growth-related `长` retain their distinguishing pronunciation.
  Other characters retain their sentence text, including dictionary homographs
  outside this reviewed set. Caller hints remain supported.
  `/health` reports `annotation_policy: nondefault-nonneutral` and
  `stream_segment_policy: sentences-soft-clause-limit` and the effective
  `stream_segment_chars` target.
- Each synthesis request logs `text-prepared` with its original script, readable
  speech and actual phonetic input. Health keeps only the latest preparation's
  timestamp, input hash, lengths, voice and hint count. `upstream_stream_path`
  and `X-TTS-Upstream-Mode` distinguish the native and compatibility paths;
  the initial health value is `unverified` until a stream is requested.
- Complete VoxCPM acoustic cues (`[sigh]`, `[laughing]`, `[breath]`, `[Uhm]`,
  `[Shh]`, `Question-*`, `Confirmation-en`, `Surprise-*`, `Dissatisfaction-hnn`)
  are removed before either IndexTTS endpoint receives text. Known emotional
  cues contribute bounded emotion hints; the adapter does not synthesize exact
  sighs, breaths or laughs. Literal words and factual bracketed clauses are
  retained. A request containing only controls returns a short silent WAV
  without invoking either model.
- The normal speech endpoint retains whole-utterance synthesis. Neither
  endpoint combines different utterances or adds filler words or facts.

On Windows, start with `python -X utf8 indextts_adapter.py` (or set
`PYTHONUTF8=1`) so upstream model resources are read as UTF-8.

## Offline tests

Optional contextual pronunciation requires `requirements-pronunciation.txt`,
the upstream [G2PW ONNX model](https://github.com/GitYCC/g2pW) and a local
`bert-base-chinese` tokenizer directory. Set `CORTICO_G2PW_MODEL_DIR` and
`CORTICO_G2PW_TOKENIZER_DIR` before starting the adapter. Resources are prepared
by the operator; requests never download them. The upstream DataLoader is set
to zero workers to avoid process creation on each Windows request. Tests use
an offline converter fixture and need no weights or network.

```powershell
python -m unittest discover -v
python -m py_compile indextts_adapter.py corti_speech_style.py spoken_text.py pronunciation.py stream_audio.py
```

Tests make no real synthesis requests and start no listener. A local ffmpeg
test verifies that tempo conversion emits PCM before its input stream ends.

## Preferences

The existing `voice` and `speed` preferences remain compatible. Optional keys:

```json
{
  "voice": "taozi",
  "speed": 1.0,
  "emotion_mix": 0.35,
  "emotion_min_confidence": 0.65,
  "stream_segment_tokens": 120,
  "stream_segment_chars": 40
}
```

`emotion_mix=0` keeps reference prosody for every utterance. Values above 0.45
are capped. Invalid non-finite or non-numeric values use the default.
These settings are read on the next request after the file changes.

`CORTI_TTS_PREFS` sets the preference file location. Its default remains the
existing deployment path under `.copaw/workspaces/default/tmp` for compatibility.

## Safe deployment

Do not start a second instance on an occupied port. Keep the existing 8087
gateway and 8090 emotion classifier running. A validation instance can use an
unused port while the old adapter stays connected:

```powershell
$env:CORTI_TTS_PORT = '8011'
python indextts_adapter.py
```

For detached Windows startup, use the existing deployment supervisor or a
hidden `Start-Process` helper. A launcher needs the Python source files including
`speech_segments.py`, and
`polyphonic_readings.json` in the
same directory. Inspect `/health` for `version: "24-word-prosody"`,
`pronunciation.context_ready: true` and the
expected port before changing CortiV's `worlds.vtuber.ttsUrl` to that instance.
The adapter's POST endpoint is `/v1/audio/speech`; `ttsUrl` is its base URL.
The VTuber client captures this URL at creation; saving another URL does not
switch an existing client. Updating the adapter on the existing URL needs only
an adapter reload, after in-flight speech completes and interruption is authorized.

The original deployed source was backed up as
`indextts_adapter_decision.py.bak-natural-20261004`. For rollback, point CortiV
back to the previous healthy adapter endpoint. Replacing Python source alone
does not change an already-running process.

## Gateway configuration

`CORTICO_INDEXTTS_URL` selects the IndexTTS gateway base URL (default `http://127.0.0.1:8087`).
It must expose `/tts_raw` and may expose `/tts_stream`.
`CORTICO_DECISION_URL` selects the optional emotion classifier endpoint.
`CORTICO_FFMPEG` selects an ffmpeg executable; otherwise it is resolved from PATH.
The adapter contains no model weights, reference voices, credentials or preference files.

## Managed lifecycle

The VTuber extension can launch this directory with `ttsService.kind=indextts` and
`ttsService.autoStart=true`. Its service manager passes the configured model gateway,
voice preference file, pronunciation assets and decision endpoint to Python.
A managed adapter reports its instance credential in `/health` and exits when the
parent stdin pipe closes. Standalone use without `CORTICO_TTS_MANAGED_TOKEN` is unchanged.
The model gateway has a separate lifecycle and is never terminated by the extension.
Managed use on Windows requires Python 3.12 or later for nonblocking pipe reads.
