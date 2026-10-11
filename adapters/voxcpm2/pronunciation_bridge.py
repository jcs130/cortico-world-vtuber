"""Keep readable caller hints and translate sparse contextual readings for VoxCPM2."""
import re

_ANNOTATION = re.compile(r'<(?P<word>[^<>|\n]+)\|(?P<phones>[^<>\n]+)>')
_PINYIN = re.compile(r'[A-Za-zÜü]+[1-5]')


def native_phonemes(text: str) -> str:
    """Unsupported hints retain their readable text, never spoken metadata."""
    def convert(match):
        word = match['word']
        phones = match['phones'].split()
        if (not re.fullmatch(r'[\u3400-\u9fff]+', word) or len(word) != len(phones)
                or not all(_PINYIN.fullmatch(phone) for phone in phones)):
            return word
        return ''.join('{' + phone.lower().replace('ü', 'v') + '}' for phone in phones)
    return _ANNOTATION.sub(convert, text)
