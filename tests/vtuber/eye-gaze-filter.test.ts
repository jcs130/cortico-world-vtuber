import { describe, expect, it } from 'vitest';
import { EyeGazeFilter } from '../../src/eye-gaze-filter.ts';
import type { IRFrame } from '../../src/mixer.ts';
const frame = (x: number): IRFrame => ({ EyeLeftX: { value: x, mode: 'set' }, EyeRightX: { value: x, mode: 'set' } });

describe('gaze stability without smearing intentional gestures', () => {
  it('reduces 60Hz small jitter while preserving a rapid gaze shift', () => {
    const filter = new EyeGazeFilter();
    let rawEnergy = 0, filteredEnergy = 0;
    for (let i = 0; i < 120; i++) {
      const raw = i % 2 ? .01 : -.01;
      const value = filter.frame(frame(raw), i * 1000 / 60, 55).EyeRightX.value;
      if (i > 30) { rawEnergy += raw ** 2; filteredEnergy += value ** 2; }
    }
    expect(Math.sqrt(filteredEnergy / rawEnergy)).toBeLessThan(.4);
    const changed = filter.frame(frame(.8), 2000, 55).EyeRightX.value;
    expect(changed).toBeGreaterThan(.69);
  });

  it('uses elapsed time, resets on tracking gaps, and leaves blink, wink and lipsync byte-for-byte intact', () => {
    const a = new EyeGazeFilter(), b = new EyeGazeFilter();
    const sample = (filter: EyeGazeFilter, hz: number): number => {
      for (let i = 0; i <= hz; i++) filter.frame(frame(.2 * i / hz), i * 1000 / hz, 55);
      return filter.frame(frame(.2), 1000 + 1000 / hz, 55).EyeRightX.value;
    };
    expect(Math.abs(sample(a, 30) - sample(b, 60))).toBeLessThan(.006);
    const input: IRFrame = { ...frame(-.9), EyeOpenLeft: { value: -1, mode: 'add' }, EyeOpenRight: { value: 0, mode: 'add' }, MouthOpen: { value: .7, mode: 'set' } };
    const out = a.frame(input, 2000, 55);
    expect(out.EyeRightX.value).toBe(-.9);
    for (const id of ['EyeOpenLeft', 'EyeOpenRight', 'MouthOpen']) expect(out[id]).toBe(input[id]);
  });
});
