import type { IRFrame } from './mixer.ts';

/**
 * Kalidokit 的双眼统一注视思路用于虚拟演出输入；不引入人脸 landmark 的比例假设。
 * 时间常数按真实 dt 求值，小幅抖动平滑、快速扫视放行。眼睑本来已有闭合曲线，
 * 不再低通：重复过滤会使短眨眼闭不上，也会抹掉有意的 wink。
 */
export class EyeGazeFilter {
  private axes = new Map<string, { raw: number; value: number; ts: number }>();
  reset(): void { this.axes.clear(); }

  frame(frame: IRFrame, now: number, tauMs: number): IRFrame {
    if (!(tauMs > 0)) { this.reset(); return frame; }
    const out = { ...frame };
    for (const axis of ['X', 'Y']) {
      const left = `EyeLeft${axis}`, right = `EyeRight${axis}`;
      const l = frame[left], r = frame[right];
      // Only fuse near-identical binocular targets; deliberately asymmetric poses retain ownership.
      const paired = l && r && l.mode === r.mode && Math.abs(l.value - r.value) < 0.06;
      for (const id of [left, right]) {
        const input = frame[id];
        if (!input || !Number.isFinite(input.value)) { this.axes.delete(id); continue; }
        const raw = paired ? (l.value + r.value) / 2 : input.value;
        const prev = this.axes.get(id), dt = prev ? now - prev.ts : 0;
        let value = raw;
        if (prev && dt > 0 && dt < 250) {
          const speed = Math.abs(raw - prev.raw) / (dt / 1000);
          const tau = Math.min(tauMs, 8 + (tauMs - 8) / (1 + (speed / 1.8) ** 2));
          value = prev.value + (raw - prev.value) * (1 - Math.exp(-dt / Math.max(1, tau)));
        }
        this.axes.set(id, { raw, value, ts: now });
        out[id] = { ...input, value };
      }
    }
    return out;
  }
}
