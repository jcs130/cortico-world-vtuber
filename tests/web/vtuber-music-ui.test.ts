/** @vitest-environment jsdom */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const LIFECYCLE = 'cortico/web/client/core/lifecycle.ts';
const PANEL_CONTEXT = 'cortico/web/client/console-pages/context.ts';
const MUSIC_PANEL = '../../src/console/music.ts';
type Any = any;
const { Lifecycle } = await import(LIFECYCLE) as Any;
const { createPanelContext } = await import(PANEL_CONTEXT) as Any;
const { musicPanel } = await import(MUSIC_PANEL) as Any;
const doc = (globalThis as Any).document;
const flush = async (): Promise<void> => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const json = (body: unknown): Any => ({ ok: true, status: 200, text: async () => JSON.stringify(body) });
let lifecycles: Any[] = [];

function mount(): { root: Any; lifecycle: Any } {
  const lifecycle = new Lifecycle();
  lifecycles.push(lifecycle);
  const root = doc.createElement('div');
  doc.body.append(root);
  const ctx = createPanelContext({
    pageId: 'world:vtuber', panelId: 'music', root, lifecycle, overlayHost: doc.body,
    refresh: async () => {}, addLeaveGuard: () => ({ dispose() {} }),
    memo: { get: (_key: string, fallback: unknown) => fallback, set() {} },
    createSocket: () => { throw new Error('歌曲面板通过状态接口读取'); },
    wsUrl: (path: string) => path, onError: vi.fn(), doc,
  });
  musicPanel.mount(ctx);
  return { root, lifecycle };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(100_000);
});
afterEach(() => {
  for (const lifecycle of lifecycles) lifecycle.dispose();
  lifecycles = [];
  vi.useRealTimers();
  vi.unstubAllGlobals();
  doc.body.replaceChildren();
});

describe('歌曲播放面板', () => {
  it('后台创作可独立取消，拒绝内容不回显，也不停止现有歌曲', async () => {
    let cancelledArgs: unknown;
    const st: Any = { tracks: [], current: { trackId: 'playing', title: '正在唱', status: 'playing',
      startedAt: Date.now(), durationMs: 30_000, volume: 1 }, queue: [], last: null,
      generation: { enabled: true, jobs: [
        { jobId: 'pending', state: 'generating', title: '新原创 <img>' },
        { jobId: 'blocked', state: 'rejected', title: '不应显示的点歌内容' },
      ] } };
    vi.stubGlobal('fetch', async (input: Any, init?: Any) => {
      const url = String(input);
      if (url.endsWith('/music/state')) return json(st);
      if (url.endsWith('/music/cancelGeneration')) {
        cancelledArgs = JSON.parse(init.body).args;
        st.generation.jobs[0].state = 'cancelled';
        return json({ ok: true, message: '创作取消，播放保留' });
      }
      throw new Error(`不应停止播放或调用其他接口: ${url}`);
    });
    const { root } = mount();
    await flush();
    expect(root.textContent).not.toContain('不应显示的点歌内容');
    expect(root.querySelector('img')).toBeNull();
    const cancel = [...root.querySelectorAll('button')].find((button: Any) => button.textContent === '取消生成');
    cancel.click();
    await flush();
    expect(cancelledArgs).toEqual(['pending']);
    expect(root.querySelector('.vt-music-title').textContent).toBe('正在唱');
    expect(root.textContent).toContain('已取消');
  });
  it('开场与收尾一次提交，歌词按真实播放时间切换并在终态撤下', async () => {
    const track = { id: 'song', title: 'Example', durationMs: 30_000 };
    const st: Any = { tracks: [track], current: null, queue: [], last: null };
    let playArgs: unknown;
    vi.stubGlobal('fetch', async (input: Any, init?: Any) => {
      if (String(input).endsWith('/music/play')) {
        playArgs = JSON.parse(init.body).args;
        st.current = { ...track, trackId: track.id, status: 'playing', startedAt: Date.now(),
          lyrics: [{ atMs: 1000, endMs: 3000, text: 'First <img>' }, { atMs: 4000, endMs: 6000, text: 'Second' }] };
        return json({ ok: true });
      }
      return json(st);
    });
    const { root } = mount();
    await flush();
    root.querySelector('[aria-label="开场白（可选）"]').value = '  Before  ';
    root.querySelector('[aria-label="收尾（可选）"]').value = 'After';
    root.querySelector('[aria-label="播放 Example"]').click();
    await flush();
    expect(playArgs).toEqual(['song', { intro: 'Before', outro: 'After' }]);
    await vi.advanceTimersByTimeAsync(1250);
    expect(root.querySelector('.vt-music-lyric').textContent).toBe('First <img>');
    expect(root.querySelector('img')).toBeNull();
    await vi.advanceTimersByTimeAsync(2000);
    expect(root.querySelector('.vt-music-lyric').hidden).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(root.querySelector('.vt-music-lyric').textContent).toBe('Second');
    st.last = { ...st.current, status: 'ended' }; st.current = null;
    await vi.advanceTimersByTimeAsync(2000);
    expect(root.querySelector('.vt-music-lyric').hidden).toBe(true);
    expect(root.querySelector('.vt-music-lyric').textContent).toBe('');
  });
  it('播放与排队来自服务端，进度按开播时间推进，停止清空待播状态', async () => {
    const tracks = [
      { id: 'first', title: 'First <img>', durationMs: 30_000, description: 'A local song' },
      { id: 'second', title: 'Second', durationMs: 40_000 },
    ];
    const st: Any = { tracks, current: null, queue: [], last: null };
    vi.stubGlobal('fetch', async (input: Any, init?: Any) => {
      const url = String(input);
      if (url.endsWith('/music/state')) return json(st);
      if (url.endsWith('/music/play')) {
        const track = tracks.find(t => t.id === JSON.parse(init.body).args[0])!;
        const playback = { trackId: track.id, title: track.title, durationMs: track.durationMs,
          status: st.current ? 'queued' : 'playing', startedAt: st.current ? null : Date.now(), volume: 1 };
        if (st.current) st.queue.push(playback); else st.current = playback;
        return json({ ok: true, message: st.queue.length ? '已排队' : '开始播放' });
      }
      if (url.endsWith('/music/stop')) {
        st.last = { ...st.current, status: 'stopped' }; st.current = null; st.queue = [];
        return json({ ok: true, message: '已停止播放' });
      }
      throw new Error(`未声明的请求: ${url}`);
    });
    const { root, lifecycle } = mount();
    await flush();
    expect(root.textContent).toContain('A local song');
    expect(root.querySelector('img')).toBeNull();
    expect(root.querySelector('audio')).toBeNull();
    expect(root.querySelector('button').disabled).toBe(true);
    root.querySelector('[aria-label="播放 First <img>"]').click();
    await flush();
    expect(root.textContent).toContain('正在播放');
    expect(root.querySelector('.vt-music-title').textContent).toBe('First <img>');
    await vi.advanceTimersByTimeAsync(5000);
    expect(root.querySelector('.vt-music-status .vt-music-time').textContent).toBe('0:05 / 0:30');
    expect(root.querySelector('progress').value).toBeCloseTo(1 / 6);
    root.querySelector('[aria-label="播放 Second"]').click();
    await flush();
    expect(root.textContent).toContain('等待 1 首');
    expect(root.querySelector('.vt-music-title').textContent).toBe('First <img>');
    root.querySelector('button').click();
    await flush();
    expect(root.textContent).toContain('已停止');
    expect(root.querySelector('progress').hidden).toBe(true);
    expect(root.querySelector('button').disabled).toBe(true);
    lifecycle.dispose();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('播放拒绝保留服务端错误，空曲库不显示可播放条目', async () => {
    let tracks = [{ id: 'song', title: 'Example', durationMs: 30_000 }];
    vi.stubGlobal('fetch', async (input: Any) => String(input).endsWith('/music/play')
      ? json({ ok: false, message: '音频出口未连接' })
      : json({ tracks, current: null, queue: [], last: null }));
    const { root } = mount();
    await flush();
    root.querySelector('[aria-label="播放 Example"]').click();
    await flush();
    expect(root.querySelector('.msgline.bad').textContent).toBe('音频出口未连接');
    expect(root.textContent).toContain('未播放');
    tracks = [];
    await vi.advanceTimersByTimeAsync(2000);
    expect(root.textContent).toContain('曲库暂无歌曲');
    expect(root.querySelector('.vt-music-tracks button')).toBeNull();
  });

  it('卸载取消轮询，迟到状态不能重新绘制已清空面板', async () => {
    let resolveState: (value: Any) => void = () => {};
    let requestSignal: AbortSignal | undefined;
    vi.stubGlobal('fetch', (_input: Any, init: Any) => {
      requestSignal = init.signal;
      return new Promise(resolve => { resolveState = resolve; });
    });
    const { root, lifecycle } = mount();
    await flush();
    lifecycle.dispose();
    root.replaceChildren();
    expect(requestSignal?.aborted).toBe(true);
    resolveState(json({ tracks: [], current: null, queue: [], last: null }));
    await flush();
    expect(root.children.length).toBe(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});
