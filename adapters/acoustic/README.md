<!-- Owner: acoustic_gateway.py -->
# Cached acoustic cues

This optional speech gateway replaces explicit cues such as `[laughing]` with
preselected PCM assets. Text before and after a cue goes to the configured speech
backend. Requests without catalog cues pass through. The gateway loads assets into
memory at startup and does not load an audio model.

This is a fallback for a backend whose live nonverbal synthesis is unsuitable for
the performance. Audio quality and permission to use each recording are properties
of the operator's catalog. A reference hash verifies provenance, not perceived
speaker identity or naturalness. Catalog audio is kept in the deployment, outside
the extension package.

## Run

```sh
python -m pip install -r adapters/acoustic/requirements.txt
python adapters/acoustic/acoustic_gateway.py --config /absolute/path/gateway.json
```

`gateway.json`:

```json
{
  "host": "127.0.0.1",
  "port": 8017,
  "enabled": true,
  "upstream_url": "http://127.0.0.1:8014",
  "manifest_file": "/absolute/path/catalog/manifest.json",
  "timeout_sec": 60
}
```

Set `worlds.vtuber.ttsUrl` to the gateway address and restart the VTuber World to
apply the endpoint. Keep the original backend running. Reverting the address and
restarting that World removes the gateway from the playback path.

Set `enabled:false` and restart only the gateway to withdraw the catalog. Ordinary
speech still passes through; catalog cue requests return HTTP 409 with
`code:acoustic_catalog_disabled` and do not fall back to native synthesis. Health
reports `enabled:false` and `fallback_reason:catalog-disabled`.

## Catalog contract

```json
{
  "version": 1,
  "voice": "deployment-voice",
  "sample_rate": 48000,
  "reference_sha256": "<SHA256 of the complete reference WAV>",
  "reference_text_sha256": "<SHA256 of the exact UTF-8 reference transcript>",
  "assets": [
    {
      "id": "short-laugh-1",
      "cue": "laughing",
      "file": "short-laugh-1.wav",
      "sha256": "<SHA256 of this complete asset WAV>"
    }
  ]
}
```

Assets must be mono PCM16 WAV, use the catalog sample rate, and contain at most
five seconds of audio. Paths remain inside the catalog directory. Startup rejects
missing, corrupt or incompatible assets. Entries for the same cue rotate in
catalog order. Trim silence and fade the edges while preparing the recordings.

The upstream `/health` must identify `voice` and `reference_sha256`. A changed
upstream reference disables cached cues and forwards the original request, with
`X-TTS-Acoustic-Fallback: upstream-reference-mismatch`. Health exposes that reason.
Cached requests validate the requested voice, optional reference and transcript,
speed, seed and style capability. Current cached composition supports speed 1.

## Speech protocol and observation

Both `/v1/audio/speech` and `/v1/audio/speech/stream` accept the existing JSON
request. The upstream stream must return one 44-byte mono PCM16 WAV header at the
catalog sample rate. Composition sends one header for the entire response.
The nonstream response contains finite RIFF/data lengths. Other paths pass through.

At a speech/asset join, quiet leading or trailing PCM is reduced to 60 ms. Pauses
inside a speech segment are preserved. The quiet buffer holds at most 800 ms;
longer silence remains observable to the client's existing silence guard.

Closing the client response cancels the active upstream request and skips the
remaining text and cues. A later upstream failure terminates the stream; it does
not finish an incomplete composition as successful audio.

`/health.acoustic_assets` reports catalog identity, loaded cues, active calls,
completion/failure/cancellation counts and the last cached composition. Each
composition logs input and PCM hashes, chosen and played assets, backend request
ids, first-audio time and duration. It does not log speech text or credentials.
Pure cached cues do not increment the backend's synthesis count.

## Verify

```sh
cd adapters/acoustic
python -m unittest -v test_acoustic_gateway.py
```

Tests use real local HTTP connections to verify PCM order, arbitrary chunk
boundaries, finite WAV output, reference validation, fallback and cancellation.
Before live use, preview the actual catalog through `TtsClient`, measure first
audio and check the voice and joins by listening. Software checks do not establish
listener approval.
