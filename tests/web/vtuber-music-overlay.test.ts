import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const JSDOM_MODULE = 'jsdom';
const { JSDOM } = await import(JSDOM_MODULE) as any;
const page = readFileSync(new URL('../../src/overlay/overlay.html', import.meta.url), 'utf8');
const script = readFileSync(new URL('../../src/overlay/app.js', import.meta.url), 'utf8');
const styles = readFileSync(new URL('../../src/overlay/styles.css', import.meta.url), 'utf8');
let dom: any;
let stream: any;

function startOverlay(url = 'http://localhost:7792/overlay', parent?: { postMessage: ReturnType<typeof vi.fn> }, referrer?: string): void {
  dom = new JSDOM(page, { url, referrer, runScripts: 'outside-only' });
  if (parent) Object.defineProperty(dom.window, 'parent', { value: parent });
  const style = dom.window.document.createElement('style');
  style.textContent = styles;
  dom.window.document.head.append(style);
  dom.window.Date = Date;
  dom.window.setTimeout = setTimeout; dom.window.clearTimeout = clearTimeout;
  dom.window.setInterval = setInterval; dom.window.clearInterval = clearInterval;
  dom.window.Range.prototype.getClientRects = () => [];
  dom.window.EventSource = class {
    onmessage: any;
    close = vi.fn();
    constructor() { stream = this; }
    send(data: unknown): void { this.onmessage({ data: JSON.stringify(data) }); }
  };
  dom.window.eval(script);
}
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime(100_000);
  startOverlay();
});
afterEach(() => {
  dom.window.dispatchEvent(new dom.window.Event('pagehide'));
  dom.window.close(); vi.useRealTimers();
});

const playback = (status = 'playing') => ({ kind: 'music', playbackId: 1, trackId: 'example',
  title: 'Song <img>', status, startedAt: 93_000, durationMs: 30_000, volume: 1 });

describe('歌曲 overlay', () => {
  it('生成歌曲在实际播放卡片中保留 AI 标识，标题按文本渲染', () => {
    stream.send({ type: 'music', music: { ...playback(), aiGenerated: true } });
    const title = dom.window.document.getElementById('music-title');
    expect(title.textContent).toBe('AI生成 · Song <img>');
    expect(title.querySelector('img')).toBeNull();
    stream.send({ type: 'music', music: { ...playback(), playbackId: 2, aiGenerated: false } });
    expect(title.textContent).toBe('Song <img>');
  });
  it('歌词从播放快照恢复，句间留白、预告下一句，终态恢复口播字幕', async () => {
    const song = { ...playback(), lyrics: [
      { atMs: 6000, endMs: 9000, text: 'First <img>' },
      { atMs: 10_000, endMs: 12_000, text: 'Second' },
    ] };
    stream.send({ type: 'subtitle', cues: [{ text: 'Spoken', atMs: -1, durMs: 60_000 }] });
    stream.send({ type: 'snapshot', music: song });
    const doc = dom.window.document;
    expect(doc.getElementById('music-lyric-current').textContent).toBe('First <img>');
    expect(doc.getElementById('music-lyric-next').textContent).toBe('Second');
    expect(doc.getElementById('music-lyrics').querySelector('img')).toBeNull();
    expect(dom.window.getComputedStyle(doc.getElementById('bubbles')).visibility).toBe('hidden');
    await vi.advanceTimersByTimeAsync(2250);
    expect(doc.getElementById('music-lyric-current').textContent).toBe('');
    expect(doc.getElementById('music-lyric-next').textContent).toBe('Second');
    await vi.advanceTimersByTimeAsync(1000);
    expect(doc.getElementById('music-lyric-current').textContent).toBe('Second');
    stream.send({ type: 'music', music: { ...song, status: 'ended' } });
    expect(doc.getElementById('music-lyrics').hidden).toBe(true);
    expect(doc.getElementById('music-lyric-current').textContent).toBe('');
    expect(doc.body.classList.contains('lyrics-on')).toBe(false);
    expect(dom.window.getComputedStyle(doc.getElementById('bubbles')).visibility).toBe('visible');
  });

  it('字幕图层开关同时控制歌词，下一首排队不会覆盖当前歌词', () => {
    const song = { ...playback(), lyrics: [{ atMs: 0, endMs: 20_000, text: 'Current line' }] };
    stream.send({ type: 'music', music: song });
    stream.send({ type: 'music', music: { ...song, playbackId: 2, status: 'queued' } });
    expect(dom.window.document.getElementById('music-lyric-current').textContent).toBe('Current line');
    stream.send({ type: 'overlay.config', config: { subtitles: false } });
    expect(dom.window.document.getElementById('music-lyrics').hidden).toBe(true);
    stream.send({ type: 'overlay.config', config: { subtitles: true } });
    expect(dom.window.document.getElementById('music-lyric-current').textContent).toBe('Current line');
    dom.window.close();
    startOverlay('http://localhost:7792/overlay?subtitles=0');
    stream.send({ type: 'snapshot', music: song });
    expect(dom.window.document.getElementById('music-lyrics').hidden).toBe(true);
    expect(dom.window.document.body.classList.contains('lyrics-on')).toBe(false);
  });
  it('订阅快照恢复已经播放的进度，歌名按文本显示且保留原字幕', async () => {
    stream.send({ type: 'subtitle', cues: [{ text: 'Spoken caption', atMs: -1, durMs: 60_000 }] });
    stream.send({ type: 'snapshot', music: playback() });
    const doc = dom.window.document;
    expect(doc.getElementById('music-card').hidden).toBe(false);
    expect(doc.getElementById('music-title').textContent).toBe('Song <img>');
    expect(doc.getElementById('music-card').querySelector('img')).toBeNull();
    expect(doc.getElementById('music-time').textContent).toBe('0:07 / 0:30');
    await vi.advanceTimersByTimeAsync(3000);
    expect(doc.getElementById('music-time').textContent).toBe('0:10 / 0:30');
    expect(doc.getElementById('music-progress').value).toBeCloseTo(1 / 3);
    expect(doc.getElementById('bubbles').textContent).toBe('Spoken caption');
  });

  it.each(['ended', 'stopped', 'failed'])('收到 %s 状态撤下歌曲卡并停止进度计时', status => {
    stream.send({ type: 'music', kind: 'music', music: playback() });
    expect(dom.window.document.getElementById('music-card').hidden).toBe(false);
    stream.send({ type: 'music', kind: 'music', music: playback(status) });
    expect(dom.window.document.getElementById('music-card').hidden).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('下一首入队或被取消时仍显示当前歌曲', () => {
    stream.send({ type: 'music', music: playback() });
    stream.send({ type: 'music', music: { ...playback('queued'), playbackId: 2, title: 'Next song' } });
    stream.send({ type: 'music', music: { ...playback('stopped'), playbackId: 2, title: 'Next song' } });
    expect(dom.window.document.getElementById('music-card').hidden).toBe(false);
    expect(dom.window.document.getElementById('music-title').textContent).toBe('Song <img>');
    expect(vi.getTimerCount()).toBe(1);
  });

  it('游戏内小窗使用顶部单行卡片，同时保留旧字幕与姓名牌', () => {
    dom.window.close();
    startOverlay('http://localhost:7792/overlay?ingame=1&speaker=Speaker');
    stream.send({ type: 'subtitle', cues: [{ text: 'Existing caption', atMs: -1, durMs: 60_000 }] });
    stream.send({ type: 'music', music: playback() });
    const doc = dom.window.document;
    const cardStyle = dom.window.getComputedStyle(doc.getElementById('music-card'));
    expect(doc.body.classList.contains('ingame')).toBe(true);
    expect(cardStyle.top).toBe('4px');
    expect(cardStyle.height).toBe('28px');
    expect(cardStyle.left).toBe('50%');
    expect(cardStyle.display).toBe('grid');
    expect(dom.window.getComputedStyle(doc.querySelector('.music-heading')).whiteSpace).toBe('nowrap');
    expect(doc.getElementById('bubbles').textContent).toBe('Existing caption');
    expect(doc.querySelector('.bubble').dataset.speaker).toBe('Speaker');
    stream.send({ type: 'music', music: playback('ended') });
    expect(dom.window.getComputedStyle(doc.getElementById('music-card')).display).toBe('none');
    expect(doc.getElementById('bubbles').textContent).toBe('Existing caption');
  });

  it('排队和空快照不冒充正在播放，页面卸载关闭订阅与计时', () => {
    stream.send({ type: 'music', kind: 'music', music: playback('queued') });
    expect(dom.window.document.getElementById('music-card').hidden).toBe(true);
    stream.send({ type: 'music', kind: 'music', music: playback() });
    stream.send({ type: 'snapshot', music: null });
    expect(dom.window.document.getElementById('music-card').hidden).toBe(true);
    stream.send({ type: 'music', kind: 'music', music: playback() });
    dom.window.dispatchEvent(new dom.window.Event('pagehide'));
    expect(stream.close).toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('嵌入游戏时向确切父页面发送播放标志，口播结束与歌曲终态解除让位', async () => {
    dom.window.close();
    const parent = { postMessage: vi.fn() };
    startOverlay('http://localhost:7792/overlay?ingame=1&subtitles=0', parent,
      'http://localhost:7793/viewer');
    const flags = () => parent.postMessage.mock.lastCall?.[0]?.detail;
    expect(parent.postMessage.mock.lastCall?.[1]).toBe('http://localhost:7793');
    expect(parent.postMessage.mock.lastCall?.[0]?.type).toBe('mc-viewer.foreground-audio');
    expect(flags()).toEqual({ speech: false, music: false });
    stream.send({ type: 'subtitle', cues: [{ text: 'Not rendered', atMs: -1000, speakMs: 3000, durMs: 8000 }] });
    expect(dom.window.document.getElementById('bubbles').textContent).toBe('');
    expect(flags().speech).toBe(true);
    await vi.advanceTimersByTimeAsync(2000);
    expect(flags().speech).toBe(false);
    stream.send({ type: 'music', music: playback() });
    expect(flags().music).toBe(true);
    stream.send({ type: 'music', music: { ...playback('queued'), playbackId: 2 } });
    expect(flags().music).toBe(true);
    stream.send({ type: 'music', music: playback('ended') });
    expect(flags().music).toBe(false);
    stream.send({ type: 'subtitle', cues: [{ text: 'Cut', atMs: 0, speakMs: 5000 }] });
    stream.send({ type: 'subtitle.cut' });
    expect(flags().speech).toBe(false);
    dom.window.dispatchEvent(new dom.window.Event('pagehide'));
    expect(flags()).toEqual({ speech: false, music: false });
    expect(vi.getTimerCount()).toBe(0);
  });

  it('独立 overlay 和无可信父页面地址时不广播音频状态', () => {
    for (const [url, referrer] of [
      ['http://localhost:7792/overlay', 'http://localhost:7793/'],
      ['http://localhost:7792/overlay?ingame=1', undefined],
      ['http://localhost:7792/overlay?ingame=1', 'file:///local/viewer.html'],
    ]) {
      dom.window.close();
      const parent = { postMessage: vi.fn() };
      startOverlay(url, parent, referrer);
      stream.send({ type: 'music', music: playback() });
      stream.send({ type: 'subtitle', cues: [{ text: 'Speech', atMs: 0, durMs: 5000 }] });
      expect(parent.postMessage).not.toHaveBeenCalled();
      dom.window.dispatchEvent(new dom.window.Event('pagehide'));
    }
  });
});
