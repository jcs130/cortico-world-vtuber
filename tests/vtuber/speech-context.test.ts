import { describe, expect, it } from 'vitest';
import { SpeechContextFence } from '../../src/speech-context.ts';

describe('speech context admission ordering', () => {
  it('invalidates an old main draft after a parallel admission, without treating its own RPC as a turn boundary', () => {
    const fence = new SpeechContextFence();
    fence.beginModel('round-1');
    const settle = fence.beginCommentary();
    expect(fence.stale()).toBe(true);
    settle(true); settle(false);
    expect(fence.stale()).toBe(true);
    expect(fence.beginModel('round-1')).toBe(false);
    expect(fence.stale()).toBe(true);
    fence.defer();
    expect(fence.holdCommentary).toBe(true);
    fence.beginModel('round-2');
    expect(fence.stale()).toBe(false);
    expect(fence.holdCommentary).toBe(true);
    fence.foregroundAccepted();
    expect(fence.holdCommentary).toBe(false);
  });

  it('releases a rejected or failed pending admission without inventing spoken history', () => {
    const fence = new SpeechContextFence();
    fence.beginModel('round-1');
    const settle = fence.beginCommentary();
    settle(false);
    expect(fence.stale()).toBe(false);
    fence.defer(); fence.idle();
    expect(fence.holdCommentary).toBe(false);
    expect(fence.stale()).toBe(false);
    fence.beginCommentary()(true);
    expect(fence.stale()).toBe(false); // Older direct hosts have no model phase to invalidate.
    fence.beginModel('round-2');
    expect(fence.stale()).toBe(false);
  });
});
