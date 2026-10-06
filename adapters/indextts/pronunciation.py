"""Phrase-scoped IndexTTS 2.5 pronunciation hints; never rewrite subtitles."""

import re


# Only unambiguous phrases. In particular, 长得 and bare 长 need sentence context.
# Longest matching phrase wins: 长大衣 must not inherit the 长大 growth reading.
_READINGS = {
    **dict.fromkeys((
        '长大', '成长', '生长', '长高', '长胖', '长个子', '长成',
        '长出', '长草', '长肉', '长叶', '长苗', '长根', '长芽', '长势',
    ), 'ZHANG3'),
    **dict.fromkeys((
        '长短', '长度', '长久', '长远', '长期', '长途', '长线', '长方形',
        '长条', '长剑', '长弓', '长枪', '长杆', '长河', '长夜', '长时间',
        '长城', '长江', '长大衣',
    ), 'CHANG2'),
}
_PHRASES = re.compile('|'.join(re.escape(word) for word in sorted(_READINGS, key=len, reverse=True)))
# Preserve explicit caller pronunciation, literal markup, code and URLs. A second
# pass must not annotate the character inside a previously inserted hint.
_PROTECTED = re.compile(r'<[^<>\n]*>|`[^`\n]*`|https?://[^\s<>，。！？；]+')


def normalize_pronunciation(text: str) -> str:
    """Annotate only the polyphonic character in known phrases, idempotently."""
    def annotate(match):
        phrase = match.group(0)
        return phrase.replace('长', f'<长|{_READINGS[phrase]}>', 1)

    output = []
    start = 0
    for protected in _PROTECTED.finditer(text):
        output.append(_PHRASES.sub(annotate, text[start:protected.start()]))
        output.append(protected.group(0))
        start = protected.end()
    output.append(_PHRASES.sub(annotate, text[start:]))
    return ''.join(output)
