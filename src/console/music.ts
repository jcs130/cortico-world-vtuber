/** 曲库播放面板；播放发生在直播音频出口，进度来自服务端开播时间。 */
import type { ConsolePanel, ConsolePanelContext } from 'cortico/web/shared/client-panel.ts';
import { errText, setMsg } from './client.ts';

export interface MusicTrack {
  id: string;
  title: string;
  durationMs: number;
  description?: string;
}

export interface MusicCurrent {
  trackId: string;
  title: string;
  status: 'queued' | 'playing' | 'ended' | 'stopped' | 'failed';
  startedAt: number | null;
  durationMs: number;
  volume: number;
  error?: string;
  aiGenerated?: boolean;
  lyrics?: { atMs: number; endMs: number; text: string }[];
}

export interface MusicState {
  tracks: MusicTrack[];
  current: MusicCurrent | null;
  queue: MusicCurrent[];
  last: MusicCurrent | null;
  error?: string;
  generation?: { enabled: boolean; jobs: { jobId: string; state: string; title: string; trackId?: string }[]; error?: string };
}

function timeText(ms: number): string {
  const sec = Math.floor(Math.max(0, ms) / 1000);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

export const musicPanel: ConsolePanel = {
  mount(ctx: ConsolePanelContext) {
    const { ui } = ctx;
    const card = ui.sheet({ title: '歌曲播放', en: 'music' });
    const status = ui.h('div', 'vt-music-status');
    const title = ui.h('strong', 'vt-music-title');
    const stateLabel = ui.pill('未播放', 'plain');
    const time = ui.h('span', 'vt-music-time');
    const progress = ui.h('progress', 'vt-music-progress');
    progress.max = 1;
    progress.value = 0;
    progress.setAttribute('aria-label', '歌曲播放进度');
    const stopButton = ui.button('停止播放', {
      variant: 'danger', onClick: () => { void command('stop'); },
    });
    const currentBar = ui.rowbar();
    currentBar.append(stateLabel, title, ui.h('span', 'grow'), time, stopButton);
    status.append(currentBar, progress);
    const lyric = ui.h('div', 'vt-music-lyric');
    lyric.setAttribute('aria-live', 'polite');
    status.append(lyric);
    const wrap = ui.h('div', 'vt-music-wrap');
    const intro = ui.h('textarea', 'vt-music-script');
    const outro = ui.h('textarea', 'vt-music-script');
    for (const [input, label] of [[intro, '开场白（可选）'], [outro, '收尾（可选）']] as const) {
      input.maxLength = 500;
      input.rows = 2;
      input.setAttribute('aria-label', label);
      const field = ui.h('label', 'vt-music-field');
      field.append(ui.h('span', null, label), input);
      wrap.append(field);
    }
    const tracksBox = ui.h('div', 'vt-music-tracks');
    const jobsBox = ui.h('div', 'vt-music-tracks');
    const message = ui.msgline('');
    card.body.append(status, wrap, ui.section('原创点歌'), jobsBox, ui.section('曲库'), tracksBox, message);
    ctx.root.appendChild(card.el);

    let current: MusicCurrent | null = null;
    let queued = 0;
    let trackButtons: HTMLButtonElement[] = [];
    let busy = false;
    let polling = false;
    let revision = 0;
    const active = (): boolean => current?.status === 'playing' || current?.status === 'queued' || queued > 0;

    function updateProgress(): void {
      const duration = current?.durationMs ?? 0;
      const elapsed = current?.status === 'playing' && current.startedAt !== null
        ? Math.min(duration, Math.max(0, Date.now() - current.startedAt)) : 0;
      progress.value = duration > 0 ? elapsed / duration : 0;
      time.textContent = current ? `${timeText(elapsed)} / ${timeText(duration)}` : '';
      progress.hidden = current?.status !== 'playing';
      const line = current?.status === 'playing'
        ? current.lyrics?.find(cue => cue.atMs <= elapsed && elapsed < cue.endMs) : undefined;
      lyric.textContent = line?.text ?? '';
      lyric.hidden = !line;
    }

    function updateButtons(): void {
      stopButton.disabled = busy || !active();
      for (const button of trackButtons) {
        button.disabled = busy;
        button.textContent = active() ? '加入队列' : '播放';
      }
    }

    function render(st: MusicState): void {
      queued = st.queue.length;
      current = st.current ?? st.queue[0] ?? st.last;
      const labels: Record<MusicCurrent['status'], string> = {
        queued: '等待播放', playing: '正在播放', ended: '播放结束', stopped: '已停止', failed: '播放失败',
      };
      stateLabel.textContent = (current ? labels[current.status] : '未播放')
        + (queued > 0 ? ` · 等待 ${queued} 首` : '');
      title.textContent = current ? (current.aiGenerated ? 'AI生成 · ' : '') + current.title : '';
      updateProgress();
      trackButtons = [];
      tracksBox.replaceChildren();
      jobsBox.replaceChildren();
      if (!st.generation?.enabled) jobsBox.append(ui.placeholder('原创歌曲生成服务未配置'));
      else if (st.generation.jobs.length === 0) jobsBox.append(ui.placeholder('暂无原创点歌任务'));
      const generationLabels: Record<string, string> = { queued: '等待创作', reviewing: '检查点歌内容', generating: '正在生成',
        validating: '检查歌曲', ready: '已完成，可播放', rejected: '未通过内容检查', review: '需要人工复核', failed: '生成失败', cancelled: '已取消' };
      for (const job of st.generation?.jobs ?? []) {
        const row = ui.h('div', 'vt-music-track');
        const label = ['rejected', 'review', 'failed', 'cancelled'].includes(job.state) ? '原创点歌' : job.title;
        row.append(ui.h('strong', null, label), ui.h('span', 'vt-dim', generationLabels[job.state] ?? '状态待确认'));
        if (['queued', 'reviewing', 'generating', 'validating'].includes(job.state)) {
          const cancel = ui.button('取消生成', { size: 'sm', onClick: () => { void cancelGeneration(job.jobId); } });
          cancel.disabled = busy;
          row.append(cancel);
        }
        jobsBox.append(row);
      }
      if (st.generation?.error) jobsBox.append(ui.placeholder(st.generation.error));
      if (st.tracks.length === 0) tracksBox.append(ui.placeholder('曲库暂无歌曲'));
      for (const track of st.tracks) {
        const row = ui.h('div', 'vt-music-track');
        const detail = ui.h('div', 'vt-music-detail');
        detail.append(ui.h('strong', null, track.title));
        if (track.description) detail.append(ui.h('div', 'vt-dim', track.description));
        const button = ui.button('播放', {
          variant: 'primary', size: 'sm', onClick: () => { void command('play', track.id); },
        });
        button.setAttribute('aria-label', `播放 ${track.title}`);
        row.append(detail, ui.h('span', 'vt-music-time', timeText(track.durationMs)), button);
        tracksBox.append(row);
        trackButtons.push(button);
      }
      updateButtons();
      if (st.error || current?.error) setMsg(message, st.error ?? current!.error!, true);
    }

    async function refresh(): Promise<void> {
      const requestRevision = ++revision;
      try {
        const st = await ctx.invoke<MusicState>('state');
        if (ctx.signal.aborted || requestRevision !== revision) return;
        render(st);
      } catch (err) {
        if (!ctx.signal.aborted && requestRevision === revision) {
          setMsg(message, `读取曲库失败: ${errText(err)}`, true);
        }
      }
    }

    async function command(method: 'play' | 'stop', trackId?: string): Promise<void> {
      if (busy || ctx.signal.aborted) return;
      busy = true;
      ++revision;
      updateButtons();
      try {
        const options = {
          ...(intro.value.trim() ? { intro: intro.value.trim() } : {}),
          ...(outro.value.trim() ? { outro: outro.value.trim() } : {}),
        };
        const args = trackId ? (Object.keys(options).length ? [trackId, options] : [trackId]) : [];
        const out = await ctx.invoke<{ ok: boolean; message?: string }>(method, args);
        if (ctx.signal.aborted) return;
        setMsg(message, out.message ?? (method === 'play' ? '已提交播放' : '已停止播放'), !out.ok);
        await refresh();
      } catch (err) {
        if (!ctx.signal.aborted) setMsg(message, `${method === 'play' ? '播放' : '停止'}失败: ${errText(err)}`, true);
      } finally {
        busy = false;
        if (!ctx.signal.aborted) updateButtons();
      }
    }

    async function cancelGeneration(jobId: string): Promise<void> {
      if (busy || ctx.signal.aborted) return;
      busy = true;
      try {
        const result = await ctx.invoke<{ ok: boolean; message: string }>('cancelGeneration', [jobId]);
        if (!ctx.signal.aborted) { setMsg(message, result.message, !result.ok); await refresh(); }
      } catch (error) { if (!ctx.signal.aborted) setMsg(message, `取消失败: ${errText(error)}`, true); }
      finally { busy = false; if (!ctx.signal.aborted) updateButtons(); }
    }

    ctx.interval(() => {
      if (busy || polling) return;
      polling = true;
      void refresh().finally(() => { polling = false; });
    }, 2000);
    ctx.interval(updateProgress, 250);
    updateProgress();
    updateButtons();
    void refresh();
  },
};
