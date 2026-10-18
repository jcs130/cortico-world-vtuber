import { createHash } from 'node:crypto';
import { readFileSync, statSync } from 'node:fs';

/** Optional Agent-side presentation fallback. Never sent as TTS configuration. */
export interface SpeechVocabularyDocument {
  schema: 'speech-vocabulary-v1';
  language?: string;
  source?: unknown;
  terms: Record<string, string>;
  /** Host-supplied proper names that collide with domain words. */
  preserve?: string[];
}
export interface SpeechTextRendering {
  text: string;
  /** Map a source UTF-16 boundary to its position in the rendered subtitle. */
  mapOffset(offset: number): number;
}
export type SpeechTextRenderer = (text: string) => SpeechTextRendering;

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const protectedText = /```[\s\S]*?```|`[^`\n]*`|<[^<>\n]*>|\{[^{}\n]*\}|\[[^\[\]\n]*\]|https?:\/\/[^\s<>，。！？；、]+|[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}|[A-Za-z]:[\\/][^\s，。！？；、]+|(?<![A-Za-z0-9_])\/[A-Za-z][^\r\n，。！？；、]*|@[A-Za-z0-9_]+|(?:玩家|账号|用户名|昵称|(?<![A-Za-z0-9_])(?:ID|id)(?![A-Za-z0-9_])|名叫|我叫|叫作|叫做)\s*[:：]?\s*["“‘]?[A-Za-z0-9_]+(?![A-Za-z0-9_])/gu;

/** No default vocabulary, model, game dependency, or network lookup. */
export class SpeechVocabulary {
  readonly entries: number;
  private readonly terms = new Map<string, string>();
  private readonly pattern: RegExp;
  private readonly preserve: Set<string>;
  constructor(body: SpeechVocabularyDocument) {
    if (!body || body.schema !== 'speech-vocabulary-v1' || !body.terms || Array.isArray(body.terms)
      || typeof body.terms !== 'object') throw Error('Unsupported speech vocabulary');
    const entries = Object.entries(body.terms);
    if (entries.length < 1 || entries.length > 10000) throw Error('Speech vocabulary requires 1–10000 entries');
    for (const [key, value] of entries) {
      if (!/^[A-Za-z0-9][A-Za-z0-9 _:'.-]{0,127}$/.test(key) || typeof value !== 'string'
        || !value.trim() || value.length > 128 || /[<>\[\]{}\r\n]/u.test(value)) throw Error('Invalid plain speech term');
      const canonical = key.toLowerCase();
      if (this.terms.has(canonical) && this.terms.get(canonical) !== value) throw Error('Conflicting speech terms');
      this.terms.set(canonical, value);
    }
    if (body.preserve !== undefined && (!Array.isArray(body.preserve)
      || body.preserve.some(value => typeof value !== 'string' || value.length > 128))) throw Error('Invalid preserved names');
    this.preserve = new Set((body.preserve ?? []).map(value => value.toLowerCase()));
    this.entries = this.terms.size;
    this.pattern = new RegExp("(?<![A-Za-z0-9_:.@/\\\\'-])(" + [...this.terms.keys()].sort((a, b) => b.length - a.length)
      .map(escape).join('|') + ")(?!(?:[A-Za-z0-9_:/\\\\'-]|\\.[A-Za-z0-9_]))([ \\t]*方块)?", 'gi');
  }
  readonly render: SpeechTextRenderer = original => {
    const edits: Array<{ start: number; end: number; text: string }> = [];
    let end = 0;
    const scan = (text: string, base: number) => {
      for (const match of text.matchAll(this.pattern)) {
        if (this.preserve.has(match[1].toLowerCase())) continue;
        const translated = this.terms.get(match[1].toLowerCase())!;
        const suffix = match[2] ?? '';
        edits.push({ start: base + match.index!, end: base + match.index! + match[0].length,
          text: translated + (suffix && translated.endsWith('块') ? '' : suffix) });
      }
    };
    for (const span of original.matchAll(protectedText)) {
      scan(original.slice(end, span.index), end);
      end = span.index! + span[0].length;
    }
    scan(original.slice(end), end);
    let text = '', at = 0;
    for (const edit of edits) { text += original.slice(at, edit.start) + edit.text; at = edit.end; }
    text += original.slice(at);
    return { text, mapOffset: offset => {
      const boundary = Math.max(0, Math.min(original.length, offset));
      let delta = 0;
      for (const edit of edits) {
        if (boundary <= edit.start) break;
        if (boundary < edit.end) return edit.start + delta + Math.round((boundary - edit.start) * edit.text.length / (edit.end - edit.start));
        delta += edit.text.length - (edit.end - edit.start);
      }
      return boundary + delta;
    } };
  };
}

/** Explicit local file only; validation failures are visible, never a remote fallback. */
export function loadSpeechVocabulary(file: string): { vocabulary: SpeechVocabulary; sha256: string } {
  if (statSync(file).size > 2 * 1024 * 1024) throw Error('Speech vocabulary exceeds 2 MiB');
  const bytes = readFileSync(file);
  return { vocabulary: new SpeechVocabulary(JSON.parse(bytes.toString('utf8').replace(/^\uFEFF/, ''))),
    sha256: createHash('sha256').update(bytes).digest('hex') };
}
