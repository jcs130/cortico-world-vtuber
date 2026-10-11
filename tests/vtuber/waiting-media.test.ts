import { afterEach, describe, expect, it, vi } from 'vitest';
import type { RunPhase } from 'cortico/core/types.ts';
import { WaitingMedia, WAITING_MEDIA_DEFAULTS, type WaitingMediaAsset, type WaitingPlaybackStatus } from '../../src/waiting-media.ts';

const cleanup: Array<() => void> = [];
afterEach(() => { for (const stop of cleanup.splice(0)) stop(); vi.useRealTimers(); });

function fixture() {
  vi.useFakeTimers({ now: new Date('2026-01-01T00:00:00Z') });
  const cfg = { ...WAITING_MEDIA_DEFAULTS, enabled: true, endpoint: 'http://fixture/decision',
    afterMs: 5000, intervalMs: 5000, cooldownMs: 10_000, maxPerRequest: 2 };
  let voice = 'voice-a';
  const availability = { live: true, idle: true, quietSinceMs: Date.now() as number | null };
  const events: Record<string, unknown>[] = [];
  const requests: any[] = [];
  const prepared: string[] = [];
  const cancelled: string[] = [];
  const playing = new Map<string, (status: WaitingPlaybackStatus) => void>();
  let answer: string | null = 'clip-a';
  let probability = .97;
  let hold = false;
  let release!: () => void;
  const pending: Array<() => void> = [];
  const assets: WaitingMediaAsset[] = ['clip-a', 'clip-b', 'clip-c'].map(id => ({
    id, kind: 'speech', title: `A prepared greeting ${id}`, durationMs: 1000,
    play: report => {
      availability.idle = false; availability.quietSinceMs = null;
      report('queued'); report('playing'); playing.set(id, report);
      return () => { cancelled.push(id); end(id, 'stopped'); };
    },
  }));
  function end(id: string, status: WaitingPlaybackStatus = 'ended') {
    const report = playing.get(id);
    playing.delete(id); availability.idle = true; availability.quietSinceMs = Date.now();
    report?.(status);
  }
  const media = new WaitingMedia({ config: () => cfg, assetKey: () => voice,
    prepare: async () => { prepared.push(voice); return assets; },
    availability: () => availability, report: row => events.push(row),
    fetch: (async (_url, init) => {
      const body = JSON.parse(String(init?.body)); requests.push(body);
      const selected = answer ?? 'quiet';
      if (hold) await new Promise<void>(resolve => { release = resolve; pending.push(resolve); });
      const choices = Object.keys(body.questions.media.criteria);
      return new Response(JSON.stringify({ answers: { media: { choice: selected, confidence: probability,
        probabilities: Object.fromEntries(choices.map(key => [key, key === selected ? probability : (1 - probability) / (choices.length - 1)])) } } }));
    }) as typeof fetch,
  });
  media.start(); cleanup.push(() => { media.stop(); for (const done of pending) done(); });
  const phase = (state: RunPhase['state'] = 'model', running: string[] = []) => {
    media.onPhase({ state, running, round: 1, enteredAt: new Date().toISOString() });
  };
  return { media, cfg, assets, phase, events, requests, prepared, cancelled, playing, end, availability,
    setVoice: (value: string) => { voice = value; }, answer: (value: string | null, p = .97) => { answer = value; probability = p; },
    hold: () => { hold = true; }, release: () => release?.() };
}

describe('optional media during a pending model request', () => {
  it('uses only prepared media after both waiting thresholds and yields when the request completes', async () => {
    const f = fixture(); f.phase();
    await vi.advanceTimersByTimeAsync(f.cfg.afterMs - 1);
    expect(f.requests).toHaveLength(0); expect(f.playing.size).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    expect([...f.playing.keys()]).toEqual(['clip-a']);
    expect(f.events.map(row => row.status)).toContain('playing');
    expect(f.prepared).toEqual(['voice-a']);
    expect(JSON.stringify(f.requests[0])).not.toContain('wav');
    f.phase('tools');
    expect(f.cancelled).toEqual(['clip-a']); expect(f.playing.size).toBe(0);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(f.requests).toHaveLength(1);
  });

  it.each(['not-live', 'occupied', 'disabled', 'running-tool'])('does not play for %s', async mode => {
    const f = fixture();
    if (mode === 'not-live') f.availability.live = false;
    if (mode === 'occupied') f.availability.idle = false;
    if (mode === 'disabled') f.cfg.enabled = false;
    f.phase('model', mode === 'running-tool' ? ['tool'] : []);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(f.playing.size).toBe(0); expect(f.requests).toHaveLength(0);
  });

  it('expires a classifier result when primary speech starts before its reply', async () => {
    const f = fixture(); f.hold(); f.phase();
    await vi.advanceTimersByTimeAsync(5000); expect(f.requests).toHaveLength(1);
    f.media.yield(); f.availability.idle = false; f.release();
    await vi.advanceTimersByTimeAsync(100);
    expect(f.playing.size).toBe(0);
    f.availability.idle = true;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(f.requests).toHaveLength(1);
  });

  it('invalidates old voice assets and choices, then prepares the current voice once', async () => {
    const f = fixture(); f.hold(); f.phase();
    await vi.advanceTimersByTimeAsync(5000);
    f.setVoice('voice-b'); await vi.advanceTimersByTimeAsync(1000); f.release();
    await vi.advanceTimersByTimeAsync(100);
    expect(f.playing.size).toBe(0); expect(f.prepared).toEqual(['voice-a', 'voice-b']);
  });

  it('stays quiet on timeout or low choice probability and bounds attempts', async () => {
    for (const timeout of [false, true]) {
      const f = fixture(); f.answer('clip-a', .4); if (timeout) f.hold(); f.phase();
      await vi.advanceTimersByTimeAsync(60_000);
      expect(f.playing.size).toBe(0); expect(f.requests.length).toBe(f.cfg.maxPerRequest * 2);
      f.media.stop();
    }
  });

  it('caps playback per request, skips repeats, and allows an old clip after its recent window', async () => {
    const f = fixture(); f.phase(); await vi.advanceTimersByTimeAsync(5000); f.end('clip-a');
    f.answer('clip-b'); await vi.advanceTimersByTimeAsync(10_000);
    expect([...f.playing.keys()]).toEqual(['clip-b']); f.end('clip-b');
    await vi.advanceTimersByTimeAsync(30_000);
    expect(f.requests).toHaveLength(2);
    expect(f.requests[1].questions.media.criteria).not.toHaveProperty('clip-a');
    f.phase('idle'); await vi.advanceTimersByTimeAsync(300_000); f.answer('clip-a'); f.phase();
    await vi.advanceTimersByTimeAsync(5000); expect([...f.playing.keys()]).toEqual(['clip-a']);
  });

  it('allows the final bounded selection attempt and handles synchronous playback failure', async () => {
    const f = fixture(); f.cfg.maxPerRequest = 1; f.answer(null); f.phase();
    await vi.advanceTimersByTimeAsync(5000);
    f.assets[0].play = report => { report('failed'); return () => { throw new Error('finished clip cannot be cancelled'); }; };
    f.answer('clip-a'); await vi.advanceTimersByTimeAsync(5000);
    expect(f.events.some(row => row.status === 'failed')).toBe(true);
    expect(() => f.phase('idle')).not.toThrow();
  });
});
