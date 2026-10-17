# Changelog

## 0.1.1-dev.20261017.3

- Add an optional read-only host status adapter to distinguish normal foreground waiting from operator pause. Unknown or stale status denies new drafts.
- Keep a single status read in flight, with a timeout and lifecycle protection against late responses. The adapter has no default endpoint or credentials.
- Strip performance markers from commentary text and recent speech instead of either reading them aloud or discarding an otherwise usable line.
- Clarify that utterances and text parameters do not verify the events they describe.

## 0.1.1-dev.20261017.2

- Add opt-in, bounded commentary drafting through host cognition while foreground activity continues.
- Drop stale drafts when foreground speech, new conversation, cited facts, pause or engine availability changes; unrelated telemetry does not starve drafting.
- Validate idle admission inside the engine process and distinguish submission, synthesis, playback and audience reception.
- Expose current silence duration and recent submitted speech in request facts; declare the submission event in the offline self-description.
- Keep provider selection and task-context support with the host; disabled by default, with no body tools or extra credentials.

## 0.1.1-dev.20261017.1

- Adopt the Cortico API 6 WebUI contract.
- Export market self-description from runtime tool declarations, with lifecycle and playback events.
