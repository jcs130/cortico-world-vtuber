from pathlib import Path
import sys
import tempfile
import json
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'adapters/voxcpm2'))
from spoken_terms import SpokenTerms, load_catalog


class SpokenTermsTest(unittest.TestCase):
    def setUp(self):
        self.catalog = SpokenTerms({'schema': 'tts-spoken-terms-v1', 'terms': {
            'sculk': '幽匿块', 'minecraft:sculk': '幽匿块', 'sculk_sensor': '幽匿感测体',
            'iron_pickaxe': '铁镐', 'Iron Pickaxe': '铁镐', 'Stone': '石头',
            'stone sword': '石剑', 'stone_sword': '石剑',
        }})

    def test_ids_display_names_namespaces_and_case_use_complete_longest_terms(self):
        self.assertEqual(self.catalog.normalize('找sculk和SCULK_SENSOR，拿Iron Pickaxe，再用stone sword。'),
                         '找幽匿块和幽匿感测体，拿铁镐，再用石剑。')
        self.assertEqual(self.catalog.normalize('minecraft:sculk, Sculk. sculk？'), '幽匿块, 幽匿块. 幽匿块？')
        self.assertEqual(self.catalog.normalize('sculk方块和sculk 方块，Stone方块。'), '幽匿块和幽匿块，石头方块。')

    def test_unknown_ids_and_word_fragments_are_not_guessed(self):
        text = 'mod:sculk、sculk_sensor_extra、sculking、sculk.png、custom_stone、mystery_item。'
        self.assertEqual(self.catalog.normalize(text), text)

    def test_named_players_mentions_and_explicit_pronunciation_are_preserved(self):
        text = 'NEKO和玩家 Stone、昵称 sculk、@Stone、<sculk|custom>，拿stone。'
        self.assertEqual(self.catalog.normalize(text), 'NEKO和玩家 Stone、昵称 sculk、@Stone、<sculk|custom>，拿石头。')

    def test_code_urls_paths_and_commands_are_not_rewritten(self):
        text = '`sculk` ```stone sword``` https://host/sculk C:\\sculk /give sculk。找到sculk。'
        self.assertEqual(self.catalog.normalize(text), '`sculk` ```stone sword``` https://host/sculk C:\\sculk /give sculk。找到幽匿块。')

    def test_replacement_is_single_pass_and_idempotent(self):
        text = '找到sculk，拿iron_pickaxe。'
        converted = self.catalog.normalize(text)
        self.assertEqual(self.catalog.normalize(converted), converted)

    def test_catalog_validation_rejects_markup_empty_or_conflicting_terms(self):
        for terms in ({}, {'sculk': '[laughing]'}, {'bad\nkey': '幽匿块'}, {'sculk': ''}, {'sculk': '甲', 'SCULK': '乙'}):
            with self.subTest(terms=terms):
                with self.assertRaises(ValueError):
                    SpokenTerms({'schema': 'tts-spoken-terms-v1', 'terms': terms})

    def test_loading_reports_a_hash_without_exposing_local_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'terms.json'
            path.write_text(json.dumps({'schema': 'tts-spoken-terms-v1', 'terms': {'sculk': '幽匿块'}}), encoding='utf-8')
            catalog, status = load_catalog(path)
            self.assertEqual(catalog.normalize('找sculk。'), '找幽匿块。')
            self.assertEqual(status['entries'], 1)
            self.assertEqual(len(status['sha256']), 64)
            self.assertNotIn(directory, json.dumps(status))
