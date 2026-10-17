"""Offline word/acronym contracts, including unseen names and literal exclusions."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'adapters/voxcpm2'))
from latin_speech import normalize_latin_speech


class LatinSpeechTest(unittest.TestCase):
    def test_capital_names_are_words_without_a_player_dictionary(self):
        self.assertEqual(normalize_latin_speech('NEKO、NANA、KIKO、MIKU、TORI、RIN、ALEX，过来。'),
                         'Neko、Nana、Kiko、Miku、Tori、Rin、Alex，过来。')
        self.assertEqual(normalize_latin_speech('NEKO你好，跟JOJO去看看。'), 'Neko你好，跟Jojo去看看。')
        self.assertEqual(normalize_latin_speech('neko、Neko、Jojo、Alex、CorMy。'), 'neko、Neko、Jojo、Alex、CorMy。')

    def test_real_initialisms_and_vowelless_names_are_not_invented_words(self):
        text = 'AI、API、CPU、GPU、TTS、MCP、YSM、QA、NPC、QQ、HTTP、ID、XQC、KK。'
        self.assertEqual(normalize_latin_speech(text), text)
        self.assertEqual(normalize_latin_speech('NASA 使用 LAN 和 RAM。'), 'Nasa 使用 Lan 和 Ram。')

    def test_separators_and_numeric_suffixes_preserve_all_identity_components(self):
        self.assertEqual(normalize_latin_speech('ag_NEKO、NANA_042、Alex123、ag_AuthQA。'),
                         'ag Neko、Nana 零四二、Alex 一二三、ag Auth QA。')
        self.assertEqual(normalize_latin_speech('AB12CD、__NEKO__、XQC_42。'), 'AB12CD、Neko、XQC 四二。')

    def test_literals_explicit_pronunciation_and_acoustic_tags_stay_literal(self):
        text = ('`NEKO_42` ```NEKO\nABC``` <长|ZHANG3> {hai2} [laughing] '
                'https://EXAMPLE.com/NEKO_42 USER@EXAMPLE.com C:\\NEKO\\42 '
                '/mycli NEKO /MCP/NEKO Qwen3.8-27B IndexTTS2.5')
        self.assertEqual(normalize_latin_speech(text), text)

    def test_idempotence_and_no_changes_to_chinese_facts(self):
        text = '玩家NEKO_042拿到十二块石头，AI有2个工具，生命二十分之十。'
        result = normalize_latin_speech(text)
        self.assertEqual(result, '玩家Neko 零四二拿到十二块石头，AI有2个工具，生命二十分之十。')
        self.assertEqual(normalize_latin_speech(result), result)
        self.assertEqual(normalize_latin_speech('你看，生命二十分之十。'), '你看，生命二十分之十。')


if __name__ == '__main__':
    unittest.main()
