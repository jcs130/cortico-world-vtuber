import { describe, expect, it } from 'vitest';
import { SpeechPlanningClock } from '../../src/speech-planning.ts';
import type { SpeechAvailability } from '../../src/commentary.ts';

const sample = (patch: Partial<SpeechAvailability> = {}): SpeechAvailability => ({
  observedAtMs: 100_000, idleSinceMs: null, busy: true, playing: true, musicPlaying: false,
  ttsReady: true, backlogMs: 9000, queueRevision: 1, ...patch,
});
describe('speech preparation clock', () => {
  it('notifies while speech is still playing, once per admitted queue revision', () => {
    const clock = new SpeechPlanningClock();
    expect(clock.observe(sample({ backlogMs: 20_000 }), true, 12, 100_000)).toBeNull();
    expect(clock.observe(sample(), true, 12, 100_000)).toMatchObject({ remainingMs: 9000, queueRevision: 1 });
    expect(clock.observe(sample({ backlogMs: 1000 }), true, 12, 100_000)).toBeNull();
    expect(clock.observe(sample({ queueRevision: 2 }), true, 12, 100_000)).toMatchObject({ queueRevision: 2 });
    expect(clock.observe(sample(), true, 12, 100_000)).toBeNull();
    clock.reset();
    expect(clock.observe(sample(), true, 12, 100_000)).not.toBeNull();
  });
  it.each([
    { playing: false }, { busy: false }, { musicPlaying: true }, { ttsReady: false },
    { observedAtMs: 94_000 }, { observedAtMs: 102_000 }, { backlogMs: 0 }, { backlogMs: NaN },
    { backlogMs: undefined }, { queueRevision: undefined }, { queueRevision: 0 }, { queueRevision: 1.5 },
  ])('does not wake from an unverified or ineligible playback sample %j', patch => {
    expect(new SpeechPlanningClock().observe(sample(patch), true, 12, 100_000)).toBeNull();
  });
  it('honors disabled lookahead, offline consumers and older engines without playback clocks', () => {
    const clock = new SpeechPlanningClock();
    expect(clock.observe(sample(), false, 12, 100_000)).toBeNull();
    expect(clock.observe(sample(), true, 0, 100_000)).toBeNull();
    expect(clock.observe(sample(), true, NaN, 100_000)).toBeNull();
    expect(clock.observe(null, true, 12, 100_000)).toBeNull();
  });
});
