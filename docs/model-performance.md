# Per-model expressions, motion and eye stability

Optional fields in `cortico.profile.json` keep older profiles compatible:

```json
{
  "emotionMap": {
    "smile": "Smile.exp3.json",
    "happy": "Smile.exp3.json",
    "angry": ["Angry.exp3.json", "AngerIcon.exp3.json"]
  },
  "motionMap": { "wave": "the-models-TriggerAnimation-hotkey-ID" },
  "gazeSmoothingMs": 55
}
```

- `emotionMap` keys are semantic emotion clip IDs or exact voice mood labels, including Chinese aliases when used by the TTS adapter. Values are filenames, filename arrays, or null. They are model assets, never assumed global names. Arrays compose a facial style with independent icon files.
- Explicit emotion state wins over voice mood. A voice mood lease begins at the audio sink's actual start time, ends on completion/interruption, and is never activated merely by synthesis or queue acceptance. Only recognized `(mood@intensity)` metadata is read; no sentiment guessing from dialogue. The first mood in a speech piece is the automatic visual cue; use existing timed `<emotion>` anchors for within-piece changes. VTS expressions are presets: intensity still controls voice/semantic curves, not fractional expression opacity. Zero voice intensity disables automatic visuals.
- Only one native facial style is selected at a time. FX leases are independent; retrigger extends the same file's lease. A timer or an old utterance cannot deactivate a file still owned by another effect/emotion. Reset and shutdown release only files owned by this backend. Model changes discard stale timers and queued controls. Reconnect does not replay old speech or native motions.
- Style files must not write eyelids, gaze, lipsync or physics-owned parameters. Separate facial and FX files; do not use a full preset that resets every icon as an independent FX.
- `motionMap` dispatches once per gesture through VTS hotkeys. The loaded model and `TriggerAnimation` type are checked before requesting execution. No per-frame hotkey loop, model switch, or animation guessed by filename. The shared pack supplies a small head/body fallback for models without the native limb motion.
- `gazeSmoothingMs` defaults to zero (off), maximum 200ms. It fuses only almost identical binocular targets and uses elapsed-time filtering for small eye jitter, with faster response for deliberate saccades. It never smooths mouth/eyelid parameters; existing authored blink curves and intentional winks retain their control. No camera, microphone or landmark detector is acquired.

## References and adoption boundary

- [Cubism official sample](https://github.com/Live2D/CubismWebSamples/blob/b1de66b0b1f1cb881d95fb6158622aeb6a2827bd/Samples/TypeScript/Demo/src/lappmodel.ts): model evaluation order, keeping motion/expression inputs before physics. Native model inspection must not reset physics after evaluation.
- [Kalidokit eye solver](https://github.com/yeemachine/kalidokit/blob/6437947da2f889fdf241b84745b53082f9e881a8/src/FaceSolver/calcEyes.ts): binocular coordination and keeping intentional winks distinct from ordinary blinking. The autonomous avatar does not have noisy facial landmarks, so this implementation uses its own adaptive temporal gaze filter and retains existing blink curves; it does not install a camera solver or apply human landmark ratios to artwork.
- [Open-LLM-VTuber model emotion map](https://github.com/Open-LLM-VTuber/Open-LLM-VTuber/blob/992309c0aa19845960228f880013d4685fde93b5/src/open_llm_vtuber/live2d_model.py): per-model emotion mapping, separating control labels from spoken text. This backend additionally performs audio-onset dispatch and scoped lifecycle cleanup.
- [VTube Studio API](https://github.com/DenchiSoft/VTubeStudio#requesting-execution-of-hotkeys): hotkey execution and expression control. Successful requests alone do not prove a drawable moved; use actual model output readback/render checks.

Tests cover actual non-stream/stream audio dispatch, queued vs playing separation, voice/state priority, overlapping leases, slow activation cancellation, model swaps, native-hotkey contract and gaze jitter/latency. Native Core acceptance and VTS desktop acceptance are separate evidence.
