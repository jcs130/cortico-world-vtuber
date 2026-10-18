import type { SpeechAvailability } from './commentary.ts';

export interface SpeechPlanningWindow {
  queueRevision: number;
  observedAtMs: number;
  remainingMs: number;
}

/** Revalidate at delivery: a wake may stay queued through slow inference. */
export function currentSpeechWindow(s: SpeechAvailability | null | undefined, live: boolean, leadSec: number,
  now = Date.now()): SpeechPlanningWindow | null {
  if (!live || !s || !s.busy || s.playing !== true || s.musicPlaying !== false || !s.ttsReady
    || !Number.isFinite(leadSec) || leadSec <= 0 || now - s.observedAtMs > 5000 || s.observedAtMs > now + 1000
    || !Number.isSafeInteger(s.queueRevision) || s.queueRevision! < 1
    || !Number.isFinite(s.backlogMs) || s.backlogMs! <= 0 || s.backlogMs! > Math.min(30, leadSec) * 1000) return null;
  return { queueRevision: s.queueRevision!, observedAtMs: s.observedAtMs, remainingMs: s.backlogMs! };
}

/** One advance notice per admitted queue revision; never starts a model or a body. */
export class SpeechPlanningClock {
  private announcedThrough = 0;

  observe(s: SpeechAvailability | null | undefined, live: boolean, leadSec: number, now = Date.now()): SpeechPlanningWindow | null {
    const current = currentSpeechWindow(s, live, leadSec, now);
    if (!current || current.queueRevision <= this.announcedThrough) return null;
    this.announcedThrough = current.queueRevision;
    return current;
  }

  reset(): void { this.announcedThrough = 0; }
}
