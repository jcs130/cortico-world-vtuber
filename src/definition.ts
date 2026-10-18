import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { describeTools, type WorldDefinition } from 'cortico/world.ts';
import {
  OVERLAY_CONFIG_DEFAULTS,
  TTS_PROFILE_DEFAULTS,
  VTUBER_DEFAULTS,
  VTUBER_SECRET,
  VTUBER_TOOL_DECLS,
  type OverlayConfig,
  type TtsProfile,
} from './world.ts';
import { VtuberWorldProxy } from './proxy.ts';
import { playbackConfigOptions } from './device-audio.ts';
import { createHash } from 'node:crypto';
import { TTS_API_KEY_SECRET, TTS_TRANSPORT_DEFAULTS, type TtsTransportConfig } from './tts-transport.ts';
import type { CommentaryConfig } from './commentary.ts';

/** config.json 的 `worlds.vtuber` 节。 */
export interface VtuberConfigSection {
  speechVocabularyFile: string;
  enabled: boolean;
  vtsWsUrl: string;
  streamPort: number;
  ttsUrl: string;
  ttsTransport: TtsTransportConfig;
  ttsService: import('./tts-service.ts').TtsServiceConfig;
  ttsSpeech: import('./tts-speech.ts').TtsSpeechPreferences;
  ttsRuntimeDir: string;
  ttsRuntimeRelease: string;
  ttsBaseLmFile: string;
  ttsAcousticFile: string;
  ttsAlignerLmFile: string;
  ttsAlignerAudioFile: string;
  ttsVoicesDir: string;
  live2dDir: string;
  /** 演出包目录;空 = bot 目录下的 `vtuber-pack/`,没有则用随 World 的范例包。 */
  packDir: string;
  /** 禁播词(| 分隔多条);台本流里出现的子串整段不进 TTS、不上字幕。 */
  mutedTexts: string;
  audioDevice: string;
  audioMirrorSystem: boolean;
  audioSecondary: string;
  musicDir: string;
  musicVolume: number;
  musicGenerationUrl: string;
  waitingMedia: import('./waiting-media.ts').WaitingMediaConfig;
  alignEnabled: boolean;
  streamEnabled: boolean;
  incrementalSpeech: boolean;
  speechCapSec: number;
  speechPlanningLeadSec: number;
  maxActRoundsPerTurn: number;
  silenceRemindSec: number;
  silenceRemind2Sec: number;
  silenceRemind3Sec: number;
  silenceLine1: string;
  silenceLine2: string;
  silenceLine3: string;
  commentary: CommentaryConfig;
  obsDelaySec: number;
  delayedSources: string[];
  yieldWindowMs: number;
  yieldFadeMs: number;
  decayGazeSec: [number, number];
  decayPoseSec: [number, number];
  decayEmotionSec: [number, number];
  modelProfile: string;
  ttsProfile: TtsProfile;
  overlay: OverlayConfig;
}

export const VTUBER: WorldDefinition<VtuberConfigSection> = {
  id: 'vtuber',
  label: 'VTuber 演出',
  describe: {
    tools: describeTools(VTUBER_TOOL_DECLS, {
      vtuber_act: { zh: '将台词和演出提示送入语音与动作队列。', en: 'Queues speech and performance cues.' },
      vtuber_interrupt: { zh: '在自然停顿处结束当前演出及排队语音。', en: 'Stops performance and queued speech at a natural pause.' },
      vtuber_music: { zh: '检索、播放和停止音乐，提交或取消异步创作。', en: 'Lists, plays and stops music and submits or cancels asynchronous composition.' },
    }),
    events: [
      { type: 'worlds.note', description: { zh: '演出生命周期、交接或服务状态通知。', en: 'Performance lifecycle, handoff or service status.' } },
      { type: 'vtuber.status', description: { zh: '当前演出队列与播放状态。', en: 'Current performance queue and playback state.' } },
      { type: 'vtuber.speech.window', tags: ['snapshot'], description: { zh: '语音播放期间的一次提前准备窗口；宿主决定是否继续思考，不表示播放结束或强制发言。', en: 'One advance preparation window during speech; the host owns cognition. Not playback completion or a request to force speech.' } },
      { type: 'vtuber.composition', description: { zh: '异步音乐创作的审核、生成或失败结果。', en: 'Review, generation or failure result of an asynchronous composition.' } },
      { type: 'vtuber.music', description: { zh: '实际音乐播放或停止的反馈。', en: 'Actual music playback or stop feedback.' } },
      { type: 'vtuber.waiting', description: { zh: '等待媒体的实际播放反馈。', en: 'Actual waiting-media playback feedback.' } },
      { type: 'vtuber.act.outcome', description: { zh: '台词与演出实际完成、取消或失败的回执。', en: 'Actual completion, cancellation or failure receipt for speech and performance.' } },
      { type: 'vtuber.speech.submitted', description: { zh: '主线程或解说通道已提交的台词及来源；不证明播放完成或观众端接收。', en: 'Speech admitted from the foreground or commentary lane, with provenance; not proof of completed playback or audience reception.' } },
      { type: 'vtuber.danmaku', description: { zh: '演出收到的观众弹幕。', en: 'Audience comments received by the performance.' } },
    ],
  },
  defaults: () => ({
    ...structuredClone(VTUBER_DEFAULTS as unknown as VtuberConfigSection),
    ttsProfile: { ...TTS_PROFILE_DEFAULTS },
    overlay: structuredClone(OVERLAY_CONFIG_DEFAULTS),
  }),
  // 播放设备下拉的整张表(含"系统默认 / 不出声"固定项)。枚举本机声卡要 audify,那是本包的
  // 依赖;World 自报,宿主与 bot 都不替它答,控制台也不认识这两个 kind。
  configOptions: (kind, language) => playbackConfigOptions(kind, language),
  create: (ctx) => {
    const { cfg } = ctx;
    // 演出包三层,与环境提示词同一套规则:部署覆盖 > 人格包自带 > World 范例(packDir 为空时)。
    // `cfg.packDir` 是部署配置里手填的绝对路径,压过一切——指到仓库外的包时用它。
    const packDir = cfg.packDir.trim()
      || [join(ctx.botDir, 'vtuber-pack'), join(ctx.packageDir, 'vtuber-pack')].find((d) => existsSync(d))
      || '';
    // The child process isolates 60 Hz avatar updates from synchronous work in the main loop.
    return new VtuberWorldProxy({
      timezone: ctx.timezone,
      botName: ctx.botName,
      vtsWsUrl: cfg.vtsWsUrl,
      streamPort: cfg.streamPort,
      ttsUrl: cfg.ttsUrl,
      ttsTransport: { ...TTS_TRANSPORT_DEFAULTS, ...cfg.ttsTransport,
        sessionId: cfg.ttsTransport?.sessionId || `vtuber-${createHash('sha256').update(ctx.dataDir).digest('hex').slice(0, 24)}` },
      ttsApiKey: ctx.secret(TTS_API_KEY_SECRET),
      speechVocabularyFile: cfg.speechVocabularyFile ?? '',
      ttsService: cfg.ttsService,
      ttsSpeech: () => cfg.ttsSpeech ?? VTUBER_DEFAULTS.ttsSpeech,
      ttsRuntimeDir: () => cfg.ttsRuntimeDir,
      ttsRuntimeRelease: () => cfg.ttsRuntimeRelease,
      ttsBaseLmFile: () => cfg.ttsBaseLmFile,
      ttsAcousticFile: () => cfg.ttsAcousticFile,
      ttsAlignerLmFile: () => cfg.ttsAlignerLmFile,
      ttsAlignerAudioFile: () => cfg.ttsAlignerAudioFile,
      ttsVoicesDir: () => cfg.ttsVoicesDir,
      live2dDir: () => cfg.live2dDir,
      packDir,
      audioDevice: () => cfg.audioDevice,
      audioMirrorSystem: () => cfg.audioMirrorSystem,
      audioSecondary: () => cfg.audioSecondary ?? VTUBER_DEFAULTS.audioSecondary,
      musicDir: () => cfg.musicDir ?? VTUBER_DEFAULTS.musicDir,
      musicVolume: () => cfg.musicVolume ?? VTUBER_DEFAULTS.musicVolume,
      musicGenerationUrl: () => cfg.musicGenerationUrl ?? VTUBER_DEFAULTS.musicGenerationUrl,
      waitingMedia: () => ({ ...VTUBER_DEFAULTS.waitingMedia, ...cfg.waitingMedia }),
      commentary: () => ({ ...VTUBER_DEFAULTS.commentary, ...cfg.commentary }),
      alignEnabled: () => cfg.alignEnabled,
      streamEnabled: () => cfg.streamEnabled,
      incrementalSpeech: () => cfg.incrementalSpeech,
      speechCapSec: () => cfg.speechCapSec,
      speechPlanningLeadSec: () => cfg.speechPlanningLeadSec ?? VTUBER_DEFAULTS.speechPlanningLeadSec,
      maxActRoundsPerTurn: () => cfg.maxActRoundsPerTurn,
      silenceRemindSec: () => cfg.silenceRemindSec,
      silenceRemind2Sec: () => cfg.silenceRemind2Sec,
      silenceRemind3Sec: () => cfg.silenceRemind3Sec,
      silenceLines: () => [cfg.silenceLine1, cfg.silenceLine2, cfg.silenceLine3],
      mutedText: () => cfg.mutedTexts,
      obsDelaySec: () => cfg.obsDelaySec,
      delayedSources: () => cfg.delayedSources,
      yieldWindowMs: () => cfg.yieldWindowMs,
      yieldFadeMs: () => cfg.yieldFadeMs,
      decaySec: () => ({ gaze: cfg.decayGazeSec, pose: cfg.decayPoseSec, emotion: cfg.decayEmotionSec }),
      modelProfile: () => cfg.modelProfile,
      onModelProfile: (profile) => ctx.persist({ modelProfile: profile }),
      diagDir: join(ctx.dataDir, 'vtuber-diag'),
      ttsProfile: cfg.ttsProfile,
      onTtsProfile: (profile) => ctx.persist({ ttsProfile: profile }),
      overlay: cfg.overlay,
      onOverlayConfig: (config) => ctx.persist({ overlay: config }),
      vtsAuthToken: ctx.secret(VTUBER_SECRET),
      onVtsToken: (token) => ctx.storeSecret(VTUBER_SECRET, token),
    });
  },
};
