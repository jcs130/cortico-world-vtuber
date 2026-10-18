"""Explicit per-voice domain vocabulary. No Minecraft dependency or network lookup."""
import hashlib
import json
from pathlib import Path
import re

POLICY = 'configured-spoken-terms-v1'
_PROTECTED = re.compile(
    r'```[\s\S]*?```|`[^`\n]*`|<[^<>\n]*>|\{[^{}\n]*\}|\[[^\[\]\n]*\]'
    r'|https?://[^\s<>，。！？；]+'
    r'|[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'
    r'|[A-Za-z]:[\\/][^\s，。！？；]+'
    r'|(?<![A-Za-z0-9_])/[A-Za-z][^\r\n，。！？；]*'
    r'|@[A-Za-z0-9_]+'
    r'|(?:玩家|账号|用户名|昵称|ID|id|名叫|我叫|叫作|叫做)\s*[:：]?\s*[\"“‘]?'
    r'[A-Za-z0-9_]+(?![A-Za-z0-9_])')


class SpokenTerms:
    def __init__(self, body):
        if not isinstance(body, dict) or body.get('schema') != 'tts-spoken-terms-v1':
            raise ValueError('Unsupported spoken term catalog schema')
        terms = body.get('terms')
        if not isinstance(terms, dict) or not 1 <= len(terms) <= 10000:
            raise ValueError('Spoken term catalog requires 1 to 10000 entries')
        self.terms = {}
        for key, value in terms.items():
            if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 _:.-]{0,127}', key)
                    or not isinstance(value, str) or not value.strip() or len(value) > 128
                    or re.search(r'[<>\[\]{}\r\n]', value)):
                raise ValueError('Spoken terms must map readable ASCII tokens to plain speech text')
            canonical = key.lower()
            if canonical in self.terms and self.terms[canonical] != value:
                raise ValueError('Conflicting case-insensitive spoken term')
            self.terms[canonical] = value
        self.pattern = re.compile(r'(?<![A-Za-z0-9_:.@/\\-])(?P<term>' + '|'.join(
            re.escape(key) for key in sorted(self.terms, key=len, reverse=True))
            + r')(?!(?:[A-Za-z0-9_:/\\-]|\.[A-Za-z0-9_]))(?P<classifier>[ \t]*方块)?', re.I | re.ASCII)

    def _replace(self, match):
        translated = self.terms[match['term'].lower()]
        classifier = match['classifier'] or ''
        return translated if classifier and translated.endswith('块') else translated + classifier

    def normalize(self, text):
        pieces, end = [], 0
        for span in _PROTECTED.finditer(text):
            pieces.extend((self.pattern.sub(self._replace, text[end:span.start()]), span[0]))
            end = span.end()
        pieces.append(self.pattern.sub(self._replace, text[end:]))
        return ''.join(pieces)


def load_catalog(path):
    raw = Path(path).read_bytes()
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError('Spoken term catalog exceeds 2 MiB')
    catalog = SpokenTerms(json.loads(raw.decode('utf-8-sig')))
    return catalog, {'sha256': hashlib.sha256(raw).hexdigest(), 'entries': len(catalog.terms)}
