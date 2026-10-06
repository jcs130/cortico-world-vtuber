import { afterEach, describe, expect, it } from 'vitest';
import { mkdir, mkdtemp, rm, stat, symlink, utimes, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { MusicLibrary, type MusicLyricLine } from '../../src/music.ts';
import { pcm16ToWav } from '../../src/tts.ts';

const directories: string[] = [];
const durationMs = 1200;
const line: MusicLyricLine = { atMs: 0, endMs: durationMs, text: '挥剑向前' };

afterEach(async () => {
  for (const directory of directories.splice(0)) await rm(directory, { recursive: true, force: true });
});

async function fixture(lyricsFile: unknown = 'lyrics.json') {
  const root = await mkdtemp(join(tmpdir(), 'vtuber-music-lyrics-'));
  directories.push(root);
  const dir = join(root, 'library');
  await mkdir(dir);
  const track = { id: 'song', title: '战斗歌', wavFile: 'song.wav', ...(lyricsFile !== undefined ? { lyricsFile } : {}) };
  await writeFile(join(dir, 'catalog.json'), JSON.stringify({ version: 1, tracks: [track] }));
  const samples = new Int16Array(16_000 * durationMs / 1000);
  await writeFile(join(dir, 'song.wav'), pcm16ToWav([new Uint8Array(samples.buffer)], 16_000));
  const file = join(dir, 'lyrics.json');
  await writeFile(file, JSON.stringify({ version: 1, lines: [line] }));
  return { root, dir, file, library: new MusicLibrary(() => dir) };
}

async function expectRejected(library: MusicLibrary, error?: string) {
  await expect(library.list()).rejects.toThrow(error);
  await expect(library.prepare('song')).rejects.toThrow(error);
}

describe('deployment music lyrics', () => {
  it('lists and prepares optional timed lyrics using the actual WAV duration', async () => {
    const { dir, file, library } = await fixture('text/lyrics.json');
    await mkdir(join(dir, 'text'));
    const lines = [{ atMs: 0, endMs: 300, text: '  举剑  ' }, { atMs: 300, endMs: durationMs, text: '向前冲' }];
    await writeFile(join(dir, 'text', 'lyrics.json'), JSON.stringify({ version: 1, lines }));
    expect((await library.list())[0]).toEqual({ id: 'song', title: '战斗歌', durationMs, envelopeSource: 'mix', lyrics: lines });
    const prepared = await library.prepare('song');
    expect(prepared.track.lyrics).toEqual(lines);
    expect(prepared.audio.durationMs).toBe(durationMs);
    expect(prepared.audio.text).toBe('');
    await rm(file);
    expect((await library.list())[0].lyrics).toEqual(lines);
  });

  it('omits the lyrics property when the catalog has no lyrics file', async () => {
    const { dir, library } = await fixture();
    await writeFile(join(dir, 'catalog.json'), JSON.stringify({ version: 1, tracks: [{ id: 'song', title: '战斗歌', wavFile: 'song.wav' }] }));
    expect((await library.list())[0]).not.toHaveProperty('lyrics');
    expect((await library.prepare('song')).track).not.toHaveProperty('lyrics');
  });

  it('accepts the inclusive line count, text length and file size limits', async () => {
    const { file, library } = await fixture();
    const lines = Array.from({ length: 500 }, (_, index) => ({ atMs: index, endMs: index + 1, text: index === 0 ? '剑'.repeat(300) : '冲锋' }));
    const json = JSON.stringify({ version: 1, lines });
    const bytes = Buffer.from(json, 'utf8');
    await writeFile(file, Buffer.concat([bytes, Buffer.alloc(256 * 1024 - bytes.length, 32)]));
    expect((await library.list())[0].lyrics).toEqual(lines);
    expect((await library.prepare('song')).track.lyrics).toEqual(lines);
  });

  it.each([
    ['negative start', [{ ...line, atMs: -1 }]],
    ['fractional start', [{ ...line, atMs: 0.5 }]],
    ['fractional end', [{ ...line, endMs: 100.5 }]],
    ['string time', [{ ...line, atMs: '0' }]],
    ['empty interval', [{ ...line, atMs: 100, endMs: 100 }]],
    ['backwards interval', [{ ...line, atMs: 100, endMs: 99 }]],
    ['after WAV duration', [{ ...line, endMs: durationMs + 1 }]],
    ['missing start', [{ endMs: 200, text: '冲锋' }]],
    ['missing end', [{ atMs: 0, text: '冲锋' }]],
    ['blank text', [{ ...line, text: '   ' }]],
    ['non-string text', [{ ...line, text: 1 }]],
    ['oversized text', [{ ...line, text: '剑'.repeat(301) }]],
    ['newline', [{ ...line, text: '向前\n冲' }]],
    ['null control', [{ ...line, text: '向前\u0000冲' }]],
    ['delete control', [{ ...line, text: '向前\u007f冲' }]],
    ['C1 control', [{ ...line, text: '向前\u0085冲' }]],
    ['extra line field', [{ ...line, singer: 'hero' }]],
    ['null row', [null]],
    ['array row', [[0, 100, '冲锋']]],
    ['too many lines', Array.from({ length: 501 }, (_, index) => ({ atMs: index, endMs: index + 1, text: '冲锋' }))],
  ])('rejects %s in both list and prepare', async (_label, lines) => {
    const { file, library } = await fixture();
    await writeFile(file, JSON.stringify({ version: 1, lines }));
    await expectRejected(library);
  });

  it.each([
    ['unsorted', [{ atMs: 200, endMs: 300, text: '第二句' }, { atMs: 0, endMs: 100, text: '第一句' }]],
    ['overlap', [{ atMs: 0, endMs: 300, text: '第一句' }, { atMs: 299, endMs: 400, text: '第二句' }]],
    ['duplicate start', [{ atMs: 0, endMs: 100, text: '第一句' }, { atMs: 0, endMs: 200, text: '第二句' }]],
  ])('rejects %s without rearranging the timing', async (_label, lines) => {
    const { file, library } = await fixture();
    await writeFile(file, JSON.stringify({ version: 1, lines }));
    await expectRejected(library, '排序且不得重叠');
  });

  it.each([
    ['invalid JSON', '{broken'],
    ['unsupported version', JSON.stringify({ version: 2, lines: [line] })],
    ['non-array lines', JSON.stringify({ version: 1, lines: line })],
    ['extra document field', JSON.stringify({ version: 1, lines: [line], durationMs: 9999 })],
    ['null document', 'null'],
    ['array document', '[]'],
  ])('rejects %s documents', async (_label, content) => {
    const { file, library } = await fixture();
    await writeFile(file, content);
    await expectRejected(library);
  });

  it('rejects files over 256 KiB before parsing them', async () => {
    const { file, library } = await fixture();
    await writeFile(file, Buffer.alloc(256 * 1024 + 1, 32));
    await expectRejected(library, '256 KiB');
  });

  it.each([
    ['traversal', '../lyrics.json', '越出目录'],
    ['absolute path', join(tmpdir(), 'lyrics.json'), '相对路径'],
    ['wrong extension', 'lyrics.txt', 'JSON'],
    ['null path byte', 'lyrics\u0000.json', '相对路径'],
    ['non-string path', 123, '无效'],
  ])('rejects %s lyric paths', async (_label, path, error) => {
    const { library } = await fixture(path);
    await expectRejected(library, error);
  });

  it('rejects real paths that escape through a directory link', async () => {
    const { root, dir, library } = await fixture('escape/lyrics.json');
    const outside = join(root, 'outside');
    await mkdir(outside);
    await writeFile(join(outside, 'lyrics.json'), JSON.stringify({ version: 1, lines: [line] }));
    await symlink(outside, join(dir, 'escape'), process.platform === 'win32' ? 'junction' : 'dir');
    await expectRejected(library, '链接越出目录');
  });

  it('refreshes cached lyrics when mtime changes even if size stays the same', async () => {
    const { file, library } = await fixture();
    expect((await library.list())[0].lyrics).toEqual([line]);
    const before = await stat(file);
    const changed = { ...line, text: '出发冲锋' };
    await writeFile(file, JSON.stringify({ version: 1, lines: [changed] }));
    await utimes(file, before.atime, new Date(before.mtimeMs + 10_000));
    expect((await stat(file)).size).toBe(before.size);
    expect((await library.list())[0].lyrics).toEqual([changed]);
  });

  it('refreshes cached lyrics when size changes even if mtime stays the same', async () => {
    const { file, library } = await fixture();
    await library.list();
    const before = await stat(file);
    const changed = { ...line, text: '挥剑向前绝不后退' };
    await writeFile(file, JSON.stringify({ version: 1, lines: [changed] }));
    await utimes(file, before.atime, before.mtime);
    expect((await stat(file)).size).not.toBe(before.size);
    expect((await library.list())[0].lyrics).toEqual([changed]);
  });

  it('revalidates changed files and shortened WAV duration after caching', async () => {
    const { dir, file, library } = await fixture();
    await library.list();
    await writeFile(file, JSON.stringify({ version: 1, lines: [{ ...line, endMs: durationMs + 100 }] }));
    await expectRejected(library, '歌曲时长');
    await writeFile(file, JSON.stringify({ version: 1, lines: [line] }));
    await library.list();
    const samples = new Int16Array(8000);
    await writeFile(join(dir, 'song.wav'), pcm16ToWav([new Uint8Array(samples.buffer)], 16_000));
    await expectRejected(library, '歌曲时长');
  });

  it('returns isolated lyric arrays so callers cannot invalidate cached metadata', async () => {
    const { library } = await fixture();
    const first = (await library.list())[0];
    first.lyrics![0].text = '';
    first.lyrics!.push({ atMs: -1, endMs: 5000, text: 'invalid' });
    expect((await library.list())[0].lyrics).toEqual([line]);
    expect((await library.prepare('song')).track.lyrics).toEqual([line]);
  });
});
