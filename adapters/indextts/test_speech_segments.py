import unittest

from pronunciation import pronunciation_segments
from speech_segments import punctuation_segments
from stream_audio import sentence_segments


class PunctuationTests(unittest.TestCase):
    def test_short_clauses_keep_sentence_context_and_sentence_ends_still_split(self):
        text = '种子已经收好了，接下来把小麦种下去。看看麦子长得怎么样，等成熟以后再收。'
        expected = ['种子已经收好了，接下来把小麦种下去。', '看看麦子长得怎么样，等成熟以后再收。']
        self.assertEqual(punctuation_segments(text), expected)
        self.assertEqual(sentence_segments(text, 120), expected)
        self.assertEqual(pronunciation_segments(text, 16), expected)

    def test_tiny_comma_prefixes_join_the_following_complete_clause(self):
        self.assertEqual(punctuation_segments('好，嗯，我们先把东西收好。然后去田边看看，收拾好再出发。'),
                         ['好，嗯，我们先把东西收好。', '然后去田边看看，收拾好再出发。'])

    def test_reported_monster_name_keeps_whole_sentence_in_one_model_request(self):
        text = '周围还有苦力怕和僵尸的动静，我先往北边走走，避开它们。'
        self.assertEqual(sentence_segments(text), [text])
        self.assertEqual(pronunciation_segments(text), [text])

    def test_long_sentence_splits_only_at_punctuation_even_when_target_crosses_a_name(self):
        text = '我们先往村庄的北边走走看看远处的苦力怕，再去附近的河边拿水桶装水，然后回家给小麦田补水。'
        parts = sentence_segments(text, 16)
        self.assertEqual(''.join(parts), text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(part.endswith(('，', '。')) for part in parts))
        for term in ('苦力怕', '水桶', '小麦田'):
            self.assertTrue(any(term in part for part in parts))

    def test_short_sentence_ends_are_not_merged_to_fill_a_budget(self):
        self.assertEqual(sentence_segments('好。知道了！走吧？'), ['好。', '知道了！', '走吧？'])

    def test_pronunciation_hint_characters_do_not_create_artificial_length_boundaries(self):
        plain = '小麦正在长大，接下来去田边看看，等成熟了再收割。'
        annotated = plain.replace('长', '<长|ZHANG3>')
        self.assertEqual(pronunciation_segments(annotated, 40), [annotated])
        parts = pronunciation_segments(annotated, 16)
        self.assertEqual(''.join(parts), annotated)
        self.assertTrue(any('<长|ZHANG3>' in part for part in parts))

    def test_period_inside_phonetic_atom_does_not_end_the_sentence(self):
        text = '<going|G OW1 . IH0 NG>，我们先收拾东西，再出发。'
        self.assertEqual(sentence_segments(text), [text])

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
