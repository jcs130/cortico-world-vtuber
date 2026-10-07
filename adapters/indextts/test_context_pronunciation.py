"""Offline pronunciation alignment and full-utterance streaming contracts."""
import unittest
import io
import json
from unittest.mock import patch
import pronunciation
from pronunciation import ContextPronunciation, pronunciation_segments, text_projection


ALTERNATIVES = {'还': ['hai2', 'huan2'], '重': ['zhong4', 'chong2'], '行': ['xing2', 'hang2'],
                '种': ['zhong3', 'zhong4'], '长': ['chang2', 'zhang3'], '和': ['he2', 'he4'],
                '弹': ['tan2', 'dan4']}


def contextual(text):
    readings = [char for char in text]
    for i, char in enumerate(text):
        if char == '还': readings[i] = 'huan2' if '借给' in text else 'hai2'
        elif char == '重': readings[i] = 'chong2' if text[i + 1:].startswith('新') else 'zhong4'
        elif char == '行': readings[i] = 'hang2' if text[:i].endswith('银') or text[i + 1:].startswith('字') else 'xing2'
        elif char == '种': readings[i] = 'zhong3' if text[i + 1:].startswith('子') else 'zhong4'
        elif char == '长': readings[i] = 'zhang3' if '树苗' in text else 'chang2'
        elif char == '和': readings[i] = 'han4'
    return [readings]


def resolver(converter=contextual, phrases=None):
    return ContextPronunciation(converter, phrases=phrases or {},
                                alternatives=lambda char: ALTERNATIVES.get(char, [char]))


class ContextTests(unittest.TestCase):
    def test_neutral_particles_and_ordinary_readings_keep_sentence_text(self):
        alternatives = {'了': ['le5', 'liao3'], '的': ['de5', 'di2', 'di4'],
                        '也': ['ye3', 'yi2'], '包': ['bao1', 'pao2'],
                        '才': ['cai2', 'zai1'], '长': ['zhang3', 'chang2']}
        model = ContextPronunciation(
            lambda text: [[alternatives.get(char, [char])[0] for char in text]],
            phrases={'长大': ['ZHANG3', 'DA4'], '长短': ['CHANG2', 'DUAN3']},
            alternatives=lambda char: alternatives.get(char, [char]))
        self.assertEqual(model.resolve('我的包也满了，小麦才长大了。'),
                         '我的包也满了，小麦才<长|ZHANG3>大了。')
        self.assertEqual(model.resolve('长短不同。'), '<长|CHANG2>短不同。')

    def test_unreviewed_characters_keep_sentence_text_and_explicit_hints_still_work(self):
        alternatives = {'了': ['le5', 'liao3'], '觉': ['jue2', 'jiao4'], '得': ['de2', 'dei3', 'de5']}
        readings = iter(['LIAO3', 'JIAO4', 'LE5', 'DEI3', 'DE5'])
        phrase = '了解以后睡一觉了，我得做得好。'
        resolved = []
        for char in phrase:
            resolved.append(next(readings) if char in alternatives else char)
        model = ContextPronunciation(lambda text: [resolved], phrases={},
            alternatives=lambda char: alternatives.get(char, [char]))
        self.assertEqual(model.resolve(phrase), '了解以后睡一觉了，我<得|DEI3>做得好。')
        self.assertEqual(model.resolve('<了|LIAO3>' + phrase[1:]),
                         '<了|LIAO3>解以后睡一觉了，我<得|DEI3>做得好。')

    def test_recorded_rare_readings_never_override_ordinary_dialogue(self):
        alternatives = {'肚': ['du4', 'du3'], '差': ['cha4', 'cha1', 'chai1'], '了': ['le5', 'liao3']}
        text = '口粮够垫肚子了，回头交公会差。'
        model = ContextPronunciation(
            lambda value: [[{'肚': 'DU3', '差': 'CHA1', '了': 'LE5'}.get(char, char) for char in value]],
            phrases={}, alternatives=lambda char: alternatives.get(char, [char]))
        self.assertEqual(model.resolve(text), text)

    def test_multiple_homographs_are_selected_in_their_sentence_context(self):
        result = resolver().resolve('银行边重新种地，种子很重，还没发芽。')
        self.assertEqual(result, '银<行|HANG2>边<重|CHONG2>新<种|ZHONG4>地，种子很重，还没发芽。')

    def test_lexicon_precedes_model_and_longest_phrase_protects_coat(self):
        resolved = resolver(phrases={'长大': ['ZHANG3', 'DA4']}).resolve('长大衣，弹幕。')
        self.assertEqual(resolved, '长大衣，<弹|DAN4>幕。')

    def test_explicit_hint_stays_visible_as_context_without_being_changed(self):
        text = '<借给|JIE4 GEI3>我的东西，还你。`还` https://example.org/还'
        result = resolver().resolve(text)
        self.assertEqual(result, '<借给|JIE4 GEI3>我的东西，<还|HUAN2>你。`还` https://example.org/还')
        self.assertEqual(resolver().resolve(result), result)
        plain, positions = text_projection(text)
        self.assertTrue(plain.startswith('借给我的东西，还你。'))
        self.assertEqual(len(plain), len(positions))

    def test_context_dependent_dictionary_phrase_does_not_pin_one_reading(self):
        model = resolver(phrases={'一行': ['YI1', 'XING2'], '长得': ['ZHANG3', 'DE5']})
        self.assertIn('<行|HANG2>', model.resolve('一行字'))
        self.assertEqual(model.resolve('一行人'), '一行人')
        self.assertIn('<长|ZHANG3>', model.resolve('树苗长得快'))
        self.assertEqual(model.resolve('路长得很'), '路长得很')

    def test_unlisted_model_reading_is_preserved_for_normal_tts(self):
        self.assertEqual(resolver().resolve('和你走。'), '和你走。')
        invalid = resolver(lambda text: [['HUAN2>注入' for _ in text]])
        self.assertEqual(invalid.resolve('还你。'), '还你。')

    def test_alignment_or_backend_failure_falls_back_without_breaking_speech(self):
        broken = resolver(lambda text: [[]])
        with patch.object(pronunciation, '_resolver', broken):
            self.assertEqual(pronunciation.normalize_pronunciation('小麦长大了。'), '小麦<长|ZHANG3>大了。')
            self.assertEqual(pronunciation.pronunciation_health()['fallback_count'], 1)

    def test_legacy_segments_retain_reading_selected_from_previous_clause(self):
        text = '刚才借给我一把剑，等会还你。'
        full = resolver().resolve(text)
        segments = pronunciation_segments(full, 10)
        self.assertEqual(''.join(segments), full)
        self.assertIn('<还|HUAN2>', segments[-1])
        self.assertEqual(resolver().resolve('等会还你。'), '等会还你。')
        self.assertEqual(pronunciation_segments('<长|CHANG2>长的山间小路，树苗正在<长|ZHANG3>大。', 8),
                         ['<长|CHANG2>长的山间小路，', '树苗正在<长|ZHANG3>大。'])

    def test_optional_selector_isolates_same_character_meanings_and_validates_choices(self):
        selector = pronunciation.ReadingSelector('http://local.test/systemone')
        responses = [io.BytesIO(json.dumps({'answers': {'reading': {'choice': choice, 'confidence': confidence}}}).encode())
                     for choice, confidence in [('ZHANG3', 0.96), ('CHANG2', 0.86)]]
        text = '树苗长得很快。这条路长得很。'
        with patch.object(pronunciation.urllib.request, 'urlopen', side_effect=responses) as request:
            self.assertEqual(selector(text, [2, 10]), {2: 'ZHANG3', 10: 'CHANG2'})
            first, second = [json.loads(call.args[0].data) for call in request.call_args_list]
            self.assertEqual(first['state']['当前分句'], '树苗【长】得很快')
            self.assertEqual(second['state']['当前分句'], '这条路【长】得很')
            self.assertEqual(selector.requests, 2)
            self.assertLessEqual(sum(call.kwargs['timeout'] for call in request.call_args_list), 1.2)
        responses = [io.BytesIO(json.dumps({'answers': {'reading': {'choice': choice, 'confidence': confidence}}}).encode())
                     for choice, confidence in [('CHANG2', 0.6), ('<fake>', 0.98)]]
        with patch.object(pronunciation.urllib.request, 'urlopen', side_effect=responses):
            self.assertEqual(selector(text, [2, 10]), {})
        with patch.object(pronunciation.urllib.request, 'urlopen', side_effect=TimeoutError):
            self.assertEqual(selector(text, [2, 10]), {})
            self.assertEqual(selector.failures, 1)

    def test_selector_only_refines_unresolved_positions_and_keeps_explicit_caller_hints(self):
        refined = resolver()
        refined.selector = lambda text, positions: {positions[-1]: 'CHANG2'}
        self.assertEqual(refined.resolve('树苗长得快，路长得很。'), '树苗<长|ZHANG3>得快，路长得很。')
        refined = resolver(phrases={'长短': ['CHANG2', 'DUAN3']})
        seen = []
        refined.selector = lambda text, positions: seen.extend(positions) or {positions[0]: 'ZHANG3'}
        result = refined.resolve('长短不同，还给你，<长|CHANG2>。')
        self.assertNotIn(0, seen)
        self.assertEqual(result, '长短不同，还给你，<长|CHANG2>。')

    def test_seed_noun_and_reduplication_remain_natural_but_planting_is_disambiguated(self):
        model = ContextPronunciation(contextual,
            phrases={'种子': ['ZHONG3', 'ZI5'], '这种': ['ZHE4', 'ZHONG3'], '种下': ['ZHONG4', 'XIA4'],
                     '看看': ['KAN4', 'KAN4']},
            alternatives=lambda char: {'看': ['kan4', 'kan1']}.get(char, ALTERNATIVES.get(char, [char])))
        self.assertEqual(model.resolve('种子收好了，先种下去，再看看。'),
                         '种子收好了，先<种|ZHONG4>下去，再看看。')
        self.assertEqual(model.resolve('小麦种子、南瓜种子，这种种子都能种下去。'),
                         '小麦种子、南瓜种子，这种种子都能<种|ZHONG4>下去。')
        self.assertEqual(model.resolve('<种|ZHONG3>子收好了。'), '<种|ZHONG3>子收好了。')


if __name__ == '__main__':
    unittest.main()
