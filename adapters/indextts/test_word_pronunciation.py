"""Word-level pronunciation atoms retain contextual readings and caller ownership."""
import io
import json
import unittest
from unittest.mock import Mock, patch

from jieba import Tokenizer
from pypinyin import Style, lazy_pinyin
import pronunciation
from pronunciation import ContextPronunciation, text_projection
from speech_segments import punctuation_segments
from test_adapter_requests import load_adapter


WORDS = Tokenizer()
WORDS.initialize()


def resolver(selector=None):
    return ContextPronunciation(
        lambda text: [lazy_pinyin(text, style=Style.TONE3, neutral_tone_with_five=True)],
        selector=selector, words=lambda text: WORDS.tokenize(text, HMM=False))


class WordPronunciationTests(unittest.TestCase):
    def test_reported_question_retains_all_three_syllables_in_one_atom(self):
        model = resolver()
        text = '先往西边找找有没有鸡或者别的食物。'
        trace = []
        result = model.resolve(text, trace)
        self.assertEqual(result, '先往西边找找<有没有|YOU3 MEI2 YOU3>鸡或者别的食物。')
        self.assertEqual(text_projection(result)[0], text)
        self.assertEqual(model.resolve(result), result)
        self.assertEqual(punctuation_segments(result, max_chars=6), [result])
        self.assertTrue(any(row.get('word') == '有没有' for row in trace))

    def test_words_use_context_selected_readings_without_annotating_ordinary_words(self):
        result = resolver().resolve('种子收好了，看看有没有水，没有就先去河边。淹没的台阶不要走。')
        self.assertIn('<有没有|YOU3 MEI2 YOU3>', result)
        self.assertIn('<没有|MEI2 YOU3>', result)
        self.assertIn('<淹没|YAN1 MO4>', result)
        self.assertNotIn('<种子|', result)
        self.assertNotIn('<看看|', result)

    def test_dictionary_word_crossing_negation_cannot_establish_a_prosody_span(self):
        model = resolver(lambda text, indices: {i: 'MEI2' for i in indices if text[i] == '没'})
        self.assertEqual(model.resolve('收没收下。'), '收<没|MEI2>收下。')

    def test_explicit_character_hints_literals_and_urls_are_not_rewritten(self):
        model = resolver()
        text = '有<没|MEI2>有水，`有没有` https://example.test/有没有。'
        self.assertEqual(model.resolve(text), text)
        self.assertEqual(model.resolve('<有没有|YOU3 MEI2 YOU3>。'), '<有没有|YOU3 MEI2 YOU3>。')

    def test_both_http_paths_preserve_readable_text_and_send_the_whole_word(self):
        adapter = load_adapter()
        handler = object.__new__(adapter.Handler)
        handler._send = Mock()
        handler.log_message = Mock()
        handler._stream = Mock()
        text = '我先看看附近有没有吃的。'
        adapter.classify_mood_decision = Mock(return_value=('calm', 0.9))
        adapter.load_prefs = Mock(return_value={'voice': 'fixture', 'speed': 1})
        adapter.retempo = Mock(side_effect=lambda wav, speed: wav)
        with patch.object(pronunciation, '_resolver', resolver()), patch.object(
                adapter.urllib.request, 'urlopen', return_value=io.BytesIO(b'RIFFoffline')) as request:
            for path in ('/v1/audio/speech', '/v1/audio/speech/stream'):
                with self.subTest(path=path):
                    adapter._recent.clear()
                    handler.path = path
                    body = json.dumps({'input': text, 'segment_text': False}).encode('utf8')
                    handler.headers = {'Content-Length': str(len(body))}
                    handler.rfile = io.BytesIO(body)
                    handler.do_POST()
                    payload = (handler._stream.call_args.args[0] if path.endswith('/stream') else
                               json.loads(request.call_args.args[0].data))
                    self.assertEqual(payload['input'], '我先看看附近<有没有|YOU3 MEI2 YOU3>吃的。')
                    self.assertEqual(text_projection(payload['input'])[0], text)
                    if path.endswith('/stream'):
                        self.assertEqual(handler._stream.call_args.args[2], text)


if __name__ == '__main__':
    unittest.main()
