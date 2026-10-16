# Service-owned voice catalog

One speech model can serve several fixed reference voices. Reference WAVs and
their matching transcripts stay on the TTS host. Clients select an ID in the
OpenAI-compatible `voice` field; they do not upload reference audio per utterance.

## Adapter configuration

Keep the existing default `voice`, `reference_file`, `reference_text`, and
`reference_sha256` fields. Add an absolute `voice_catalog_file` path and optionally
`max_cached_voices` (default 16, range 2–64). Existing single-voice configurations
continue to work without a catalog.

The additional catalog has this structure; replace the example paths and hash
with the actual local reference and its SHA256:

```json
{
  "schema": "tts-voice-catalog-v1",
  "voices": [{
    "id": "avatar-bright",
    "label": "Bright adventurer",
    "description": "An original cheerful voice",
    "reference_file": "references/bright.wav",
    "reference_text": "The exact words spoken in the reference audio.",
    "reference_sha256": "REPLACE_WITH_64_LOWERCASE_HEX_CHARACTERS",
    "reference_seconds": 8.5
  }]
}
```

Relative reference paths resolve against the catalog's directory. IDs must be
unique, including the existing default voice. Up to 64 voices are accepted.
All references are checked against their pinned hashes before the model starts.
Keep references on local storage; no reference is downloaded during a request.
Adding or replacing an asset requires a controlled reload of the speech service.
Choosing among already configured voices does not reload the service or model.

`GET /voices` and `GET /v1/audio/voices` return:

```json
{
  "schema": "tts-voice-catalog-v1",
  "default_voice": "avatar-default",
  "voices": ["avatar-default", "avatar-bright"],
  "catalog": [{
    "id": "avatar-bright",
    "label": "Bright adventurer",
    "description": "An original cheerful voice",
    "reference_sha256": "...",
    "reference_seconds": 8.5
  }]
}
```

The public catalog excludes local paths and deployment secrets. The default is
also included in the actual `catalog` array. Unknown voice IDs return HTTP 400;
they never silently fall back to the default reference. `default`, an empty ID,
or an omitted ID retains the explicit legacy default behavior.

All reference-cache creation and generation run on the same inference worker.
Additional reference features are cached on first use. The LRU cache is bounded;
the default stays pinned. Each request captures its voice before queuing and
retains it across all segments. Disconnecting while queued prevents generation.
Request records and streaming headers expose `voice` / `X-TTS-Voice` and the
selected reference hash / `X-TTS-Reference-SHA256`.

## Gateway and VTuber client

Use one stable model route for the speech engine. A gateway must forward `voice`
unchanged, expose authenticated `GET /v1/audio/voices` on the configured origin,
and forward the voice and reference-hash response headers. A compatibility route
for a named fixed voice should preserve that voice's meaning.

For `openai-chunked`, the VTuber console loads the service catalog through that
same configured origin and credentials. The catalog is cached for 30 seconds;
no alternate host or port is discovered. In **语音试听与声线 → 服务端音色库**,
select a voice and save. The selection is persisted using the existing hot
`ttsSpeech.enabled` / `ttsSpeech.voice` configuration. It affects the next HTTP
request; an active or already synthesized utterance keeps its original voice.
The model route, session ID and prebuffer remain stable.

Remote mode sends only the selected voice ID and standard speech parameters.
Local reference uploads, per-utterance style controls and IndexTTS emotion/speed
preferences are not forwarded. Keep the configured remote speed compatible with
the backend (the Hi-Fi adapter requires 1). A response explicitly claiming a
different voice is rejected before playback. The existing WAV prebuffer,
cancellation and queue-clearing behavior remain in use.

## Offline verification

```text
cd adapters/voxcpm2
python -m unittest test_voice_catalog test_synthesis_metrics

pnpm exec vitest run tests/vtuber/tts-transport.test.ts tests/vtuber/tts.test.ts tests/web/vtuber-tts-ui.test.ts
pnpm typecheck
pnpm build
```

The Python HTTP fixture uses one fake inference worker and three clients to check
queue cancellation, per-request references, truthful headers, bounded caching,
unknown-ID rejection and refusal after stopping. Client tests check authenticated
catalog loading, request snapshots during a live selection change, no fallback
on a wrong voice, and a persistent console selection without a service restart.
These tests do not establish production audio quality or deployment success;
verify the actual gateway route, selected hash, playback and rollback separately.
