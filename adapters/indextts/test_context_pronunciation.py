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
    def test_multiple_homographs_are_selected_in_their_sentence_context(self):
        result = resolver().resolve('银行边重新种地，种子很重，还没发芽。')
        self.assertEqual(result, '银<行|HANG2>边<重|CHONG2>新<种|ZHONG4>地，<种|ZHONG3>子很<重|ZHONG4>，<还|HAI2>没发芽。')

    def test_lexicon_precedes_model_and_longest_phrase_protects_coat(self):
        resolved = resolver(phrases={'长大': ['ZHANG3', 'DA4']}).resolve('长大衣，弹幕。')
        self.assertEqual(resolved, '<长|CHANG2>大衣，<弹|DAN4>幕。')

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
        self.assertIn('<行|XING2>', model.resolve('一行人'))
        self.assertIn('<长|ZHANG3>', model.resolve('树苗长得快'))
        self.assertIn('<长|CHANG2>', model.resolve('路长得很'))

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
        self.assertIn('<还|HAI2>', resolver().resolve('等会还你。'))
        self.assertEqual(pronunciation_segments('<长|CHANG2>长的路，树苗正在<长|ZHANG3>大。', 8),
                         ['<长|CHANG2>长的路，', '树苗正在<长|ZHANG3>大。'])

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
        self.assertEqual(refined.resolve('树苗长得快，路长得很。'), '树苗<长|ZHANG3>得快，路<长|CHANG2>得很。')
        refined = resolver(phrases={'长短': ['CHANG2', 'DUAN3']})
        seen = []
        refined.selector = lambda text, positions: seen.extend(positions) or {positions[0]: 'ZHANG3'}
        result = refined.resolve('长短不同，还给你，<长|CHANG2>。')
        self.assertNotIn(0, seen)
        self.assertEqual(result, '<长|CHANG2>短不同，还给你，<长|CHANG2>。')


if __name__ == '__main__':
    unittest.main()
