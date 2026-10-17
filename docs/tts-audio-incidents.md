# Retaining a TTS sound incident

HTTP success and ASR output do not establish sound quality. The OpenAI chunked transport can retain the exact PCM received from the server, including a partial response on interruption. This is reception evidence, not a microphone or physical output recording.

Recording is off by default. For a bounded incident window, create `tts-audio-capture.json` under the existing private VTuber `diagDir`:

```json
{"expiresAt":"2026-10-17T06:00:00.000Z"}
```

The deadline must be in the future and at most one hour away. Removing the marker or reaching its deadline stops recording. No credentials, request headers or endpoint addresses are saved. Do not expose this directory as a public web resource.

The `tts-audio` subdirectory contains paired WAV and JSON files. Metadata contains the text, original request/session IDs, selected voice/reference hash, PCM hash, and received/aborted/error phase. At most 32 utterances and 64 MiB of WAV audio are retained; entries older than one hour are pruned on the next capture. At most two disk writes can be pending. Disk failures and a saturated diagnostic queue never block playback or trigger another inference request.

Compare the beginning of the retained WAV with the actual audible incident. If the WAV already has the abnormal onset, investigate synthesis, its spoken input and reference continuation. If the WAV is clear but the physical playback is abnormal, investigate playback timing, sample-rate conversion and overlapping output. Do not trim an arbitrary fixed prefix, change voices, or label an HTTP 200 response as a repair.
