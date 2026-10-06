"""Sentence-context IndexTTS pronunciation; explicit hints and subtitles retain their text."""

from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import urllib.request


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
# A dictionary's default cannot pin these homographs: 长得快 / 路长得很,
# 一行字 / 一行人. Their grammatical use is resolved in the whole sentence.
_CONTEXT_DEPENDENT_PHRASES = frozenset(('长得', '一行'))


def phrase_fallback(text: str) -> str:
    """Use the small unambiguous lexicon when contextual dependencies are unavailable."""
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


def text_projection(text: str) -> tuple[str, list[int | None]]:
    """Keep explicit hint text as context; code and URLs have no editable positions."""
    pieces, positions = [], []
    start = 0
    for match in _PROTECTED.finditer(text):
        pieces.append(text[start:match.start()])
        positions.extend(range(start, match.start()))
        protected = match.group(0)
        plain = protected[1:-1].split('|', 1)[0] if protected.startswith('<') and '|' in protected else ' '
        pieces.append(plain)
        positions.extend([None] * len(plain))
        start = match.end()
    pieces.append(text[start:])
    positions.extend(range(start, len(text)))
    return ''.join(pieces), positions


class ReadingSelector:
    """Optional local choice classifier for unresolved readings, never a speech generator."""

    def __init__(self, url: str, timeout: float = 0.6, min_confidence: float = 0.8):
        self.url, self.timeout, self.min_confidence = url, timeout, min_confidence
        self.criteria = json.loads(Path(__file__).with_name('polyphonic_readings.json').read_text('utf-8'))
        self.requests = self.failures = self.last_selected = 0
        self.last_ms = 0.0

    def __call__(self, text: str, positions: list[int]) -> dict[int, str]:
        targets = []
        for i in positions:
            if text[i] not in self.criteria:
                continue
            start = max(text.rfind(mark, 0, i) for mark in '，。！？；') + 1
            ends = [text.find(mark, i) for mark in '，。！？；' if text.find(mark, i) >= 0]
            end = min(ends) if ends else len(text)
            target = text[start:i] + '【' + text[i] + '】' + text[i + 1:end]
            targets.append((i, {'当前分句': target, '前面发生': text[:start][-120:]}, self.criteria[text[i]]))
        if not targets:
            return {}
        started = time.perf_counter()
        self.last_selected = 0
        selected = {}
        try:
            # Each target has its own semantic state. Sharing one state for two
            # uses of the same character biases a choice classifier toward one meaning.
            # The complete utterance is retained for G2PW; this optional review has
            # one total time budget, including every query and any queue delay.
            for i, state, criteria in targets:
                remaining = self.timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    break
                payload = {'state': state, 'questions': {'reading': {'type': 'choice',
                    'instructions': '给当前分句里用【】标出的那个字选择普通话读音。只判断这个标出的字，按当前分句的语义选择。',
                    'criteria': criteria}}}
                request = urllib.request.Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                                 headers={'Content-Type': 'application/json'})
                self.requests += 1
                with urllib.request.urlopen(request, timeout=remaining) as response:
                    answer = json.load(response)['answers'].get('reading', {})
                confidence = answer.get('confidence')
                choice = answer.get('choice')
                if (isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                        and math.isfinite(confidence) and self.min_confidence <= confidence <= 1
                        and choice in criteria):
                    selected[i] = choice
            return selected
        except Exception:
            self.failures += 1
            return selected
        finally:
            self.last_selected = len(selected)
            self.last_ms = (time.perf_counter() - started) * 1000


class ContextPronunciation:
    """Dictionary phrases precede CPU G2PW for unresolved polyphonic characters."""

    def __init__(self, converter, phrases=None, alternatives=None, selector=None):
        if phrases is None or alternatives is None:
            from pypinyin import pinyin, Style
            from pypinyin.phrases_dict import phrases_dict
            from pypinyin.contrib.tone_convert import to_tone3
            phrases = {word: [to_tone3(reading[0], neutral_tone_with_five=True).upper() for reading in readings]
                       for word, readings in phrases_dict.items()}
            alternatives = lambda char: pinyin(char, style=Style.TONE3, heteronym=True,
                                                neutral_tone_with_five=True)[0]
        self.converter = converter
        self.selector = selector
        self.lock = threading.Lock()
        self.failures = 0
        self.last_ms = 0.0
        self.alternatives = alternatives
        self.trie = {}
        for word, readings in phrases.items():
            if len(word) < 2 or word in _CONTEXT_DEPENDENT_PHRASES or len(word) != len(readings):
                continue
            branch = self.trie
            for char in word:
                branch = branch.setdefault(char, {})
            branch[''] = [reading.upper() for reading in readings]
        # Technical homographs and longest-match protection use the same lexicon.
        for word, readings in {'弹幕': ['DAN4', 'MU4'], '长大衣': ['CHANG2', 'DA4', 'YI1']}.items():
            branch = self.trie
            for char in word:
                branch = branch.setdefault(char, {})
            branch[''] = readings

    @lru_cache(maxsize=4096)
    def candidates(self, char: str) -> frozenset[str]:
        if not '\u3400' <= char <= '\u9fff' or char in '一不':
            return frozenset()
        return frozenset(reading.upper() for reading in self.alternatives(char))

    def resolve(self, text: str) -> str:
        plain, positions = text_projection(text)
        choices = {}
        index = 0
        while index < len(plain):
            branch, cursor, match = self.trie, index, None
            while cursor < len(plain) and plain[cursor] in branch:
                branch = branch[plain[cursor]]; cursor += 1
                if '' in branch:
                    match = cursor, branch['']
            if match:
                end, readings = match
                choices.update(zip(range(index, end), readings)); index = end
            else:
                index += 1
        ambiguous = [i for i, char in enumerate(plain)
                     if positions[i] is not None and len(self.candidates(char)) > 1]
        missing = [i for i in ambiguous if i not in choices]
        if missing:
            with self.lock:
                readings = self.converter(plain)[0]
            if len(readings) != len(plain):
                raise ValueError('context pronunciation lost source alignment')
            choices.update((i, readings[i].upper()) for i in missing if isinstance(readings[i], str))
            if self.selector:
                choices.update(self.selector(plain, missing))
        edits = {}
        for i in ambiguous:
            reading = choices.get(i)
            # Taiwanese variants or malformed model output cannot introduce a new reading.
            if reading in self.candidates(plain[i]):
                edits[positions[i]] = f'<{plain[i]}|{reading}>'
        result = ''.join(edits.get(i, char) for i, char in enumerate(text))
        return phrase_fallback(result)


_resolver: ContextPronunciation | None = None
_initialization_error: str | None = None


def initialize_pronunciation(model_dir: str | None = None, tokenizer_dir: str | None = None) -> None:
    """Load prepared local resources once; speech requests never download a model."""
    global _resolver, _initialization_error
    model_dir = model_dir or os.environ.get('CORTICO_G2PW_MODEL_DIR')
    tokenizer_dir = tokenizer_dir or os.environ.get('CORTICO_G2PW_TOKENIZER_DIR')
    if not model_dir or not tokenizer_dir:
        _initialization_error = 'local-resources-not-configured'
        return
    try:
        for resource in (Path(model_dir) / 'version', Path(model_dir) / 'g2pw.onnx', Path(tokenizer_dir) / 'vocab.txt'):
            if not resource.is_file():
                raise FileNotFoundError('prepared local pronunciation resource missing')
        from g2pw import G2PWConverter
        converter = G2PWConverter(model_dir=model_dir, model_source=tokenizer_dir, style='pinyin',
                                  enable_non_tradional_chinese=True, turnoff_tqdm=True)
        # Upstream treats zero as unset; Windows must not spawn DataLoader workers per utterance.
        converter.num_workers = 0
        selector_url = os.environ.get('CORTICO_PRONUNCIATION_DECISION_URL')
        _resolver = ContextPronunciation(converter, selector=ReadingSelector(selector_url) if selector_url else None)
        _initialization_error = None
    except Exception as error:
        _resolver = None
        _initialization_error = type(error).__name__


def pronunciation_health() -> dict:
    selector = _resolver.selector if _resolver else None
    return {'policy': 'context-pinyin' if _resolver else 'phrase-pinyin-fallback',
            'context_ready': _resolver is not None, 'initialization_error': _initialization_error,
            'fallback_count': _resolver.failures if _resolver else 0,
            'last_ms': round(_resolver.last_ms, 2) if _resolver else None,
            'selector': {'configured': selector is not None,
                         'requests': getattr(selector, 'requests', 0),
                         'failures': getattr(selector, 'failures', 0),
                         'last_selected': getattr(selector, 'last_selected', 0),
                         'last_ms': round(getattr(selector, 'last_ms', 0), 2)}}


def normalize_pronunciation(text: str) -> str:
    if not _resolver:
        return phrase_fallback(text)
    started = time.perf_counter()
    try:
        return _resolver.resolve(text)
    except Exception:
        _resolver.failures += 1
        return phrase_fallback(text)
    finally:
        _resolver.last_ms = (time.perf_counter() - started) * 1000


def pronunciation_segments(text: str, max_chars: int = 40) -> list[str]:
    """Split resolved speech at clause boundaries, counting visible characters."""
    units = re.findall(r'<[^<>\n]*>|`[^`\n]*`|https?://[^\s<>，。！？；]+|.', text, re.DOTALL)
    parts, part = [], ''
    for index, unit in enumerate(units):
        part += unit
        boundary = unit in '，。！？；,!?;' or (unit == '.' and not (index + 1 < len(units) and units[index + 1].isdigit()))
        if boundary:
            parts.append(part); part = ''
    if part:
        parts.append(part)
    segments, current = [], ''
    def visible(value):
        return len(re.sub(r'<([^<>|]+)\|[^<>]+>', r'\1', value))
    for part in parts:
        if current and visible(current + part) > max_chars:
            segments.append(current); current = part
        else:
            current += part
    if current:
        segments.append(current)
    return segments or [text]
