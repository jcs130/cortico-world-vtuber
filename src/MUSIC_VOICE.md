# `music_voice.py` / Reference-conditioned singing

The optional `voiceConversion` pipeline belongs to the generic VTuber music
gateway. It has no Minecraft or server-specific dependency.

## Pipeline

1. Review the original song request.
2. Generate a new composition with ACE-Step from text and lyrics.
3. Decode the lossless generated audio and check its duration/activity.
4. Separate vocals with the locally cached `HDEMUCS_HIGH_MUSDB_PLUS` model.
5. Convert **only vocals** with Seed-VC v1 200M 44.1 kHz SVC using an
   authorized reference recording, 30–50 steps, F0 conditioning enabled,
   automatic F0 adjustment disabled, length adjustment 1.0.
6. Mix converted vocals into the original residual backing. Limit peak to
   0.92; preserve the timeline. Allow at most 100 ms of hop rounding at the
   tail; no time stretching. Do not add the source singer back into the mix.
7. Transcribe and review the **final mixed audio**, then verify lyric coverage
   and real word timestamps. Only an accepted final result enters the catalog.

`generationMode: "text-to-music"` requires a template without `LoadAudio`,
`ReferenceTimbreAudio` or the experimental `ACEStep15TimbreWithCodes` wrapper.
This keeps composition generation independent of the voice-conversion stage.
The legacy `reference-timbre` mode remains available without SVC; it is not
evidence of consistent singer identity.

The request API cannot choose local files, Python executables, models or shell
commands. Those are private deployment settings. The gateway launches its
fixed worker script with an argument array and `shell=False`.

## Local dependencies and private configuration

The gateway itself remains a standard-library service with its existing
Whisper environment. SVC runs in a separate CPU Python environment containing
PyTorch/torchaudio, NumPy, soundfile, transformers, librosa, PyYAML and the
dependencies of the pinned upstream Seed-VC checkout. The tested CPU runtime
is torch/torchaudio **2.11.0**, transformers **4.46.3**. CUDA conversion has also
been checked with torch/torchaudio **2.10.0+cu130**, transformers **4.46.3**,
huggingface-hub **0.36.0** and NumPy **2.3.5** in a separate worker environment.
Earlier local torch 2.4
CPU numerical failures must not be masked by replacing NaNs with zero.

Supply an unmodified upstream checkout and cached models; the worker never
downloads them. `assetRoot` layout:

```text
seed-vc/                       # unmodified Git checkout
models/Plachta--Seed-VC/        # SVC checkpoint and YAML
models/nvidia--bigvgan_v2_44khz_128band_512x/
models/lj1995--VoiceConversionWebUI/rmvpe.pt
models/funasr--campplus/campplus_cn_common.bin
models/openai--whisper-small/preprocessor_config.json
```

The multilingual OpenAI Whisper small checkpoint is reused as the speech
encoder through its official strict encoder key mapping. Model learned
parameters are checked for missing/mismatched weights. The adapter does not
modify upstream files.

Private gateway JSON adds:

```json
{
  "generationMode": "text-to-music",
  "voiceConversion": {
    "enabled": true,
    "backend": "seed-vc-v1-200m-svc",
    "pythonFile": "/absolute/isolated/python",
    "assetRoot": "/absolute/private/svc-assets",
    "manifestFile": "/absolute/private/model-manifest.json",
    "manifestSha256": "REPLACE_WITH_ACTUAL_SHA256",
    "upstreamRevision": "REPLACE_WITH_PINNED_GIT_COMMIT",
    "separatorCheckpoint": "/absolute/private/hdemucs_high_trained.pt",
    "separatorSha256": "REPLACE_WITH_ACTUAL_SHA256",
    "whisperCheckpoint": "/absolute/private/small.pt",
    "whisperSha256": "REPLACE_WITH_ACTUAL_SHA256",
    "referenceFile": "/absolute/private/original-reference.wav",
    "referenceSha256": "REPLACE_WITH_ACTUAL_SHA256",
    "referenceAuthorized": true,
    "referenceKind": "original-speech",
    "steps": 30,
    "cpuThreads": 4,
    "device": "cpu",
    "referenceSeconds": 15,
    "semiToneShift": 0,
    "timeoutSec": 3600
  }
}
```

The model manifest is a JSON array of seven rows, each containing
`directory`, `file`, `bytes`, `sha256` and optional upstream repository/revision
information. Its own hash is pinned. All seven cached files, the separator,
Whisper and reference are checked before processing. Seed-VC revision and
tracked-file cleanliness are checked too. Missing/changed assets fail closed.

Reference recordings must contain 1–30 seconds of finite, audible material.
Only leading/trailing silence is trimmed. Short original speech is not
repeated or re-synthesized to pretend there is more identity information.
`referenceKind` explicitly distinguishes original speech, recorded singing
and TTS speech. Zero semitone shift preserves the source melody/register;
cross-register experiments may use an explicitly labelled shift, never a
hidden automatic octave change.

## Async jobs, cancellation and publication

Submission remains immediate; a single background worker processes the queue.
SVC stage messages use the existing `validating` state, so existing World
clients can keep polling without a main-program reload. A low-priority CPU
subprocess uses `cpuThreads` (default 4, range 1–16). `device` defaults to `cpu`;
`cuda` requires a separate compatible runtime selected by `pythonFile` and an available GPU.
Windows CUDA workers use below-normal GPU scheduling priority so live speech
can retain its normal priority.
An unavailable configured device fails the job. Conversion can take several minutes;
it is not a real-time speech stage. HTTP, TTS and gameplay do not await it.

Cancellation/timeout terminates only the owned child. It never interrupts
shared ComfyUI, evicts GPU models or changes game connections. Conversion
failure goes to review; no silent fallback to the original singer. Interrupted
validation remains quarantined on restart, avoiding duplicate conversion or
publication. Known generation prompt IDs still resume without resubmission.

Each job keeps progress, worker logs, float source/converted vocal stems,
backing, a synchronized playback vocal stem and `voice-provenance.json`.
Published entries include `vocalFile` for lip sync, `voiceConditioned:true`,
`singingVoiceVerified:false` and `voiceProvenanceFile`. The waveform and
provenance are staged before the atomic catalog update.

`voiceConditioned` means the conversion ran with the recorded reference.
It does **not** mean a listener accepted the target singer identity.
`singingVoiceVerified` cannot be set true by this generator. Acceptance needs
longer dry-vocal and mixed listening across songs/registers; any speaker
similarity metric is supporting evidence. CAMPPlus is part of conditioning,
so its similarity score is not an independent acceptance test.

## Production operation

Keep the song gateway independent of the World, speech adapter and game
connection. Windows deployments can run `scripts/music-gateway-watch.ps1`
with `-ConfigPath` and `-PythonExe`. The watcher adopts an already healthy
gateway, restarts it when its port is closed, and backs off after failed
launches. An occupied but unhealthy port is logged and left running; it
never kills an existing process. A named mutex prevents duplicate watchers.
Use `-Once` for a bounded health/adoption check. A deployment may register
the watcher as a hidden, user-level logon task.

A listener's choice of reference/pipeline is a deployment preference. Store
its timestamp, the exact accepted sample hash and the reference hash with
the private deployment, not in the extension's defaults. An explicitly
accepted audition can be curated into the deployment's library with a
matching vocal stem and lyric timestamps; retain the raw transcription and
any lyric uncertainty. Publish complete caption lines supported by the final
audio; omit unconfirmed lines. Keep uncertainty in the review evidence rather
than inserting ellipsis placeholders or joining verified fragments into a line.
The composition draft alone does not establish what was sung. Archive older
methods' catalog entries if the live
library should use only the selected voice, preserving all original assets.
This is separate from approving a generated job: the gateway still applies
its full content/audio/lyric checks to each new song, and does not turn an
unreviewed job into a playable one because another song sounded good.

Submit new compositions asynchronously. The presenter can keep playing or
talking while they process, then use the real `ready` receipt and catalog
ID to queue an intro, the song and an outro in one play operation. Conversion
can take several minutes on CPU; it must not become a synchronous wait or
claim immediate completion.

## Focused verification

```text
python tests/vtuber/music-generation-gateway-test.py
python tests/vtuber/music-voice-test.py
```

Real auditions should preserve identical source, seed, steps, backing and
pitch when comparing references, expose both dry vocals and mixes, and keep
the source singer as a baseline. Do not relax final transcript/lyric checks
or automatically play diagnostic samples to pass a test.

## Method sources

- [Seed-VC official SVC instructions](https://github.com/Plachtaa/seed-vc)
- [Seed-VC paper: reference conditioning and F0](https://arxiv.org/html/2411.09943v1)
- [Seed-VC independent evaluation encoders](https://github.com/Plachtaa/seed-vc/blob/main/eval.py)
- [ACE-Step reference audio semantics](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/Tutorial.md)

Seed-VC's upstream repository is archived; this is a pinned, locally tested
backend rather than a claim that it is the newest available method.
