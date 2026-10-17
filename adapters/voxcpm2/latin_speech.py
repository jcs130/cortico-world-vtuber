"""Spoken Latin words and identifiers; no player dictionary or extra model call."""
from __future__ import annotations

import re

POLICY = 'latin-words-and-initialisms-v1'
# These are conventional letter-by-letter abbreviations, not names of players.
# Pronounceable acronyms such as NASA, LAN and RAM deliberately follow word rules.
INITIALISMS = frozenset('''
AFK AI AOE API ASCII ASR CEO CFO CLI COO CPU CTO DNA DNS FPS GPU GUI HDMI HP HTML HTTP HTTPS
ID IO IP JSON LLM MCP MMO MMORPG MRI NPC OBS OCR OS PC PDF PNG PVE QA QQ QR RGB RNA RPC SDK SOS SQL SSD
STT TCP TLS TTS UDP UI URL USB UTF UUID VR XML XSS YSM
'''.split())
_DIGITS = '零一二三四五六七八九'
_PROTECTED = re.compile(
    r'```[\s\S]*?```|`[^`\n]*`|<[^<>\n]*>|\{[^{}\n]*\}|\[[^\[\]\n]*\]'
    r'|https?://[^\s<>，。！？；]+'
    r'|[A-Za-z0-9_.+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}'
    r'|[A-Za-z]:[\\/][^\s，。！？；]+'
    r'|(?<![A-Za-z0-9_])/[A-Za-z][^\r\n，。！？；]*'
    r'|(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9_-]*\d\.[A-Za-z0-9.\-]+'
)
_TOKEN = re.compile(r'(?<![A-Za-z0-9_.])[A-Za-z_][A-Za-z0-9_]*(?![A-Za-z0-9_.])')
_CAMEL_INITIALISM = re.compile(r'(?<=[a-z])(?=[A-Z]{2,}(?:\d|$))')
_SUFFIX = re.compile(r'(?P<word>[A-Za-z]+)(?P<digits>\d*)$')


def _word(word: str) -> str:
    # A word's capitalization is not an instruction to spell every letter.
    if (len(word) >= 3 and word.isupper() and word not in INITIALISMS
            and not re.fullmatch('[IVXLCDM]+', word)
            and re.search('[AEIOU]|(?<!^)Y', word)):
        return word.capitalize()
    return word


def _token(match: re.Match) -> str:
    token = match[0]
    # Preserve unknown interleaved product/ID spellings instead of guessing syllables.
    pieces = []
    for component in token.split('_'):
        if not component:
            continue
        if component.isdigit():
            pieces.append(''.join(_DIGITS[int(d)] for d in component))
            continue
        suffix = _SUFFIX.fullmatch(component)
        if suffix is None:
            return token
        words = [_word(word) for word in _CAMEL_INITIALISM.split(suffix['word'])]
        pieces.extend(words)
        if suffix['digits']:
            pieces.append(''.join(_DIGITS[int(d)] for d in suffix['digits']))
    return ' '.join(pieces) if pieces else token


def normalize_latin_speech(text: str) -> str:
    """Only synthesis text changes; callers keep original names, subtitles and IDs.

    Plain pronounceable capitals become word case. Conventional initialisms and
    vowelless tokens keep their spelling. Underscores separate spoken components;
    numeric ID suffixes are digits, never a guessed nickname or a cardinal count.
    Explicit pronunciation, code, commands, URLs, emails and version strings stay
    literal. This deterministic fallback does not claim to infer every proper name.
    """
    pieces, end = [], 0
    for span in _PROTECTED.finditer(text):
        pieces.extend((_TOKEN.sub(_token, text[end:span.start()]), span[0]))
        end = span.end()
    pieces.append(_TOKEN.sub(_token, text[end:]))
    return ''.join(pieces)
