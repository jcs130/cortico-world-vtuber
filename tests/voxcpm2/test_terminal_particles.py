"""Model-free boundaries for opt-in, voice-owned final speech particles."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'adapters/voxcpm2'))
from terminal_particles import normalize_terminal_particles


class TerminalParticlesTest(unittest.TestCase):
    def test_only_an_attached_final_particle_is_softened(self):
        self.assertEqual(normalize_terminal_particles('一起走喵。', {'喵': 'miao5'}), '一起走<喵|miao5>。')
        self.assertEqual(normalize_terminal_particles('来喵。回去喵！', {'喵': 'miao5'}), '来喵。回去<喵|miao5>！')
        self.assertEqual(normalize_terminal_particles('一起走喵', {'喵': 'miao5'}), '一起走<喵|miao5>')

    def test_isolated_calls_repetitions_and_internal_words_are_preserved(self):
        for text in ('喵。', '喵喵喵！', '学小猫喵喵。', '一起走，喵。', 'NEKO喵。', '喵在这里。', '喵喵叫。'):
            with self.subTest(text=text):
                self.assertEqual(normalize_terminal_particles(text, {'喵': 'miao5'}), text)

    def test_explicit_pronunciation_and_literal_quotes_code_or_urls_are_preserved(self):
        for text in ('一起走<喵|miao1>。', '一起走{miao1}。', '猫叫一声“喵”。', '`一起走喵`。',
                     '```一起走喵```', '打开 https://example.test/一起走喵。'):
            with self.subTest(text=text):
                self.assertEqual(normalize_terminal_particles(text, {'喵': 'miao5'}), text)

    def test_closing_quotes_wave_and_native_cues_keep_their_position(self):
        text = '她说“回去喵！”[sigh]'
        expected = '她说“回去<喵|miao5>！”[sigh]'
        self.assertEqual(normalize_terminal_particles(text, {'喵': 'miao5'}), expected)
        self.assertEqual(normalize_terminal_particles('一起走喵～[laugh]', {'喵': 'miao5'}), '一起走<喵|miao5>～[laugh]')
        self.assertEqual(normalize_terminal_particles(expected, {'喵': 'miao5'}), expected)

    def test_default_does_not_rewrite_and_other_configured_particles_are_supported(self):
        self.assertEqual(normalize_terminal_particles('一起走喵。'), '一起走喵。')
        self.assertEqual(normalize_terminal_particles('已经好啦。', {'啦': 'la5'}), '已经好<啦|la5>。')

    def test_invalid_configuration_is_rejected(self):
        for value in ([], {'': 'miao5'}, {'喵': 'miao'}, {'喵': 'miao5 extra5'}, {'喵': 1}, {'script': 'miao5'}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_terminal_particles('一起走喵。', value)


if __name__ == '__main__':
    unittest.main()
