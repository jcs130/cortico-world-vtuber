"""Speech text preserves facts and identifiers while removing ambiguous readings."""

import unittest

from spoken_text import chinese_number, normalize_spoken_text


class SpokenTextTests(unittest.TestCase):
    def test_recorded_adjacent_enchantment_levels(self):
        self.assertEqual(normalize_spoken_text('效率V耐久II的，锋利IV耐久III的。'),
                         '效率五级耐久二级的，锋利四级耐久三级的。')
        self.assertEqual(normalize_spoken_text('保护I耐久II荆棘III'), '保护一级耐久二级荆棘三级')
        self.assertEqual(normalize_spoken_text('锋利II、耐久III和经验修补I，修补Ⅰ。'),
                         '锋利二级、耐久三级和经验修补一级，修补一级。')

    def test_arabic_count_and_numeric_sequence_do_not_depend_on_backend_reading(self):
        self.assertEqual(normalize_spoken_text('1、2、3，凑够16个小麦，12个面包。'),
                         '一、二、三，凑够十六个小麦，十二个面包。')
        self.assertEqual(normalize_spoken_text('坐标(-538, 63, -375)，还剩1.25秒。'),
                         '坐标(负五百三十八, 六十三, 负三百七十五)，还剩一点二五秒。')

    def test_measured_growth_progress_uses_both_values_without_predicting_time(self):
        self.assertEqual(normalize_spoken_text('小麦还在长，age 5/7，age才1/7。'),
                         '小麦还在长，生长进度五，成熟需要到七，生长进度一，成熟需要到七。')
        self.assertEqual(normalize_spoken_text('age 全没变，moisture=7。'),
                         '生长阶段 全没变，耕地湿度是七。')
        self.assertEqual(normalize_spoken_text('age=0/7，age:7/7'),
                         '生长进度零，成熟需要到七，生长进度七，成熟需要到七')
        self.assertEqual(normalize_spoken_text('age=8/7'), '生长阶段是七分之八')

    def test_cooldown_milliseconds_convert_to_seconds_without_rounding(self):
        self.assertEqual(normalize_spoken_text('cooldownMs=14000，cooldownRemainingMs:1250ms。'),
                         '冷却时间十四秒，冷却还剩一点二五秒。')
        self.assertEqual(normalize_spoken_text('cooldownMs=10000，cooldownRemainingMs=0。'),
                         '冷却时间十秒，冷却还剩零秒。')

    def test_resource_reading_is_current_and_maximum_not_a_fraction(self):
        self.assertEqual(normalize_spoken_text('health=6/20，food:18/20，mana 2.5/100。'),
                         '生命值六，满值二十，饱食度十八，满值二十，魔力二点五，满值一百。')

    def test_slash_fractions_do_not_become_dates_when_numerator_is_a_month_number(self):
        self.assertEqual(normalize_spoken_text('生命12/20，饥饿10/20，进度11/20，完成了1 / 2。'),
                         '生命二十分之十二，饥饿二十分之十，进度二十分之十一，完成了二分之一。')
        for numerator in range(1, 13):
            with self.subTest(numerator=numerator):
                expected = '二十分之' + chinese_number(str(numerator))
                self.assertEqual(normalize_spoken_text(f'{numerator}/20'), expected)
                self.assertEqual(normalize_spoken_text(f'{numerator}／20'), expected)
        self.assertEqual(normalize_spoken_text('2.5/100，-3/4，11/0。'),
                         '一百分之二点五，四分之负三，十一除以零。')

    def test_explicit_calendar_dates_and_fractions_keep_distinct_meanings(self):
        self.assertEqual(normalize_spoken_text('日期是11/20，生命11/20，今天是10/9，饥饿10/20。'),
                         '日期是十一月二十日，生命二十分之十一，今天是十月九日，饥饿二十分之十。')
        self.assertEqual(normalize_spoken_text('生日：2/29，纪念日在11/20，2026/10/09。'),
                         '生日：二月二十九日，纪念日在十一月二十日，二零二六年十月九日。')

    def test_fraction_conversion_preserves_identifiers_literals_and_multiple_slashes(self):
        text = '玩家11/20，v11/20，item_11/20，v11／20，/11/20，1/2/3，`11/20`，<11/20|fixture>，https://example.invalid/11/20。'
        self.assertEqual(normalize_spoken_text(text), text)
        normalized = normalize_spoken_text('11/20，日期11/20。')
        self.assertEqual(normalize_spoken_text(normalized), normalized)

    def test_literal_commands_hints_and_identity_are_preserved(self):
        text = '玩家锋利IV耐久III，Bob效率V耐久II，昵称123。`/mycli cast home`，`age=5/7`，<长|ZHANG3>大。https://example.invalid/age/123。'
        self.assertEqual(normalize_spoken_text(text), text)
        text = 'IndexTTS2.5、Qwen3.8-27B、CortiII、AdeleFelice、Bob_IV、III，stage5，damage_6，my_age。'
        self.assertEqual(normalize_spoken_text(text), text)

    def test_conversion_is_idempotent_and_keeps_factual_parentheses(self):
        original = '这把（锋利IV耐久III）给你。小麦age5/7，数量16。'
        normalized = normalize_spoken_text(original)
        self.assertEqual(normalize_spoken_text(normalized), normalized)
        self.assertIn('（锋利四级耐久三级）', normalized)

    def test_large_zero_groups_leading_zeroes_and_decimals(self):
        for token, spoken in [('0','零'),('10','十'),('11','十一'),('101','一百零一'),
                              ('1001','一千零一'),('10001','一万零一'),('100000001','一亿零一'),
                              ('100010001','一亿零一万零一'),('0123','零一二三'),
                              ('-0.5','负零点五'),('+2.00','正二点零零')]:
            with self.subTest(token=token):
                self.assertEqual(chinese_number(token), spoken)


if __name__ == '__main__':
    unittest.main()
