# IndexTTS Adapter — `indextts_adapter.py`, `corti_speech_style.py`, `pronunciation.py`

This adapter exposes `POST /v1/audio/speech`, `POST /v1/audio/speech/stream`
and a read-only `GET /health`, using an already-running IndexTTS gateway.
It does not load a second speech model or play audio itself.

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

`stream_segment_tokens` defaults to 56 (16–120) for the native generator.
`stream_segment_chars` defaults to 40 (16–120) for compatibility synthesis.
Compatibility splitting retains punctuation and keeps long clauses intact,
so it never cuts words to enforce the character target. Larger segments retain
more prosody context; smaller segments can begin speaking sooner.

## Reference prosody and pronunciation

- Calm and low-confidence automatic emotion labels keep reference prosody.
  Both `mood` and `emo_vector` are omitted, because the gateway interprets a
  lone `mood` field as a full-strength preset.
- Other emotions mix at most 0.45 of a preset with at least 0.55 of reference
  prosody. The default maximum is 0.35. Explicit stage tags retain their mood
  and requested intensity within this mixing budget. Unassigned weight is
  no longer filled with the calm preset.
- Chinese enchantment and explicit level labels read canonical Roman levels
  aloud: `锋利 II` → `锋利二级`, `耐久 Ⅲ` → `耐久三级`, `第 XV 层` → `第十五层`.
  English words, model names, player IDs, bare Roman tokens, coordinates and
  fractions are preserved. Conversion is local, not global Unicode folding.
- Recognized stage directions are removed. Ordinary parentheses containing
  facts, item properties and coordinates are retained. Existing pronunciation
  markup such as `<行|XING2>` is retained.
- Unambiguous phrases receive IndexTTS 2.5 phonetic hints just before synthesis:
  `长大` / `生长` use `<长|ZHANG3>`; `长短` / `长度` use `<长|CHANG2>`.
  The longest matching phrase wins (`长大衣` uses `CHANG2`). Bare `长` and
  ambiguous `长得` remain untouched, as do explicit hints, code and URLs.
  The same hints reach whole-utterance, native streaming and legacy segment
  synthesis. Emotion classification and duplicate checks still use plain text;
  caller text and subtitles are not changed. No additional model call is added.
  Syntax follows [IndexTTS pronunciation control](https://github.com/index-tts/index-tts).
- Complete VoxCPM acoustic cues (`[sigh]`, `[laughing]`, `[breath]`, `[Uhm]`,
  `[Shh]`, `Question-*`, `Confirmation-en`, `Surprise-*`, `Dissatisfaction-hnn`)
  are removed before either IndexTTS endpoint receives text. Known emotional
  cues contribute bounded emotion hints; the adapter does not synthesize exact
  sighs, breaths or laughs. Literal words and factual bracketed clauses are
  retained. A request containing only controls returns a short silent WAV
  without invoking either model.
- The normal speech endpoint retains whole-utterance synthesis. Neither
  endpoint combines different utterances or adds filler words or facts.

## Offline tests

```powershell
python -m unittest discover -v
python -m py_compile indextts_adapter.py corti_speech_style.py pronunciation.py stream_audio.py
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
  "stream_segment_tokens": 56,
  "stream_segment_chars": 40
}
```

`emotion_mix=0` keeps reference prosody for every utterance. Values above 0.45
are capped. Invalid non-finite or non-numeric values use the default.
These settings are read on the next request after the file changes.

`CORTI_TTS_PREFS` sets the preference file location. The default is
`~/.config/cortico/tts-prefs.json`. Set the variable when migrating an existing preference file.

## Safe deployment

Do not start a second instance on an occupied port. Keep the existing 8087
gateway and 8090 emotion classifier running. A validation instance can use an
unused port while the old adapter stays connected:

```powershell
$env:CORTI_TTS_PORT = '8011'
python indextts_adapter.py
```

For detached Windows startup, use the existing deployment supervisor or a
hidden `Start-Process` helper. A launcher needs all four Python source files in the
same directory. Inspect `/health` for `version: "15-polyphone-pinyin"` and the
expected port before changing CortiV's `worlds.vtuber.ttsUrl` to that instance.
The adapter's POST endpoint is `/v1/audio/speech`; `ttsUrl` is its base URL.
The VTuber client captures this URL at creation; saving another URL does not
switch an existing client. Updating the adapter on the existing URL needs only
an adapter reload, after in-flight speech completes and interruption is authorized.

## Gateway configuration

`CORTICO_INDEXTTS_URL` selects the IndexTTS gateway base URL (default `http://127.0.0.1:8087`).
It must expose `/tts_raw` and may expose `/tts_stream`.
`CORTICO_DECISION_URL` selects the optional emotion classifier endpoint.
`CORTICO_FFMPEG` selects an ffmpeg executable; otherwise it is resolved from PATH.
The adapter contains no model weights, reference voices, credentials or preference files.
