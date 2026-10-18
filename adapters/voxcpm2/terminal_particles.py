"""Opt-in, voice-owned readings for attached utterance-final speech particles."""
import re

POLICY = 'configured-terminal-particles-v1'
_LITERAL = re.compile(r'```[\s\S]*?```|`[^`\n]*`|<[^<>\n]*>|\{[^{}\n]*\}|https?://[^\s<>]+')
_TAIL = r'[。！？!?…~～〜.,，;；:：]*[\"\'”’」』）》）\s]*(?:\[[^\[\]<>\r\n]+\][\"\'”’」』）》）\s]*)*$'


def validate_readings(readings):
    if readings is None:
        return {}
    if not isinstance(readings, dict) or len(readings) > 16:
        raise ValueError('Terminal particle readings must be a mapping of at most 16 suffixes')
    for word, phones in readings.items():
        if (not isinstance(word, str) or not re.fullmatch(r'[\u3400-\u9fff]{1,4}', word)
                or not isinstance(phones, str) or len(phones) > 64):
            raise ValueError('Terminal particles require Chinese text and matching pinyin')
        parts = phones.split()
        if len(parts) != len(word) or not all(re.fullmatch(r'[A-Za-zÜü]+[1-5]', part) for part in parts):
            raise ValueError('Terminal particle pinyin must contain one toned syllable per character')
    return readings


def normalize_terminal_particles(text: str, readings=None) -> str:
    """Retain isolated calls, repetitions and explicit/literal pronunciation controls.

    This is a conservative positional rule, not a semantic classifier. Only an
    utterance-final particle attached directly to Chinese speech is eligible.
    """
    readings = validate_readings(readings)
    if not readings:
        return text
    pattern = re.compile('(' + '|'.join(re.escape(word) for word in sorted(readings, key=len, reverse=True)) + ')(?=' + _TAIL + ')')
    match = pattern.search(text)
    if match is None:
        return text
    word = match[0]
    prefix = text[:match.start()]
    if not prefix or not re.fullmatch(r'[\u3400-\u9fff]', prefix[-1]) or prefix.endswith(word):
        return text
    if any(span.start() <= match.start() < span.end() for span in _LITERAL.finditer(text)):
        return text
    return prefix + '<' + word + '|' + ' '.join(readings[word].lower().split()) + '>' + text[match.end():]
