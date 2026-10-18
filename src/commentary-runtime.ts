/** Optional read-only Cortico status adapter. RunPhase.idle is not a pause flag. */
export class CommentaryRuntimeGate {
  private inFlight = false;
  private observation: { url: string; at: number; active: boolean } | null = null;
  private stopped = false;
  private epoch = 0;
  constructor(private readonly ports: {
    url(): string;
    get(url: string): Promise<unknown>;
    invalidate(): void;
    now?(): number;
  }) {}
  private now(): number { return this.ports.now?.() ?? Date.now(); }
  active(fallback: boolean): boolean {
    const url = this.ports.url().trim();
    if (!url) return fallback;
    return !this.stopped && this.observation?.url === url && this.now() - this.observation.at <= 5000
      && this.observation.active;
  }
  tick(): void {
    const url = this.ports.url().trim();
    if (!url || this.stopped || this.inFlight) return;
    this.inFlight = true;
    const epoch = this.epoch;
    const apply = (active: boolean) => {
      if (epoch !== this.epoch || this.stopped || this.ports.url().trim() !== url) return;
      if (!active) this.ports.invalidate();
      this.observation = { url, at: this.now(), active };
    };
    void (async () => {
      try {
        const parsed = new URL(url);
        if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) throw Error('Invalid status URL');
        const status = await this.ports.get(url) as { loop?: { running?: unknown; paused?: unknown } } | null;
        apply(status?.loop?.running === true && status.loop.paused === false);
      } catch { apply(false); }
      finally { this.inFlight = false; }
    })();
  }
  stop(): void { this.epoch++; this.stopped = true; this.observation = null; this.ports.invalidate(); }
  resume(): void { this.epoch++; this.stopped = false; this.observation = null; }
}
