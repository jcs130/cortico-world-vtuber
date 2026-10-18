Owner: `src/proxy.ts` admits performance requests; `src/world.ts` prepares speech; `src/tts.ts` owns transport. The host Persona owns identity and recent utterances.

# Speech routes and repetition

| Entry | Text and playback path | Notes |
| --- | --- | --- |
| Main `vtuber_act`, including output tap | ScriptParser → Agent renderer → Performer → World synthesis → TtsClient | Tap and final tool execution share the call ID. Admission is not playback completion. |
| Optional commentary | Host cognition draft → same `vtuber_act` engine and TtsClient | No private model credentials, personality or autonomous model loop in this World. |
| Song introduction / ending | Same Performer and Agent renderer | Song recordings themselves are music assets, not TTS. |
| Optional waiting speech | Agent renderer → TtsClient → prepared Performer audio | Disabled by default; explicit lines only. Cache key includes speech preferences and profile. Voice changes invalidate prepared assets. |
| Performance-console test | Same Performer / ScriptParser route | Explicit operator action. |
| TTS-panel audition | Agent renderer → same TtsClient → prepared playback | Explicit operator action; a busy performance queue refuses playback. |
| Alignment-panel synthesis | Agent renderer → same TtsClient → returned WAV | Creates test audio; no automatic broadcast. |
| Alignment of existing audio | Supplied WAV and transcript → aligner | No TTS; do not translate an existing recording's transcript. |

All synthesis uses one World-owned TtsClient with the configured protocol and selected speech preferences. For `openai-chunked`, full and streaming interfaces use the same OpenAI-compatible speech POST, model, voice and session headers. There is no legacy stream probe or fallback to a local voice on this protocol. Legacy transports retain their explicit compatibility behavior. No endpoint or vocabulary is scanned for automatically.

Agent-local vocabulary, when explicitly configured, is applied once before synthesis. Performer scripts pass through ScriptParser; non-script entries use the same renderer in World. Subtitles and alignment use the rendered text. The service receives ordinary final speech text, without a game-specific dictionary. Per-request diagnostic data keeps call/session/request IDs where supplied by the endpoint.

## Meaning repeats need shared context

Identical call IDs and identical strings can be deduplicated deterministically. Two different sentences can convey the same meaning; a string threshold would also risk rejecting a correction, negation or a newly completed result. The World gives host cognition bounded recent accepted speech and source-attributed evidence, and explicitly permits `skip`. A new cursor is not proof of new meaning.

The host should consume its internal `vtuber.speech.submitted` events from **every** producer, merge them with transcript calls by `callId`, retain the original timestamps, and apply `vtuber.act.outcome` playback corrections. Accepted speech is not proof of delivery to a viewer. This shared record should enter the next main request and commentary draft. Speech candidates should use the same host-owned character and language guidance as main narration; a generic observation-only prompt produces a different speaking style even with the same voice.

Keep the fixed identity/instructions at the request prefix and dynamic utterances/evidence at the tail. Do not copy the entire main session into short commentary or add an unbounded classification loop. A main model request already in flight cannot read a later commentary admission; shared history reduces repeats but does not guarantee semantic uniqueness. Live observation remains a separate acceptance step from offline contract tests.

## Diagnostic procedure

1. Compare admitted scripts, producer role, timestamps and cited evidence before blaming synthesis.
2. Compare actual HTTP model/voice/session/request trace and reference identity; ignore inactive legacy configuration.
3. Inspect interruption outcomes and the host's next-request recent-speech projection.
4. Distinguish multiple audio outputs or browser monitoring of one recording from independently generated paraphrases.
5. Test with loopback fixtures and `audioDevice: none`; live reload and playback need the deployment's authorization.

This extension does not choose the host's personality, rewrite goals, or treat a single RPC as the end of a decision turn.
