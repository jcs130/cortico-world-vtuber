"""Offline regression tests for corti_speech_style request preparation."""

import math
import re
import unittest

from corti_speech_style import emotion_mix_vector, normalize_level_speech, strip_stage_directions


RULES = [
    (re.compile(r"开心|兴奋|笑|happy", re.I), "happy"),
    (re.compile(r"平静|calm", re.I), "calm"),
    (re.compile(r"温柔|轻声|gentle", re.I), "gentle"),
    (re.compile(r"惊讶|surprised", re.I), "surprised"),
]
HAPPY = [1, 0, 0, 0, 0, 0, 0, 0]


class EmotionTests(unittest.TestCase):
    def test_calm_uses_reference_even_when_explicit(self):
        for source in ("tag", "decision", "regex"):
            self.assertIsNone(emotion_mix_vector("calm", [0, 0, 0, 0, 0, 0, 0, 1], source=source, confidence=1))

    def test_uncertain_decision_uses_reference(self):
        for confidence in (0, 0.49, 0.649, None, math.nan):
            self.assertIsNone(emotion_mix_vector("happy", HAPPY, source="decision", confidence=confidence))

    def test_high_confidence_keeps_reference_weight(self):
        vector = emotion_mix_vector("happy", HAPPY, source="decision", confidence=0.65)
        self.assertEqual(vector, [0.35, 0, 0, 0, 0, 0, 0, 0])
        self.assertAlmostEqual(1 - sum(vector), 0.65)

    def test_explicit_intensity_is_bounded_not_filled_with_calm(self):
        vector = emotion_mix_vector("happy", HAPPY, source="tag", intensity=0.5, max_mix=0.45)
        self.assertEqual(vector, [0.45, 0, 0, 0, 0, 0, 0, 0])
        self.assertEqual(emotion_mix_vector("happy", HAPPY, source="tag", intensity=0.1), [0.1, 0, 0, 0, 0, 0, 0, 0])

    def test_preferences_cannot_override_hard_cap(self):
        for mix in (100, 1, 0.7):
            vector = emotion_mix_vector("happy", HAPPY, source="tag", max_mix=mix)
            self.assertLessEqual(sum(vector), 0.45)
        for mix in (0, -1):
            self.assertIsNone(emotion_mix_vector("happy", HAPPY, source="tag", max_mix=mix))

    def test_invalid_values_use_defaults_without_nan(self):
        for mix in (math.inf, math.nan, None, "bad", True):
            vector = emotion_mix_vector("happy", HAPPY, source="tag", max_mix=mix)
            self.assertEqual(sum(vector), 0.35)
        for vector in ([1], [math.nan] * 8, [-1] * 8, [0] * 8):
            self.assertIsNone(emotion_mix_vector("happy", vector, source="tag"))

    def test_mixed_emotions_keep_distribution(self):
        vector = emotion_mix_vector("playful", [0.6, 0, 0, 0, 0, 0, 0.3, 0.1], source="tag", max_mix=0.45)
        self.assertLessEqual(sum(vector), 0.45)
        self.assertAlmostEqual(vector[0] / vector[6], 2)
        self.assertAlmostEqual(vector[7], 0.045)


class PronunciationTests(unittest.TestCase):
    def test_ascii_enchantment_levels(self):
        self.assertEqual(normalize_level_speech("锋利 II、耐久 III，力量 V。"), "锋利二级、耐久三级，力量五级。")

    def test_unicode_roman_levels(self):
        self.assertEqual(normalize_level_speech("保护Ⅳ、效率Ⅴ、抢夺ⅲ。"), "保护四级、效率五级、抢夺三级。")

    def test_adjacent_enchantments_each_keep_their_numeric_level(self):
        self.assertEqual(normalize_level_speech('效率V耐久II的，锋利IV耐久III的。'),
                         '效率五级耐久二级的，锋利四级耐久三级的。')
        self.assertEqual(normalize_level_speech('保护I耐久II荆棘III，效率Ⅴ耐久ⅲ。'),
                         '保护一级耐久二级荆棘三级，效率五级耐久三级。')
        self.assertEqual(normalize_level_speech('玩家锋利IV耐久III，Bob效率V耐久II'),
                         '玩家锋利IV耐久III，Bob效率V耐久II')

    def test_explicit_levels_and_tower_floors(self):
        self.assertEqual(normalize_level_speech("技能等级：III；第 XV 层；试炼塔 IV 层。"), "技能等级三级；第十五层；试炼塔第四层。")

    def test_no_duplicate_level_suffix(self):
        self.assertEqual(normalize_level_speech("锋利 II 级和等级 IV级"), "锋利二级和等级四级")

    def test_english_models_ids_and_coordinates_unchanged(self):
        text = "CortiII、AdeleFelice、Bob_IV；IndexTTS2.5，Qwen3.8-27B，VLM，III。坐标(-538, 63, -375)，血量6/20。"
        self.assertEqual(normalize_level_speech(text), text)
        self.assertEqual(normalize_level_speech("玩家锋利II，ID：耐久III，Bob锋利II，昵称‘保护IV’"), "玩家锋利II，ID：耐久III，Bob锋利II，昵称‘保护IV’")

    def test_invalid_and_embedded_roman_tokens_unchanged(self):
        for text in ("锋利 IIV", "锋利 IIII", "保护 MIX", "力量 IVORY", "力量 V_Pro", "锋利 II2"):
            self.assertEqual(normalize_level_speech(text), text)


class StageDirectionTests(unittest.TestCase):
    def test_acoustic_cues_are_metadata_for_index_not_english_speech(self):
        cues = ('laughing', 'sigh', 'breath', 'Uhm', 'Shh',
                'Question-ah', 'Question-ei', 'Question-en', 'Question-oh',
                'Confirmation-en', 'Surprise-wa', 'Surprise-yo', 'Surprise-ah',
                'Surprise-oh', 'Dissatisfaction-hnn')
        for cue in cues:
            for casing in (cue, cue.upper(), cue.lower()):
                with self.subTest(cue=casing):
                    clean, tags, _, _ = strip_stage_directions(f'你好[{casing}]。', RULES)
                    self.assertEqual(clean, '你好。')
                    self.assertEqual(tags, [casing])

    def test_cue_words_inside_facts_or_plain_speech_are_preserved(self):
        for text in ('这个单词是 sigh。', '[a sigh of relief]', '[玩家 Shh]',
                     '[Surprise-wa 是一种标签]', '[breath_count=3]', '[SighPlayer]'):
            with self.subTest(text=text):
                self.assertEqual(strip_stage_directions(text, RULES), (text, [], None, None))

    def test_voice_hints_yield_to_explicit_mood_in_either_order(self):
        for text in ('[sigh](calm)你好。', '(calm)[sigh]你好。'):
            clean, _, mood, strength = strip_stage_directions(text, RULES)
            self.assertEqual((clean, mood, strength), ('你好。', 'calm', None))
        self.assertEqual(strip_stage_directions('[sigh]唉。', RULES),
                         ('唉。', ['sigh'], 'melancholic', None))

    def test_recognized_stage_cue_and_explicit_intensity(self):
        self.assertEqual(strip_stage_directions("（开心@0.5）你好！", RULES), ("你好！", ["开心@0.5"], "happy", 0.5))

    def test_bare_tags_removed_but_preserve_fact_parentheses(self):
        clean, tags, mood, strength = strip_stage_directions("开心@0.5 这把（锋利 II）给你。坐标（1,2,3）。", RULES)
        self.assertEqual(clean, "这把（锋利 II）给你。坐标（1,2,3）。")
        self.assertEqual((tags, mood, strength), (["开心@0.5"], "happy", 0.5))

    def test_unknown_complete_emotion_tags_are_metadata_not_speech(self):
        for tag in ("轻松@0.4", "专注@0.2", "relaxed@0.4"):
            for wrapped in ("（%s）", "(%s)", "[%s]", "【%s】", "<%s>", "*%s*", "%s"):
                with self.subTest(tag=tag, wrapped=wrapped):
                    self.assertEqual(strip_stage_directions(wrapped % tag, RULES), ("", [tag], None, None))
        self.assertEqual(strip_stage_directions("（轻松@0.4）我在河边。", RULES),
                         ("我在河边。", ["轻松@0.4"], None, None))

    def test_unknown_tag_does_not_override_later_known_mood(self):
        self.assertEqual(strip_stage_directions("（轻松@0.4）（开心@0.2）钓到了！", RULES),
                         ("钓到了！", ["轻松@0.4", "开心@0.2"], "happy", 0.2))

    def test_explicit_tag_requires_complete_short_label_and_number(self):
        for text in ("（开心时血量@0.4/20）", "（happy fish@0.4）", "（轻松@abc）",
                     "（轻松@0.4.2）", "（版本@0.4-beta）", "（开心|happy@0.4）",
                     "（普通事实里出现开心@0.4的说明）", "我在村庄开心@0.4地钓鱼。",
                     "（" + "这是一段事实说明" * 8 + " happy@0.4 只是示例文本）",
                     "这是版本 relaxed@0.4-beta，不是情绪。",
                     "LongPlayerNameIsHappy@0.4", "User_happy@0.4", "0happy@0.4"):
            with self.subTest(text=text):
                self.assertEqual(strip_stage_directions(text, RULES), (text, [], None, None))

    def test_bare_english_tag_has_whole_word_boundaries(self):
        self.assertEqual(strip_stage_directions("relaxed@0.4. 你好！", RULES),
                         (". 你好！", ["relaxed@0.4"], None, None))
        self.assertEqual(strip_stage_directions("surprised@0.3 你好！", RULES),
                         ("你好！", ["surprised@0.3"], "surprised", 0.3))

    def test_pronunciation_and_plain_brackets_preserved(self):
        text = "<行|XING2>，[村庄北侧]，（等级 II），*锋利 II*，<CortiLan>。"
        self.assertEqual(strip_stage_directions(text, RULES)[0], text)

    def test_remove_action_without_manufacturing_words(self):
        self.assertEqual(strip_stage_directions("（挥手）你好。\n我在河边。", RULES)[0], "你好。\n我在河边。")

    def test_remove_tag_only_and_empty_punctuation_gap(self):
        self.assertEqual(strip_stage_directions("（轻声）", RULES)[0], "")
        self.assertEqual(strip_stage_directions("你好，（微笑），给你面包。", RULES)[0], "你好，给你面包。")

    def test_ordinary_sentences_are_not_cut_or_rewritten(self):
        text = "我先去河边。鱼还没上钩，你们先走。等我一会儿！"
        self.assertEqual(strip_stage_directions(text, RULES)[0], text)


if __name__ == "__main__":
    unittest.main()
