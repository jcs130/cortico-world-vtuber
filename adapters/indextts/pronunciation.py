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
from speech_segments import punctuation_segments


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
# Reviewed proper nouns use one phonetic atom for the complete word. Single-
# character readings cannot tell the acoustic model which syllables form a name.
_TERM_READINGS = {'苦力怕': 'KU3 LI4 PA4'}
_PHRASES = re.compile('|'.join(re.escape(word) for word in sorted(_READINGS | _TERM_READINGS, key=len, reverse=True)))
# Preserve explicit caller pronunciation, literal markup, code and URLs. A second
# pass must not annotate the character inside a previously inserted hint.
_PROTECTED = re.compile(r'<[^<>\n]*>|`[^`\n]*`|https?://[^\s<>，。！？；]+')
# A dictionary's default cannot pin these homographs: 长得快 / 路长得很,
# 一行字 / 一行人. Their grammatical use is resolved in the whole sentence.
_CONTEXT_DEPENDENT_PHRASES = frozenset(('长得', '一行'))
_COMMON_READINGS = json.loads(Path(__file__).with_name('polyphonic_readings.json').read_text('utf-8'))
# These grammatical homographs have produced the wrong ordinary reading in the
# acoustic model. Pin their contextual reading, including the neutral 得; other
# default/neutral syllables retain Chinese text for natural prosody.
_CONTEXT_HINT_CHARACTERS = frozenset(('得', '还', '没'))
_REVIEWED_PHRASES = {
    word: [reading if char == '长' else None for char in word]
    for word, reading in _READINGS.items()
}
_REVIEWED_PHRASES['弹幕'] = ['DAN4', None]


def phrase_fallback(text: str) -> str:
    """Use the small unambiguous lexicon when contextual dependencies are unavailable."""
    def annotate(match):
        phrase = match.group(0)
        if phrase in _TERM_READINGS:
            return f'<{phrase}|{_TERM_READINGS[phrase]}>'
        if _READINGS[phrase] == 'CHANG2':
            return phrase
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
        self.criteria = _COMMON_READINGS
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
    """Resolve a bounded set of modern homographs in the complete utterance."""

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
        for word, readings in _REVIEWED_PHRASES.items():
            branch = self.trie
            for char in word:
                branch = branch.setdefault(char, {})
            branch[''] = readings

    @lru_cache(maxsize=4096)
    def candidates(self, char: str) -> frozenset[str]:
        modern = _COMMON_READINGS.get(char)
        if modern is None:
            return frozenset()
        return frozenset(reading.upper() for reading in self.alternatives(char)
                         if reading.upper() in modern)

    @lru_cache(maxsize=4096)
    def ordinary_reading(self, char: str) -> str | None:
        readings = self.alternatives(char)
        return readings[0].upper() if readings else None

    def resolve(self, text: str, trace: list[dict] | None = None) -> str:
        plain, positions = text_projection(text)
        dictionary, reviewed = {}, {}
        index = 0
        while index < len(plain):
            branch, cursor, match = self.trie, index, None
            while cursor < len(plain) and plain[cursor] in branch:
                branch = branch[plain[cursor]]; cursor += 1
                if '' in branch:
                    match = cursor, branch['']
            if match:
                end, readings = match
                word = plain[index:end]
                dictionary.update((i, reading) for i, reading in zip(range(index, end), readings) if reading)
                if word in _REVIEWED_PHRASES:
                    reviewed.update((i, reading) for i, reading in zip(range(index, end), readings) if reading)
                index = end
            else:
                index += 1
        ambiguous = [i for i, char in enumerate(plain)
                     if positions[i] is not None and len(self.candidates(char)) > 1]
        unresolved = [i for i in ambiguous if i not in reviewed]
        model, review = {}, {}
        if unresolved:
            with self.lock:
                readings = self.converter(plain)[0]
            if len(readings) != len(plain):
                raise ValueError('context pronunciation lost source alignment')
            model = {i: readings[i].upper() for i in unresolved
                     if isinstance(readings[i], str) and readings[i].upper() in self.candidates(plain[i])}
            if self.selector:
                # Review dictionary matches too: a substring such as 没收 in
                # 收没收下 can cross grammatical boundaries. Spend the bounded
                # review budget on known acoustic ambiguities and proposed
                # non-default hints before ordinary readings.
                targets = sorted(unresolved, key=lambda i: (
                    plain[i] not in _CONTEXT_HINT_CHARACTERS,
                    model.get(i, dictionary.get(i)) == self.ordinary_reading(plain[i]), i))
                selected = self.selector(plain, targets)
                review = {i: reading for i, reading in selected.items()
                          if i in unresolved and reading in self.candidates(plain[i])}
        choices = {**dictionary, **model, **review, **reviewed}
        conflicts = {i for i in unresolved if i not in review and i in model
                     and dictionary.get(i) in self.candidates(plain[i]) and dictionary[i] != model[i]}
        for i in conflicts:
            choices.pop(i, None)
        edits = {}
        for i in ambiguous:
            reading = choices.get(i)
            source = ('unresolved-conflict' if i in conflicts else
                      'reviewed-phrase' if i in reviewed else 'selector' if i in review else
                      'context-model' if i in model else 'dictionary')
            # Taiwanese variants or malformed model output cannot introduce a new reading.
            if reading in self.candidates(plain[i]):
                contextual_hint = plain[i] in _CONTEXT_HINT_CHARACTERS and source != 'dictionary'
                if contextual_hint or (not reading.endswith('5') and reading != self.ordinary_reading(plain[i])):
                    edits[positions[i]] = f'<{plain[i]}|{reading}>'
            if trace is not None:
                trace.append({'index': positions[i], 'character': plain[i], 'dictionary': dictionary.get(i),
                              'model': model.get(i), 'review': review.get(i), 'reading': reading,
                              'source': source, 'hinted': positions[i] in edits})
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
            'annotation_policy': 'context-homographs-sparse',
            'context_hint_characters': sorted(_CONTEXT_HINT_CHARACTERS),
            'dictionary_policy': 'context-reviewed',
            'term_policy': 'reviewed-whole-word', 'term_count': len(_TERM_READINGS),
            'context_ready': _resolver is not None, 'initialization_error': _initialization_error,
            'fallback_count': _resolver.failures if _resolver else 0,
            'last_ms': round(_resolver.last_ms, 2) if _resolver else None,
            'selector': {'configured': selector is not None,
                         'requests': getattr(selector, 'requests', 0),
                         'failures': getattr(selector, 'failures', 0),
                         'last_selected': getattr(selector, 'last_selected', 0),
                         'last_ms': round(getattr(selector, 'last_ms', 0), 2)}}


def normalize_pronunciation(text: str, trace: list[dict] | None = None) -> str:
    if not _resolver:
        return phrase_fallback(text)
    started = time.perf_counter()
    try:
        return _resolver.resolve(text, trace)
    except Exception as error:
        _resolver.failures += 1
        if trace is not None:
            trace.clear()
            trace.append({'source': 'phrase-fallback', 'reason': type(error).__name__})
        return phrase_fallback(text)
    finally:
        _resolver.last_ms = (time.perf_counter() - started) * 1000


def pronunciation_segments(text: str, max_chars: int = 40) -> list[str]:
    """Keep punctuation/closing quotes together and preserve pronunciation atoms.

    Sentence ends are boundaries. Commas become boundaries only when adjoining
    complete clauses exceed max_chars; pronunciation atoms count as their text.
    No word is cut to meet this soft target.
    """
    return punctuation_segments(text, max_chars=max_chars)
