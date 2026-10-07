import unittest

from pronunciation import pronunciation_segments
from speech_segments import punctuation_segments
from stream_audio import sentence_segments


class PunctuationTests(unittest.TestCase):
    def test_commas_and_periods_are_boundaries_even_under_the_old_character_budget(self):
        text = '种子已经收好了，接下来把小麦种下去。看看麦子长得怎么样，等成熟以后再收。'
        expected = ['种子已经收好了，', '接下来把小麦种下去。', '看看麦子长得怎么样，', '等成熟以后再收。']
        self.assertEqual(punctuation_segments(text), expected)
        self.assertEqual(sentence_segments(text, 120), expected)
        self.assertEqual(pronunciation_segments(text, 16), expected)

    def test_tiny_comma_prefixes_join_the_following_complete_clause(self):
        self.assertEqual(punctuation_segments('好，嗯，我们先把东西收好。然后去田边看看，收拾好再出发。'),
                         ['好，嗯，我们先把东西收好。', '然后去田边看看，', '收拾好再出发。'])

    def test_closing_quotes_punctuation_runs_and_spaces_stay_with_their_clause(self):
        text = '她说：“种子已经收好了！！” 接下来先去播种……」然后回家。'
        parts = punctuation_segments(text)
        self.assertEqual(parts, ['她说：“种子已经收好了！！” ', '接下来先去播种……」', '然后回家。'])
        self.assertEqual(''.join(parts), text)

    def test_decimals_and_phonetic_atoms_remain_whole(self):
        hint = '<|SPECIAL_TOKEN_2|>ZHANG3<|SPECIAL_TOKEN_2|>'
        text = '距离还有12.5格，麦子正在' + hint + '大。小麦<种|ZHONG3>子先收好，接下来再去播种。'
        parts = punctuation_segments(text)
        self.assertEqual(''.join(parts), text)
        self.assertIn('12.5', parts[0])
        self.assertTrue(any(hint in part for part in parts))
        self.assertTrue(any('<种|ZHONG3>' in part for part in parts))

    def test_unpunctuated_clauses_are_not_cut_into_characters(self):
        text = '这是一句没有标点的完整说话内容' * 4
        self.assertEqual(punctuation_segments(text), [text])

    def test_literal_periods_inside_atoms_do_not_turn_short_comma_into_a_hard_boundary(self):
        text = '`a.b`，接下来先去种小麦。'
        self.assertEqual(punctuation_segments(text), [text])


if __name__ == '__main__':
    unittest.main()
