# `voxcpm2_server.py`: official VoxCPM2 Hi-Fi speech service

`GET /metrics` returns only time, health, synthesizing/waiting counts and the last
request's synthesis duration. Counts start after input preparation. The duration
sums worker iterator advancement and excludes the inference-lock wait, output
queue backpressure and HTTP transmission. Cancellation waits for worker
acknowledgement before releasing the inference lock. It is not playback latency.
The performer reports unsent beats separately through
`GET /api/console/providers/world:vtuber/panels/tts/metrics`; this read neither
probes the speech backend nor starts synthesis. A beat may contain speech or
actions, so pending_items is not a count of audio chunks.

Run the model-free counter and worker tests with
`python -m unittest discover -s adapters/voxcpm2 -p test_synthesis_metrics.py`.

The service loads the official VoxCPM2 Python model once and caches a combined
reference plus continuation prompt. Each request uses the configured reference
audio and its transcript. The SHA256 in the configuration must match the file.
The adapter normalizes a trailing expressive `~`, `～` or `〜` to a sentence
stop in synthesis text, preserving closing quotes, native vocal tags and an
existing question/exclamation mark. This is a checkpoint fallback: NVVSpeech
could read the complete text and then babble until the generation limit when
two reproduced utterances ended in `咯～`. The same inputs ended normally with
a full stop. Subtitles and reference transcripts are unchanged; internal ranges,
URLs and code literals are not rewritten. Health reports
`terminal_wave_policy: "sentence-stop"`. This prevents the reproduced trigger,
not every possible model hallucination.

Hi-Fi follows reference delivery; parenthesized emotion and speed instructions
are unsupported. Stage cues are removed before synthesis. Native square-bracket
vocal tags such as `[laughing]`, `[sigh]`, `[Uhm]` and `[Shh]` reach the model
in their original positions, including tag-only utterances. Their audible
delivery depends on the reference voice and surrounding text; `[laughing]`
does not select a separate chuckle intensity.

## Pronunciation aliases

The optional `pronunciation_aliases` configuration maps literal, case-sensitive
names to their spoken spelling, for example `{"示例名": "Mira"}`. Keys and
values must be nonempty strings. Longer matches take precedence and replacement
text is not processed again. Aliases apply to the synthesized input after stage
cue cleanup; reference audio, reference transcript and client subtitles retain
their original text. Add full bilingual names as aliases to avoid speaking the
same name twice. Synthesis records include the final `spoken_text_sha256`.

Deploy the adapter's shared `indextts` text modules alongside this server. Its
`corti_speech_style.py` must support the `preserve_acoustic_cues` argument.

## Optional text delivery control

Set `enable_style_instructions: true` only after comparing audio on the intended
reference voice. This adds a cached reference-only prompt using the **same**
configured WAV and the same model weights. With no configured default, requests
without `instructions` retain Hi-Fi `ref_continuation`; a concise description uses the
official controllable `reference` mode and prepends that description inside the
decoder only. Do not insert descriptions into the spoken `input` or the reference
transcript. Both response routes validate the voice/hash and use this same policy.

The extension script can use `(语气：轻快带笑，自然交谈)` at a complete sentence
boundary. The parser carries this as separate metadata for subsequent speech in
that script; it occupies no subtitle or alignment units and resets with the next
script. A new description changes delivery at that boundary. Use at most 64
characters without brackets/newlines. The client checks `/health` for explicit
`style_instructions_supported: true` before sending it; disabled or old backends
fail visibly rather than silently ignoring descriptions.

Example request: `{"input":"群星啊，回应我的呼唤！","voice":"default",
"instructions":"清晰有力，带一点戏剧感"}`. Describe delivery, pace and emotion,
not a different speaker, gender or voice identity. Keep descriptions simple and
use native vocal cues sparingly. This mode can trade some Hi-Fi fidelity for
control; a fixed reference hash alone does not prove perceived voice consistency.
Compare the same sentences with no description and with two contrasting styles,
check transcription, tone and latency, then validate real playback before
advertising the capability as enabled. Startup also warms the controlled path on
the existing owner thread. `/health`, streaming `X-TTS-Prompt-Mode` and synthesis
records distinguish the two modes; records include instruction length/hash.

The optional `default_instructions` service setting applies a delivery description
when a request omits `instructions`. It requires `enable_style_instructions: true`
and the same 64-character/plain-text limits. Per-request descriptions override
the default; an explicit JSON `"instructions": null` selects Hi-Fi continuation.
The default never changes spoken input, reference WAV or reference transcript.
Startup validates and warms the configured description. `/health` reports
`default_instructions` and the effective default `prompt_mode`; request records
identify `instructions_source` as `default`, `request` or `none`.

Create an isolated Python environment with a PyTorch build compatible with the
GPU, then install `requirements.txt`. Model weights, references and deployment
configuration stay outside the extension package. Download the official
`openbmb/VoxCPM2` model with an explicitly selected revision.

Example configuration:

```json
{
  "host": "127.0.0.1",
  "port": 8014,
  "device": "cuda",
  "voice": "speaker",
  "model_dir": "/path/to/model",
  "model_revision": "the-selected-model-revision",
  "reference_file": "/path/to/reference.wav",
  "reference_sha256": "the-reference-file-sha256",
  "reference_text": "Exact transcript of the reference recording.",
  "seed": 42,
  "cfg_value": 2.0,
  "inference_timesteps": 10,
  "max_steps": 200,
  "segment_chars": 80,
  "threads": 4,
  "optimize": true,
  "cuda_priority": -1,
  "stream_prebuffer_ms": 480,
  "warmup_texts": ["晚上好呀。", "以千灯之名，唤醒沉睡的星火。群星啊，回应我的呼唤！"],
  "queue_timeout_sec": 25,
  "minimum_free_gpu_mib": 7000
}
```

Run `python voxcpm2_server.py --config /path/to/voice.json`. Set
`NUMBA_CACHE_DIR` to a writable directory. With local model files,
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` prevent model downloads.
Deploy the adapter together with `inference_worker.py` and its sibling
`pronunciation_bridge.py` and its sibling
`adapters/indextts/{corti_speech_style,speech_segments,spoken_text,pronunciation}.py`
and `polyphonic_readings.json` from the
same checkout, preserving that directory layout. Updating only the server can
leave an incompatible text helper even while `/health` succeeds. Before warming
the model, import `prepare_text` in the service environment and check an ordinary
sentence and a native vocal cue; before switching live speech back, require an
actual generated WAV as well as the expected voice hash and health response.
The HTTP listener becomes available after model loading, prompt caching and
warmup. Use a service supervisor for startup and bounded crash recovery.

`optimize: true` enables the official CUDA `torch.compile` paths (requires the
Triton version supplied for the installed PyTorch build). Model loading, warmup
and every generation stay on one dedicated inference thread. HTTP handler
threads never execute the model. This avoids the CUDA graph thread ownership
failure documented in the upstream [FAQ](https://voxcpm.readthedocs.io/en/latest/faq.html#cuda-graphs-and-multi-threading).
Compiled startup is intentionally completed before opening the HTTP listener;
it can take several minutes the first time. Set `TORCHINDUCTOR_CACHE_DIR` to a
writable persistent directory. `cuda_priority` selects the CUDA stream priority
within this process; it does not reserve GPU capacity against other services.
Warm up representative text lengths before switching a live client to the
service, and measure both first audio and generation/audio duration under the
actual shared GPU load. A fast first chunk alone does not prevent starvation.

Connect the VTuber extension with `ttsService.kind: "external"`,
`ttsService.autoStart: true` and `ttsUrl` set to this service. It remains running
when the extension reloads. A client voice profile may supply the same reference
and transcript; different references or voice names are rejected. The server
owns CFG, inference steps and generation limits. Requests may override the seed
for a retry. `voice: "default"` selects the configured reference voice.

## HTTP contract

- `GET /health` reports backend, reference hash, prompt mode, cached features,
  request timings and completed, cancelled and failed counts.
- `GET /voices` returns the single configured voice.
- `POST /v1/audio/speech` returns a complete mono PCM16 WAV.
- `POST /v1/audio/speech/stream` returns a chunked 44-byte WAV header and PCM16
  latent patches. HTTP 200 is sent after the first nonempty audio arrives.
- A generation failure before audio returns 503. A later failure aborts the
  response; it does not complete or replay already delivered speech.
- Requests share one model lock. Disconnecting closes the generator and releases
  the lock after the current patch. Cancelled queued requests do not synthesize.

Every request uses the shared punctuation splitter, including paragraphs below
`segment_chars`. Complete sentences are separate generations; short comma
clauses remain together up to that soft readable-length target. Oversized unpunctuated clauses are rejected, and reaching
the audio generation limit fails the request. Shared spoken-text normalization
retains field values and reads numeric/level notation aloud. Valid Chinese
`<字词|PIN1 YIN1>` annotations become native `{pin1}{yin1}` controls; the
syllable count must match the Chinese word. Invalid annotations retain the word.
Parentheses around factual text, including a single coordinate, become spoken
commas. Factual text cannot be mistaken for a parenthesized delivery control.
Delivery instructions use the separate validated `instructions` field.
An em dash becomes a spoken comma. A backend pronunciation fallback uses native
`{gan1}` for soil-dryness phrases such as `泥土干了` and `耕地全是干的`;
work and combat uses of `干` retain their original text. The utensil expression
`家伙什` (including an existing `儿` suffix) becomes the colloquial `家伙事儿`;
the shorter `家什` uses `{shi5}`. Question words in phrases such as
`那家伙什么时候回来` remain unchanged. English player names such as `Neko`
and identifiers such as `neko_42` pass through unchanged for native bilingual
synthesis. Prefer the player's own spelling or an explicitly agreed nickname
in the client script; do not translate names or insert Chinese soundalikes.
These changes apply only
to synthesized speech; scripts, subtitles and reference transcripts stay intact.
Health reports sentence segmentation and the active pronunciation fallbacks;
synthesis records include segment lengths and the prepared-text fingerprint.
The service makes no mood-classification LLM call. Speed must be 1; the model
controls delivery pace from the reference recording or delivery description.

## Contextual pronunciation

Install `requirements-pronunciation.txt` and prepare the g2pW ONNX model and its
Chinese BERT tokenizer locally. Configure `pronunciation_model_dir` and
`pronunciation_tokenizer_dir`; optional `pronunciation_asset_sha256` maps resource
paths to expected hashes. A configured model must initialize before the speech
listener opens. Requests never download resources.

The resolver reads the complete utterance on CPU and emits sparse hints for
polyphonic words and the grammatical characters `得、还、没、着`. It keeps caller
hints, English names and identifiers intact. Model and dictionary conflicts
remain ordinary Chinese rather than forcing a disputed reading. There is no
per-utterance LLM request. `/health` reports readiness and the disabled external
selector; synthesis records include preparation time and phoneme count.

If generation is slower than playback, set the VTuber extension's
`streamEnabled` to `false` to buffer complete speech pieces. This stops playback
starvation at the cost of waiting for synthesis before speaking. Restore
streaming only after measuring sustained throughput faster than real time.
`stream_prebuffer_ms` optionally accumulates a small amount of actual PCM before
sending the first audio to cover short scheduling stalls. It is limited to two
seconds. It cannot compensate for generation that remains slower than playback.
Health distinguishes model `first_pcm_ms` from socket `first_output_ms`.

Run HTTP and cancellation checks with
`python -m unittest discover -s tests/voxcpm2 -v` from the extension root.

## Specialized VoxCPM2 checkpoints

`model_dir` may select an architecture-compatible fine-tuned checkpoint. Set
`backend` to its deployed identity; health and `X-TTS-Backend` report that value.
Keep the selected model revision and reference hash in deployment configuration.

The optional `acoustic_tag_aliases` maps complete bracketed source tags to the
checkpoint's canonical tags, for example `{"[laughing]":"[笑声]"}`. Canonical
tags are accepted too. Configured tags retain their position through stage
cleanup and pronunciation processing. They are generated with the surrounding
dialogue; no cached audio is inserted. With this map configured, the adapter
preserves only its declared acoustic controls. `/health` exposes the map.

`sentence_segmentation:false` groups adjacent complete sentences up to
`segment_chars` so a short utterance is generated together. Long input keeps
the punctuation splitter and generation limits. The default remains per-sentence
generation. Delivery descriptions still use the separate `instructions` field
and the reference-only prompt; placing them into continuation text can speak them.

`reference_encoder_device:"cpu"` moves reference encoding off the GPU; decoded
speech remains on the configured model device. Prompt features are prepared at
startup. `gpu_memory_fraction` optionally limits this process's PyTorch allocator
to a fraction in `(0,1]`; it does not reserve memory or limit other processes.
Select the model's context length in its local config before loading. Measure
compiled warmup memory and sustained streaming before switching the live route.

The [NVVSpeech Track 2 baseline](https://github.com/NVVSpeech-Challenge/NVVSpeech-Challenge-Track2-Baseline)
uses a VoxCPM2 full fine-tune with 16 nonverbal controls and 48 kHz output. Its
English labels map to canonical Chinese tags. Clone and style fidelity require
listening checks with the intended reference; the architecture alone does not
establish them.

The upstream [usage guide](https://voxcpm.readthedocs.io/en/latest/usage_guide.html)
and [mode clarification](https://github.com/OpenBMB/VoxCPM/issues/210) describe
the Hi-Fi reference and style-control behavior.
