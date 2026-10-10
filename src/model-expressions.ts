import type { ModelProfile } from './models/index.ts';
import { isMoodTag } from './parser.ts';

/** Explicit emotion state beats voice metadata. Files are owned by leases, not independent off timers. */
export class ModelExpressions {
  private explicit: string | null = null;
  private voice: { token: symbol; key: string } | null = null;
  private fx = new Map<string, ReturnType<typeof setTimeout>>();
  private starts = new Set<ReturnType<typeof setTimeout>>();
  private applied = new Set<string>();
  private running = false;
  private dirty = false;
  private generation = 0;
  private profileId = '';
  private stopped = false;
  private pending: Promise<void> | null = null;

  constructor(private readonly api: { connected: boolean; setExpression(file: string, active: boolean): Promise<void> },
    private readonly profile: () => ModelProfile, private readonly onError?: (error: Error) => void) {}

  /** Called before every frame: profile changes must release old owned files, even without new speech. */
  syncProfile(): void {
    const p = this.profile(), id = `${p.id}:${p.vtsModelName}`;
    if (this.profileId && id !== this.profileId) this.clear();
    this.profileId = id;
  }

  emotion(key: string | null): void {
    if (this.stopped) return;
    this.syncProfile(); this.explicit = key; this.reconcile();
  }

  speech(text: string, startedAt = Date.now()): () => void {
    if (this.stopped) return () => {};
    this.syncProfile();
    const tag = text.match(/[（(][^()（）\n]{1,32}[)）]/gu)?.find(isMoodTag);
    const key = tag?.slice(1, -1).split('@')[0].toLowerCase();
    // No keyword guessing from dialogue, and factual parentheses are never an emotion.
    if (!key || !this.profile().emotionMap?.[key] || /@0(?:\.0{1,2})?[)）]$/u.test(tag!)) return () => {};
    const token = Symbol('speech');
    let released = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const begin = (): void => {
      if (timer) this.starts.delete(timer);
      if (released || this.stopped) return;
      this.voice = { token, key }; this.reconcile();
    };
    if (startedAt > Date.now()) { timer = setTimeout(begin, startedAt - Date.now()); this.starts.add(timer); }
    else begin();
    return () => {
      released = true;
      if (timer) { clearTimeout(timer); this.starts.delete(timer); }
      if (this.voice?.token !== token) return;
      this.voice = null; this.reconcile();
    };
  }

  pulse(file: string, durationMs: number): void {
    if (this.stopped) return;
    this.syncProfile();
    const prior = this.fx.get(file);
    if (prior) clearTimeout(prior);
    const timer = setTimeout(() => { this.fx.delete(file); this.reconcile(); }, durationMs);
    timer.unref?.();
    this.fx.set(file, timer); this.reconcile();
  }

  clear(): void {
    this.explicit = null; this.voice = null;
    for (const timer of this.fx.values()) clearTimeout(timer);
    for (const timer of this.starts) clearTimeout(timer);
    this.fx.clear(); this.starts.clear(); this.reconcile();
  }

  /** A new model must never receive old model's queued expressions or timer callbacks. */
  modelChanged(): void {
    this.generation++; this.applied.clear(); this.clear();
  }

  stop(): void { this.stopped = true; this.clear(); }

  settled(): Promise<void> { return this.pending ?? Promise.resolve(); }

  private wanted(): Set<string> {
    const result = new Set(this.fx.keys());
    const key = this.explicit ?? this.voice?.key;
    const files = key ? this.profile().emotionMap?.[key] : null;
    for (const file of typeof files === 'string' ? [files] : files ?? []) result.add(file);
    return result;
  }

  private reconcile(): void {
    this.dirty = true;
    if (this.running || !this.api.connected) return;
    this.running = true;
    this.pending = (async () => {
      try {
        while (this.dirty && this.api.connected) {
          this.dirty = false;
          const wanted = this.wanted(), generation = this.generation;
          const removed = [...this.applied].find((file) => !wanted.has(file));
          const added = [...wanted].find((file) => !this.applied.has(file));
          const file = removed ?? added;
          if (!file) continue;
          const active = removed === undefined;
          try { await this.api.setExpression(file, active); }
          catch (error) { this.onError?.(error instanceof Error ? error : new Error(String(error))); return; }
          if (generation === this.generation) {
            if (active) this.applied.add(file); else this.applied.delete(file);
          }
          this.dirty = true;
        }
      } finally { this.running = false; }
    })();
  }
}
