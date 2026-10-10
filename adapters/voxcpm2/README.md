# `voxcpm2_server.py`: official VoxCPM2 Hi-Fi speech service

The service loads the official VoxCPM2 Python model once and caches a combined
reference plus continuation prompt. Each request uses the configured reference
audio and its transcript. The SHA256 in the configuration must match the file.
Hi-Fi follows reference delivery; parenthesized emotion and speed instructions
are unsupported. Stage cues are removed before synthesis. Native square-bracket
vocal tags such as `[laughing]`, `[sigh]`, `[Uhm]` and `[Shh]` reach the model
in their original positions, including tag-only utterances. Their audible
delivery depends on the reference voice and surrounding text; `[laughing]`
does not select a separate chuckle intensity.

## Optional text delivery control

Set `enable_style_instructions: true` only after comparing audio on the intended
reference voice. This adds a cached reference-only prompt using the **same**
configured WAV and the same model weights. Requests without `instructions` retain
Hi-Fi `ref_continuation`; requests with a concise `instructions` string use the
official controllable `reference` mode and prepend that description inside the
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

Short utterances retain their complete prosody. Longer text uses the shared
punctuation splitter. Oversized unpunctuated clauses are rejected, and reaching
the audio generation limit fails the request. Shared spoken-text normalization
retains field values and reads numeric/level notation aloud. IndexTTS phonetic
annotations become readable text; pronunciation is then handled by VoxCPM2.
The service makes no mood-classification LLM call. Speed must be 1; delivery
pace comes from the reference recording.

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

The upstream [usage guide](https://voxcpm.readthedocs.io/en/latest/usage_guide.html)
and [mode clarification](https://github.com/OpenBMB/VoxCPM/issues/210) describe
the Hi-Fi reference and style-control behavior.
