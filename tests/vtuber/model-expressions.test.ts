import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ModelExpressions } from '../../src/model-expressions.ts';
import { DEFAULT_PROFILE } from '../../src/models/index.ts';

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());
const flush = async (): Promise<void> => { await vi.advanceTimersByTimeAsync(0); };
function setup() {
  const calls: Array<[string, boolean]> = [];
  let changed: (() => void) | undefined;
  const api = { connected: true, setExpression: vi.fn(async (f: string, on: boolean) => { calls.push([f, on]); changed?.(); }) };
  const profile = { ...DEFAULT_PROFILE, id: 'Test', emotionMap: { happy: 'Smile.exp3.json', 开心: 'Smile.exp3.json', angry: 'Angry.exp3.json' } };
  const driver = new ModelExpressions(api, () => profile);
  return { calls, api, profile, driver, duringRequest: (fn: () => void) => { changed = fn; } };
}

describe('model expression ownership', () => {
  it('starts at audio onset, releases at audio end, never interprets factual parentheses', async () => {
    const { driver, calls } = setup();
    driver.speech('库存(11/20)，今天到了3个人。');
    const end = driver.speech('(开心@0.8)欢迎回来！', Date.now() + 250);
    expect(calls).toEqual([]);
    await vi.advanceTimersByTimeAsync(249); expect(calls).toEqual([]);
    await vi.advanceTimersByTimeAsync(1); expect(calls).toEqual([['Smile.exp3.json', true]]);
    end(); await flush(); expect(calls.at(-1)).toEqual(['Smile.exp3.json', false]);
  });

  it('explicit state overrides voice, and old speech completion cannot clear a new voice lease', async () => {
    const { driver, calls } = setup();
    const oldEnd = driver.speech('(happy)hello'); await flush();
    driver.emotion('angry'); await flush();
    expect(calls.slice(-2)).toEqual([['Smile.exp3.json', false], ['Angry.exp3.json', true]]);
    const newEnd = driver.speech('(happy)again');
    oldEnd(); driver.emotion(null); await flush();
    expect(calls.at(-1)).toEqual(['Smile.exp3.json', true]);
    newEnd(); await flush(); expect(calls.at(-1)).toEqual(['Smile.exp3.json', false]);
  });

  it('retriggering FX extends its lease, and FX ending cannot clear a face using the same file', async () => {
    const { driver, calls } = setup();
    driver.pulse('Sweat.exp3.json', 900); await vi.advanceTimersByTimeAsync(700);
    driver.pulse('Sweat.exp3.json', 900); await vi.advanceTimersByTimeAsync(300);
    expect(calls).toEqual([['Sweat.exp3.json', true]]);
    await vi.advanceTimersByTimeAsync(600); expect(calls.at(-1)).toEqual(['Sweat.exp3.json', false]);
    const end = driver.speech('(happy)hello'); await flush();
    driver.pulse('Smile.exp3.json', 100); await vi.advanceTimersByTimeAsync(100);
    expect(calls.at(-1)).toEqual(['Smile.exp3.json', true]);
    end(); await flush(); expect(calls.at(-1)).toEqual(['Smile.exp3.json', false]);
  });

  it('handles a slow activation followed by cancellation without leaving an orphan expression', async () => {
    const { driver, calls, api } = setup();
    let resolve!: () => void;
    api.setExpression.mockImplementationOnce((file, active) => {
      calls.push([file, active]); return new Promise<void>((r) => { resolve = r; });
    });
    const end = driver.speech('(happy)hello'); end();
    expect(calls).toEqual([['Smile.exp3.json', true]]);
    resolve(); await flush(); expect(calls.at(-1)).toEqual(['Smile.exp3.json', false]);
  });

  it('model change discards queued old effects and timers; stop releases owned files only', async () => {
    const { driver, calls } = setup();
    driver.pulse('Old.exp3.json', 500); await flush();
    driver.speech('(happy)not yet', Date.now() + 100);
    driver.modelChanged(); await vi.advanceTimersByTimeAsync(600);
    expect(calls).toEqual([['Old.exp3.json', true]]);
    driver.pulse('New.exp3.json', 500); await flush();
    driver.stop(); await flush();
    expect(calls.slice(-2)).toEqual([['New.exp3.json', true], ['New.exp3.json', false]]);
    await vi.advanceTimersByTimeAsync(600); expect(calls).toHaveLength(3);
  });

  it('cancel before scheduled onset, zero intensity, and unknown moods never activate anything', async () => {
    const { driver, calls } = setup();
    driver.speech('(happy)hello', Date.now() + 500)();
    driver.speech('(happy@0.0)hello'); driver.speech('(unknown@0.8)hello');
    await vi.advanceTimersByTimeAsync(600); expect(calls).toEqual([]);
  });
});
