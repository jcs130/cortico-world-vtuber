/** Orders speech candidates against parallel admissions, without judging sentence meaning or running a model. */
export class SpeechContextFence {
  private revision = 0;
  private requestRevision: number | null = null;
  private modelKey = '';
  private pending = 0;
  private retryHeld = false;

  beginModel(key: string): boolean {
    if (key === this.modelKey) return false;
    this.modelKey = key;
    this.requestRevision = this.revision;
    return true;
  }

  /** Called before IPC; an in-flight admission must not race past the output tap. */
  beginCommentary(): (accepted: boolean) => void {
    this.pending++;
    let settled = false;
    return accepted => {
      if (settled) return;
      settled = true;
      this.pending--;
      if (accepted) this.revision++;
    };
  }

  stale(): boolean {
    return this.requestRevision !== null && (this.pending > 0 || this.requestRevision !== this.revision);
  }

  defer(): void { this.retryHeld = true; }
  get holdCommentary(): boolean { return this.retryHeld; }
  foregroundAccepted(): void { this.retryHeld = false; }
  idle(): void { this.requestRevision = null; this.modelKey = ''; this.retryHeld = false; }
}
