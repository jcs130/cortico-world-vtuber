"""Offline pronunciation contract: contrast, ambiguity and explicit overrides."""

import unittest

from pronunciation import normalize_pronunciation


class PronunciationTests(unittest.TestCase):
    def test_growth_and_length_contrast_in_one_utterance(self):
        self.assertEqual(
            normalize_pronunciation('小麦长大了，看看木板的长短。树苗生长很快，测量一下长度。'),
            '小麦<长|ZHANG3>大了，看看木板的长短。树苗生<长|ZHANG3>很快，测量一下长度。',
        )

    def test_proper_name_is_one_pronunciation_atom_and_ordinary_words_stay_natural(self):
        text = '苦力怕在旁边，苦力活不怕做。种子已经收好了。'
        expected = '<苦力怕|KU3 LI4 PA4>在旁边，苦力活不怕做。种子已经收好了。'
        self.assertEqual(normalize_pronunciation(text), expected)
        self.assertEqual(normalize_pronunciation(expected), expected)

    def test_term_dictionary_preserves_explicit_overrides_code_and_urls(self):
        text = '<苦力怕|KU3 LI4 PA4>、`苦力怕`、https://example.test/苦力怕。'
        self.assertEqual(normalize_pronunciation(text), text)

    def test_not_every_occurrence_is_growth_or_length(self):
        text = '村长、队长和长老说：树长得很快，这条路长得很，我不擅长。'
        self.assertEqual(normalize_pronunciation(text), text)

    def test_longest_phrase_avoids_growth_prefix_in_overcoat(self):
        self.assertEqual(normalize_pronunciation('孩子长大后穿长大衣。'),
                         '孩子<长|ZHANG3>大后穿长大衣。')

    def test_preserves_explicit_hints_literals_urls_and_is_idempotent(self):
        text = '我<长|CHANG2>大了。<长大|CHANG2 DA4> `长短` https://example.test/长大，然后成长。'
        expected = '我<长|CHANG2>大了。<长大|CHANG2 DA4> `长短` https://example.test/长大，然后成<长|ZHANG3>。'
        self.assertEqual(normalize_pronunciation(text), expected)
        self.assertEqual(normalize_pronunciation(expected), expected)


if __name__ == '__main__':
    unittest.main()
