"""Readable Chinese synthesis text; the caller's script and subtitles stay intact."""

import re
from decimal import Decimal

from corti_speech_style import normalize_level_speech


_DIGITS = '零一二三四五六七八九'
_PROTECTED = re.compile(
    r'```[\s\S]*?```|`[^`\n]*`|<[^<>\n]*>|https?://[^\s<>，。！？；]+'
    r'|(?:玩家|账号|用户名|昵称|ID|id|名叫|我叫|叫作|叫做)\s*[:：]?\s*[\"“‘]?'
    r'[A-Za-z0-9_]+(?![A-Za-z0-9_])'
)
_NUMBER = re.compile(r'(?<![A-Za-z0-9_.:/+\-])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_.:/+\-])')
_AGE_PROGRESS = re.compile(
    r'(?<![A-Za-z0-9_])age\s*(?:[=:：]|才|是|还在|还是|到了?|为)?\s*'
    r'(?P<current>\d{1,2})\s*/\s*(?P<maximum>\d{1,2})(?![A-Za-z0-9_./])'
)
_COOLDOWN = re.compile(
    r'(?<![A-Za-z0-9_])(?P<field>cooldownRemainingMs|cooldownMs)\s*[=:：]?\s*'
    r'(?P<ms>\d+(?:\.\d+)?)\s*(?:ms(?![A-Za-z0-9_]))?(?![A-Za-z0-9_.])'
)
_RESOURCE = re.compile(
    r'(?<![A-Za-z0-9_])(?P<field>health|food|mana)\s*[=:：]?\s*'
    r'(?P<current>\d+(?:\.\d+)?)\s*/\s*(?P<maximum>\d+(?:\.\d+)?)(?![A-Za-z0-9_./])'
)
_FIELD_NAMES = {
    'age': '生长阶段', 'moisture': '耕地湿度',
    'health': '生命值', 'food': '饱食度', 'mana': '魔力',
    'durability': '耐久度', 'cooldown': '冷却时间',
    'cooldownRemainingMs': '剩余冷却毫秒', 'cooldownMs': '总冷却毫秒',
}
_FIELD = re.compile(r'(?<![A-Za-z0-9_])(?P<field>' + '|'.join(
    sorted(_FIELD_NAMES, key=len, reverse=True)) + r')(?![A-Za-z0-9_])(?P<assignment>\s*[=:：]\s*)?')


def chinese_number(token: str) -> str:
    """Cardinals, signed decimals and digit sequences with leading zeroes."""
    sign = ''
    if token[0] in '+-':
        sign, token = ('负' if token[0] == '-' else '正'), token[1:]
    whole, dot, fraction = token.partition('.')
    if len(whole) > 12 or len(whole) > 1 and whole.startswith('0'):
        integer = ''.join(_DIGITS[int(d)] for d in whole)
    else:
        number = int(whole)
        if number == 0:
            integer = '零'
        else:
            pieces = []
            group = 0
            gap = False
            while number:
                number, value = divmod(number, 10000)
                if value:
                    digits = str(value)
                    part = ''
                    zero = False
                    for pos, digit in enumerate(digits):
                        n = int(digit)
                        power = len(digits) - pos - 1
                        if n:
                            part += ('零' if zero else '') + _DIGITS[n] + ('', '十', '百', '千')[power]
                            zero = False
                        elif part:
                            zero = True
                    pieces.insert(0, part + ('', '万', '亿')[group])
                    if number and (value < 1000 or gap):
                        pieces.insert(0, '零')
                    gap = False
                else:
                    gap = bool(pieces)
                group += 1
            integer = ''.join(pieces)
            if integer.startswith('一十'):
                integer = integer[1:]
    return sign + integer + ('点' + ''.join(_DIGITS[int(d)] for d in fraction) if dot else '')


def _spoken_fragment(text: str) -> str:
    text = normalize_level_speech(text)

    def age(match):
        current, maximum = int(match['current']), int(match['maximum'])
        if not 0 <= current <= maximum or maximum == 0:
            return match[0]
        return f'生长进度{current}，成熟需要到{maximum}'

    def cooldown(match):
        seconds = format(Decimal(match['ms']) / 1000, 'f')
        if '.' in seconds:
            seconds = seconds.rstrip('0').rstrip('.')
        label = '冷却还剩' if match['field'] == 'cooldownRemainingMs' else '冷却时间'
        return label + seconds + '秒'

    text = _AGE_PROGRESS.sub(age, text)
    text = _COOLDOWN.sub(cooldown, text)
    text = _RESOURCE.sub(lambda m: _FIELD_NAMES[m['field']] + m['current'] + '，满值' + m['maximum'], text)
    text = _FIELD.sub(lambda m: _FIELD_NAMES[m['field']] + ('是' if m['assignment'] else ''), text)
    return _NUMBER.sub(lambda m: chinese_number(m[0]), text)


def normalize_spoken_text(text: str) -> str:
    """Normalize known levels, measured fields and unambiguous numeric tokens."""
    pieces = []
    end = 0
    for match in _PROTECTED.finditer(text):
        pieces.extend((_spoken_fragment(text[end:match.start()]), match[0]))
        end = match.end()
    pieces.append(_spoken_fragment(text[end:]))
    return ''.join(pieces)
