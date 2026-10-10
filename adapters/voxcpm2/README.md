# `voxcpm2_server.py`: official VoxCPM2 Hi-Fi speech service

The service loads the official VoxCPM2 Python model once and caches a combined
reference plus continuation prompt. Each request uses the configured reference
audio and its transcript. The SHA256 in the configuration must match the file.
Hi-Fi follows reference delivery; parenthesized emotion and speed instructions
are unsupported. Stage cues are removed before synthesis.

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
  "queue_timeout_sec": 25,
  "minimum_free_gpu_mib": 7000
}
```

Run `python voxcpm2_server.py --config /path/to/voice.json`. Set
`NUMBA_CACHE_DIR` to a writable directory. With local model files,
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` prevent model downloads.
The HTTP listener becomes available after model loading, prompt caching and
warmup. Use a service supervisor for startup and bounded crash recovery.

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

Run HTTP and cancellation checks with
`python -m unittest discover -s tests/voxcpm2 -v` from the extension root.

The upstream [usage guide](https://voxcpm.readthedocs.io/en/latest/usage_guide.html)
and [mode clarification](https://github.com/OpenBMB/VoxCPM/issues/210) describe
the Hi-Fi reference and style-control behavior.
