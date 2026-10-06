/** Deployment-owned, pre-generated WAVs. Track ids are the only playback input. */
import { readFile, realpath, stat } from 'node:fs/promises';
import { isAbsolute, relative, resolve } from 'node:path';
import { decodeWav, extractEnvelope, type TtsPiece } from './tts.ts';
import type { MusicGenerationState } from './music-generation.ts';

export type MusicStatus = 'queued' | 'playing' | 'ended' | 'stopped' | 'failed';
export interface MusicLyricLine {
  atMs: number;
  endMs: number;
  text: string;
}
export interface MusicPlayOptions { intro?: string; outro?: string }
export interface MusicTrack {
  id: string;
  title: string;
  durationMs: number;
  envelopeSource: 'vocal' | 'mix';
  description?: string;
  aiGenerated?: boolean;
  lyrics?: MusicLyricLine[];
}
export interface MusicPlayback {
  kind: 'music';
  playbackId: string;
  trackId: string;
  title: string;
  status: MusicStatus;
  durationMs: number;
  startedAt: number | null;
  volume: number;
  envelopeSource: 'vocal' | 'mix';
  error?: string;
  aiGenerated?: boolean;
  lyrics?: MusicLyricLine[];
}
export interface MusicQueueState {
  current: MusicPlayback | null;
  queue: MusicPlayback[];
  last: MusicPlayback | null;
}
export interface MusicState extends MusicQueueState {
  tracks: MusicTrack[];
  volume: number;
  error?: string;
  generation?: MusicGenerationState;
}
interface CatalogTrack { id: string; title: string; wavFile: string; vocalFile?: string; description?: string; lyricsFile?: string; aiGenerated?: boolean; contentApproved?: boolean; audioValidated?: boolean }
export interface PreparedMusic { track: MusicTrack; audio: TtsPiece }
const LYRICS_MAX_BYTES = 256 * 1024;

export function musicVolume(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0.65;
}

export class MusicLibrary {
  private cache = new Map<string, { stamp: string; track: MusicTrack }>();
  constructor(private readonly directory: () => string) {}

  private async catalog(): Promise<{ root: string; tracks: CatalogTrack[] }> {
    const dir = this.directory().trim();
    if (!dir) return { root: '', tracks: [] };
    const root = await realpath(dir).catch(() => { throw new Error('歌曲目录不可读'); });
    const file = await this.asset(root, 'catalog.json');
    const s = await stat(file);
    if (s.size > 1_048_576) throw new Error('歌曲目录清单过大');
    let data: unknown;
    try { data = JSON.parse(await readFile(file, 'utf8')); } catch { throw new Error('catalog.json 不是有效 JSON'); }
    const c = data as { version?: unknown; tracks?: unknown };
    if (c?.version !== 1 || !Array.isArray(c.tracks) || c.tracks.length > 200) throw new Error('歌曲清单需要 version:1 与 tracks 数组（最多 200 首）');
    const seen = new Set<string>();
    const tracks = c.tracks.map((row: unknown) => {
      const t = row as CatalogTrack;
      if (!t || typeof t.id !== 'string' || !/^[\p{L}\p{N}_-]{1,100}$/u.test(t.id)
        || seen.has(t.id) || typeof t.title !== 'string' || !t.title.trim() || t.title.length > 200
        || typeof t.wavFile !== 'string' || (t.vocalFile !== undefined && typeof t.vocalFile !== 'string')
        || (t.lyricsFile !== undefined && typeof t.lyricsFile !== 'string')
        || (t.aiGenerated !== undefined && typeof t.aiGenerated !== 'boolean')
        || (t.aiGenerated === true && (t.contentApproved !== true || t.audioValidated !== true))
        || (t.description !== undefined && (typeof t.description !== 'string' || t.description.length > 2000))) {
        throw new Error('歌曲清单存在无效或重复曲目');
      }
      seen.add(t.id);
      return t;
    });
    return { root, tracks };
  }

  private async asset(root: string, file: string): Promise<string> {
    if (!file || isAbsolute(file) || file.includes('\0')) throw new Error('歌曲资产必须使用目录内相对路径');
    const candidate = resolve(root, file);
    const within = (path: string): boolean => { const r = relative(root, path); return r !== '..' && !r.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) && !isAbsolute(r); };
    if (!within(candidate)) throw new Error('歌曲资产路径越出目录');
    const actual = await realpath(candidate).catch(() => { throw new Error('歌曲资产不存在'); });
    if (!within(actual)) throw new Error('歌曲资产链接越出目录');
    return actual;
  }

  private async wav(root: string, file: string): Promise<Uint8Array> {
    if (!/\.wav$/i.test(file)) throw new Error('歌曲资产必须是 WAV');
    const actual = await this.asset(root, file);
    const s = await stat(actual);
    if (!s.isFile() || s.size < 44 || s.size > 268_435_456) throw new Error('歌曲 WAV 大小无效（上限 256 MiB）');
    return readFile(actual);
  }

  private decode(bytes: Uint8Array) {
    try {
      const wav = decodeWav(bytes);
      if (!Number.isFinite(wav.durationMs) || wav.durationMs <= 0 || wav.durationMs > 3_600_000) throw new Error();
      return wav;
    } catch { throw new Error('歌曲 WAV 无效或时长超出 1 小时'); }
  }

  private async lyricsAsset(root: string, file: string): Promise<{ path: string; stamp: string }> {
    if (!/\.json$/i.test(file)) throw new Error('歌词文件必须是 JSON');
    const path = await this.asset(root, file);
    const s = await stat(path);
    if (!s.isFile() || s.size > LYRICS_MAX_BYTES) throw new Error('歌词文件大小无效（上限 256 KiB）');
    return { path, stamp: `${path}:${s.size}:${s.mtimeMs}` };
  }

  private async lyrics(path: string, durationMs: number): Promise<MusicLyricLine[]> {
    const bytes = await readFile(path);
    if (bytes.byteLength > LYRICS_MAX_BYTES) throw new Error('歌词文件大小无效（上限 256 KiB）');
    let data: unknown;
    try { data = JSON.parse(bytes.toString('utf8')); } catch { throw new Error('歌词文件不是有效 JSON'); }
    const doc = data as { version?: unknown; lines?: unknown };
    if (!doc || typeof doc !== 'object' || Array.isArray(doc)
      || Object.keys(doc).some((key) => key !== 'version' && key !== 'lines')
      || doc.version !== 1 || !Array.isArray(doc.lines) || doc.lines.length > 500) {
      throw new Error('歌词文件需要 version:1 与 lines 数组（最多 500 行）');
    }
    let previousEnd = 0;
    return doc.lines.map((row: unknown) => {
      const line = row as MusicLyricLine;
      if (!line || typeof line !== 'object' || Array.isArray(line)
        || Object.keys(line).some((key) => !['atMs', 'endMs', 'text'].includes(key))
        || !Number.isInteger(line.atMs) || !Number.isInteger(line.endMs)
        || line.atMs < 0 || line.endMs <= line.atMs || line.endMs > durationMs
        || typeof line.text !== 'string' || !line.text.trim() || line.text.length > 300
        || /[\u0000-\u001f\u007f-\u009f]/.test(line.text)) {
        throw new Error('歌词行需要有效整数时间与非空文本（最多 300 字符、无控制符），结束时间不得超出歌曲时长');
      }
      if (line.atMs < previousEnd) throw new Error('歌词行必须按起始时间排序且不得重叠');
      previousEnd = line.endMs;
      return { atMs: line.atMs, endMs: line.endMs, text: line.text };
    });
  }

  private copyTrack(track: MusicTrack): MusicTrack {
    return { ...track, ...(track.lyrics !== undefined ? { lyrics: track.lyrics.map((line) => ({ ...line })) } : {}) };
  }

  async list(): Promise<MusicTrack[]> {
    const { root, tracks } = await this.catalog();
    const result: MusicTrack[] = [];
    for (const t of tracks) {
      const p = await this.asset(root, t.wavFile);
      const s = await stat(p);
      const lyricsAsset = t.lyricsFile !== undefined ? await this.lyricsAsset(root, t.lyricsFile) : undefined;
      const stamp = `${p}:${s.size}:${s.mtimeMs}:${t.title}:${t.description ?? ''}:${t.vocalFile ?? ''}:${lyricsAsset?.stamp ?? ''}:${t.aiGenerated ?? ''}`;
      const cached = this.cache.get(t.id);
      if (cached?.stamp === stamp) { result.push(this.copyTrack(cached.track)); continue; }
      const wav = this.decode(await this.wav(root, t.wavFile));
      const lyrics = lyricsAsset ? await this.lyrics(lyricsAsset.path, wav.durationMs) : undefined;
      const track: MusicTrack = { id: t.id, title: t.title, durationMs: wav.durationMs, envelopeSource: t.vocalFile ? 'vocal' : 'mix', ...(t.description ? { description: t.description } : {}), ...(lyrics !== undefined ? { lyrics } : {}), ...(t.aiGenerated === true ? { aiGenerated: true } : {}) };
      this.cache.set(t.id, { stamp, track });
      result.push(this.copyTrack(track));
    }
    return result;
  }

  async prepare(trackId: string): Promise<PreparedMusic> {
    const { root, tracks } = await this.catalog();
    const t = tracks.find((row) => row.id === trackId);
    if (!t) throw new Error('曲库没有这个 trackId');
    const bytes = await this.wav(root, t.wavFile);
    const wav = this.decode(bytes);
    const vocal = t.vocalFile ? this.decode(await this.wav(root, t.vocalFile)) : wav;
    if (Math.abs(vocal.durationMs - wav.durationMs) > 100) throw new Error('vocalFile 必须与歌曲同步且时长相差不超过 100ms');
    const lyricsAsset = t.lyricsFile !== undefined ? await this.lyricsAsset(root, t.lyricsFile) : undefined;
    const lyrics = lyricsAsset ? await this.lyrics(lyricsAsset.path, wav.durationMs) : undefined;
    const track: MusicTrack = { id: t.id, title: t.title, durationMs: wav.durationMs, envelopeSource: t.vocalFile ? 'vocal' : 'mix', ...(t.description ? { description: t.description } : {}), ...(lyrics !== undefined ? { lyrics } : {}), ...(t.aiGenerated === true ? { aiGenerated: true } : {}) };
    return { track, audio: { text: '', wav: bytes, durationMs: wav.durationMs, envelope: extractEnvelope(vocal) } };
  }
}
