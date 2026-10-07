/** 语音服务启停与已声明的 IndexTTS 声线配置。保存经框架配置面持久化并热更新。 */
import type { ConsolePanelContext } from 'cortico/web/shared/client-panel.ts';
import { TTS_SPEECH_DEFAULTS, type TtsSpeechPreferences } from '../tts-speech.ts';
import { dimLine, errText, numField, setMsg, type TtsPanelState } from './client.ts';

export function speechControls(ctx: ConsolePanelContext, refresh: () => Promise<void>): {
  el: HTMLElement; render(state: TtsPanelState): void; unavailable(): void;
} {
  const { ui } = ctx;
  const box = ui.h('div');
  const status = ui.chip('读取中');
  const detail = dimLine(ctx);
  const msg = ui.msgline('');
  const start = ui.button('启动语音', { onClick: () => void act('start') });
  const stop = ui.button('停止语音', { variant: 'danger', onClick: () => void act('stop') });
  const restart = ui.button('重启语音', { onClick: () => void act('restart') });
  const config = ui.h('a', 'btn sm', '服务地址与自动启动配置');
  config.href = `#/provider/${encodeURIComponent(ctx.pageId)}/~config`;
  const bar = ui.rowbar(); bar.classList.add('vt-wrap');
  bar.append(status, start, stop, restart, config);
  box.append(bar, detail);

  const prefs = ui.h('div');
  const enabled = ui.checkbox('使用网页中的声线配置', { onChange: dirty });
  const voice = ui.select({ onInput: dirty });
  voice.setAttribute('aria-label', '音色');
  const speed = numField(ctx, { value: TTS_SPEECH_DEFAULTS.speed, min: 0.5, max: 2, step: 0.05 });
  const mix = numField(ctx, { value: TTS_SPEECH_DEFAULTS.emotionMix, min: 0, max: 0.45, step: 0.05 });
  const confidence = numField(ctx, { value: TTS_SPEECH_DEFAULTS.emotionMinConfidence, min: 0, max: 1, step: 0.05 });
  speed.setAttribute('aria-label', '语速'); mix.setAttribute('aria-label', '语气强度');
  confidence.setAttribute('aria-label', '语气置信度');
  for (const input of [speed, mix, confidence]) input.addEventListener('input', dirty, { signal: ctx.signal });
  const fields = ui.rowbar(); fields.classList.add('vt-wrap');
  fields.append(ui.field('音色', voice), ui.field('语速', speed), ui.field('语气强度', mix), ui.field('语气置信度', confidence));
  const save = ui.button('保存声线配置', { variant: 'primary', onClick: () => void savePreferences() });
  prefs.append(ui.section('IndexTTS 声线'), enabled.el, fields,
    dimLine(ctx, '保存后下一次合成生效。语气强度设为 0 时沿用参考语气。'), save);
  box.append(prefs, msg);
  let state: TtsPanelState | null = null;
  let saved: TtsSpeechPreferences | null = null;
  let busy = false;
  let polling = false;

  function form(): TtsSpeechPreferences {
    return { enabled: enabled.checked, voice: voice.value, speed: Number(speed.value),
      emotionMix: Number(mix.value), emotionMinConfidence: Number(confidence.value) };
  }
  function isDirty(): boolean { return saved !== null && JSON.stringify(form()) !== JSON.stringify(saved); }
  function actions(): void {
    start.disabled = busy || !state || ['starting', 'stopping', 'running'].includes(state.phase);
    stop.disabled = busy || !state || state.ownership === 'external' || state.kind === 'external'
      || state.phase === 'stopped' || state.phase === 'stopping';
    restart.disabled = busy || !state || state.ownership !== 'owned' || ['starting', 'stopping'].includes(state.phase);
    save.disabled = busy || !isDirty();
  }
  function dirty(): void {
    setMsg(msg, isDirty() ? '声线配置尚未保存。' : '');
    actions();
  }
  ctx.guardLeave(() => isDirty() ? '声线配置有未保存的改动' : null);
  function render(next: TtsPanelState): void {
    const keep = isDirty(); state = next;
    status.textContent = ({ running: '运行中', stopped: '已停止', starting: '启动中', stopping: '停止中', error: '异常' } as Record<string, string>)[next.phase] ?? next.phase;
    detail.textContent = [next.kind, next.ownership === 'owned' ? '由 Corti 管理' : next.ownership === 'external' ? '外部服务' : '', next.url, next.pid ? `进程 ${next.pid}` : '', next.detail].filter(Boolean).join(' · ');
    prefs.hidden = next.kind !== 'indextts';
    if (!keep) {
      saved = { ...TTS_SPEECH_DEFAULTS, ...next.speech };
      enabled.setChecked(saved.enabled);
      const voices = [...new Set(['', ...(next.speechVoices ?? []), saved.voice].filter((v, i) => i === 0 || !!v))];
      voice.replaceChildren(...voices.map(value => {
        const option = ui.h('option', null, value || `沿用适配器（${String(next.health?.voice ?? '默认')}）`);
        option.value = value; return option;
      }));
      voice.value = saved.voice; speed.value = String(saved.speed);
      mix.value = String(saved.emotionMix); confidence.value = String(saved.emotionMinConfidence);
    }
    actions();
  }
  function unavailable(): void {
    state = null;
    status.textContent = '状态不可用';
    actions();
  }
  async function act(action: 'start' | 'stop' | 'restart'): Promise<void> {
    if (busy) return;
    busy = true; actions(); setMsg(msg, '处理中…');
    try {
      if (action === 'restart') { await ctx.invoke('stop'); await ctx.invoke('start'); }
      else await ctx.invoke(action);
      if (ctx.signal.aborted) return;
      await refresh(); await ctx.refresh();
      setMsg(msg, state?.phase === 'running' ? '语音服务已就绪。' : state?.phase === 'stopped' ? '语音服务已停止。' : state?.detail ?? '请查看服务状态。', state?.phase === 'error');
    } catch (error) { if (!ctx.signal.aborted) setMsg(msg, errText(error), true); }
    finally { busy = false; if (!ctx.signal.aborted) actions(); }
  }
  async function savePreferences(): Promise<void> {
    if (busy || !isDirty()) return;
    if ([speed, mix, confidence].some(input => !input.reportValidity())) return;
    busy = true; actions();
    const value = form();
    try {
      await ctx.setConfig('world:vtuber', Object.fromEntries(Object.entries(value).map(([key, val]) => [`worlds.vtuber.ttsSpeech.${key}`, val])));
      if (ctx.signal.aborted) return;
      saved = value;
      await refresh();
      setMsg(msg, '已保存，下一次合成生效。');
    } catch (error) { if (!ctx.signal.aborted) setMsg(msg, errText(error), true); }
    finally { busy = false; if (!ctx.signal.aborted) actions(); }
  }
  ctx.interval(() => {
    if (busy || polling) return;
    polling = true;
    void refresh().finally(() => { polling = false; });
  }, 2000);
  actions();
  return { el: box, render, unavailable };
}
