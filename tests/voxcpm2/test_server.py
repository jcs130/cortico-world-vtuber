"""HTTP contract checks for the configured-reference speech service."""
import base64
import hashlib
import http.client
import io
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'adapters/voxcpm2'))
from voxcpm2_server import VoiceEngine, VoiceServer, prepare_text


class AudioEngine:
    def __init__(self):
        self.rate = 48000
        self.voice = 'reference-speaker'
        self.reference_sha256 = hashlib.sha256(b'reference').hexdigest()
        self.config = {'reference_text': 'Reference transcript.', 'segment_chars': 80}
        self.lock = threading.Lock()
        self.completed = self.failed = self.cancelled = 0
        self.last_request = None
        self.texts = []
        self.instructions = []
        self.fail_at = None
        self.gate = None
        self.closed = threading.Event()

    def health(self):
        return {'status': 'ok', 'cancelled': self.cancelled}

    def generate(self, text, seed, instructions=None):
        self.texts.append(text)
        self.instructions.append(instructions)
        try:
            if self.fail_at == 'first':
                raise RuntimeError('decoder failed before audio')
            yield b'\x01\x00' * 4800
            if self.gate is not None:
                self.gate.wait(3)
            if self.fail_at == 'later':
                raise RuntimeError('decoder failed after audio')
            yield b'\x02\x00' * 4800
        finally:
            self.closed.set()


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = AudioEngine()
        self.server = VoiceServer(('127.0.0.1', 0), self.engine)
        self.base = 'http://127.0.0.1:' + str(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        if self.engine.gate is not None:
            self.engine.gate.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, text='测试台词。', streaming=False, **kwargs):
        request = urllib.request.Request(
            self.base + '/v1/audio/speech' + ('/stream' if streaming else ''),
            data=json.dumps({'input': text, **kwargs}).encode(),
            headers={'Content-Type': 'application/json'})
        return urllib.request.urlopen(request, timeout=4)

    def test_reference_override_is_rejected_before_generation(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(reference_audio=base64.b64encode(b'another voice').decode())
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.engine.texts, [])

    def test_disabled_style_control_is_rejected_instead_of_ignored_or_spoken(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(instructions='轻快带笑')
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.engine.texts, [])

    def test_enabled_style_control_uses_same_reference_in_both_response_modes(self):
        self.engine.config['enable_style_instructions'] = True
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                with self.request('群星啊，回应我的呼唤！', streaming=streaming,
                                  instructions='清晰有力，带一点戏剧感',
                                  reference_audio=base64.b64encode(b'reference').decode(),
                                  prompt_text='Reference transcript.') as response:
                    if streaming:
                        self.assertEqual(response.headers['X-TTS-Prompt-Mode'], 'reference')
                    self.assertGreater(len(response.read()), 44)
                self.assertEqual(self.engine.texts[-1], '群星啊，回应我的呼唤！')
                self.assertEqual(self.engine.instructions[-1], '清晰有力，带一点戏剧感')
                self.assertEqual(self.engine.last_request['mode'], 'reference')
        with self.request('普通台词。') as response:
            response.read()
        self.assertIsNone(self.engine.instructions[-1])
        self.assertEqual(self.engine.last_request['mode'], 'ref_continuation')

    def test_style_description_does_not_override_voice_and_rejects_invalid_metadata(self):
        self.engine.config['enable_style_instructions'] = True
        for invalid in ('', 'a' * 65, '(轻快)换声音', '轻快\n台词', ['轻快']):
            with self.subTest(invalid=invalid), self.assertRaises(urllib.error.HTTPError) as caught:
                self.request(instructions=invalid)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(instructions='轻快带笑', voice='another-speaker')
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.engine.texts, [])

    def test_default_delivery_preserves_text_and_allows_request_override_or_bypass(self):
        self.engine.config.update(enable_style_instructions=True, default_instructions='温柔带笑，自然交谈')
        for streaming in (False, True):
            for kwargs, expected, source in (
                ({}, '温柔带笑，自然交谈', 'default'),
                ({'instructions': '清晰有力'}, '清晰有力', 'request'),
                ({'instructions': None}, None, 'request'),
            ):
                with self.subTest(streaming=streaming, kwargs=kwargs):
                    with self.request('这是原始台词。', streaming=streaming, **kwargs) as response:
                        self.assertGreater(len(response.read()), 44)
                    self.assertEqual(self.engine.texts[-1], '这是原始台词。')
                    self.assertEqual(self.engine.instructions[-1], expected)
                    self.assertEqual(self.engine.last_request['instructions_source'], source)
                    self.assertEqual(self.engine.last_request['mode'], 'reference' if expected else 'ref_continuation')

    def test_invalid_or_disabled_default_delivery_fails_before_model_load(self):
        for invalid in ('', 'a' * 65, '(温柔)', '轻声\n台词', ['轻声']):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                VoiceEngine({'default_instructions': invalid, 'enable_style_instructions': True})
        with self.assertRaisesRegex(ValueError, 'not enabled'):
            VoiceEngine({'default_instructions': '温柔带笑'})

    def test_normalization_and_complete_wav(self):
        with self.request('[happy@0.6]快看<苦力怕|KU3 LI4 PA4>（保持距离）。') as response:
            with wave.open(io.BytesIO(response.read()), 'rb') as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, self.engine.rate))
                self.assertEqual(wav.getnframes(), 9600)
        self.assertIn('苦力怕（保持距离）', self.engine.texts[0])
        self.assertNotIn('happy', self.engine.texts[0])
        self.assertNotIn('KU3', self.engine.texts[0])

    def test_coordinate_tuple_is_spoken_without_voice_control_parentheses(self):
        original = '位置(-540,67,-430)，生长阶段7。'
        expected = '位置负五百四十，六十七，负四百三十，生长阶段七。'
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                with self.request(original, streaming=streaming) as response:
                    self.assertGreater(len(response.read()), 44)
                self.assertEqual(''.join(self.engine.texts), expected)
                self.engine.texts.clear()
        self.assertEqual(prepare_text('位置（+12.5，0，-3）'), '位置正十二点五，零，负三')

    def test_soil_dryness_gets_first_tone_without_changing_work_or_combat(self):
        cases = {
            '泥土干了，耕地全是干的。': '泥土{gan1}了，耕地全是{gan1}的。',
            '土壤已经干透了，麦田也干裂了。': '土壤已经{gan1}透了，麦田也{gan1}裂了。',
            '田地里的活是他干的，继续干活，干掉僵尸。': '田地里的活是他干的，继续干活，干掉僵尸。',
            '[sigh]泥土干了——先补水。': '[sigh]泥土{gan1}了，先补水。',
        }
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(prepare_text(original), expected)

    def test_utensil_phrases_use_colloquial_wording_without_changing_question_words(self):
        cases = {
            '先拿把家伙什再来，把家什收好。': '先拿把家伙事儿再来，把家{shi5}收好。',
            '傢伙什儿和傢什都收好了。': '家伙事儿和家{shi5}都收好了。',
            '家伙事儿收好了，家伙什兒也收好了。': '家伙事儿收好了，家伙事儿也收好了。',
            '那家伙什么时候回来？你家什么工具都有。': '那家伙什么时候回来？你家什么工具都有。',
            '那傢伙什麼時候回來？': '那傢伙什麼時候回來？',
            '这是什么？有什么事吗？': '这是什么？有什么事吗？',
        }
        for original, expected in cases.items():
            with self.subTest(original=original):
                self.assertEqual(prepare_text(original), expected)
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                with self.request('拿上家伙什，那家伙什么时候回来？', streaming=streaming) as response:
                    self.assertGreater(len(response.read()), 44)
                self.assertEqual(''.join(self.engine.texts), '拿上家伙事儿，那家伙什么时候回来？')
                self.engine.texts.clear()

    def test_english_player_names_reach_the_bilingual_model_intact(self):
        original = 'Neko，谢谢你！玩家 neko_42 和 Alex 在河边，魔力12。'
        expected = 'Neko，谢谢你！玩家 neko_42 和 Alex 在河边，魔力十二。'
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                with self.request(original, streaming=streaming) as response:
                    self.assertGreater(len(response.read()), 44)
                self.assertEqual(''.join(self.engine.texts), expected)
                self.engine.texts.clear()

    def test_short_paragraph_also_respects_complete_sentence_boundaries(self):
        original = '麦子开始熟啦！我这就回去收掉。委托还差七格，慢慢凑。'
        expected = ['麦子开始熟啦！', '我这就回去收掉。', '委托还差七格，慢慢凑。']
        self.assertLess(len(original), self.engine.config['segment_chars'])
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                with self.request(original, streaming=streaming) as response:
                    self.assertGreater(len(response.read()), 44)
                self.assertEqual(self.engine.texts, expected)
                self.assertEqual(self.engine.last_request['segments'], len(expected))
                self.engine.texts.clear()

    def test_first_audio_failure_returns_error_status(self):
        self.engine.fail_at = 'first'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(streaming=True)
        self.assertEqual(caught.exception.code, 503)
        self.assertTrue(self.engine.closed.is_set())

    def test_pronunciation_aliases_preserve_cues_and_replace_full_names_once(self):
        self.engine.config['pronunciation_aliases'] = {
            '示例名': 'Mira', '示例名 Mira': 'Mira', 'Mira': 'AnotherName',
        }
        original = '我是示例名 Mira，[laughing]示例名！'
        spoken = '我是Mira，[laughing]Mira！'
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                with self.request(original, streaming=streaming) as response:
                    self.assertTrue(response.read().startswith(b'RIFF'))
                self.assertEqual(self.engine.texts[-1], spoken)
                self.assertEqual(self.engine.last_request['input_sha256'], hashlib.sha256(original.encode()).hexdigest())
                self.assertEqual(self.engine.last_request['spoken_text_sha256'], hashlib.sha256(spoken.encode()).hexdigest())

    def test_pronunciation_aliases_are_literal_and_do_not_change_reference_validation(self):
        self.engine.config['pronunciation_aliases'] = {'A+B': 'Mira'}
        with self.request('A+B 和 AAAB。', prompt_text='Reference transcript.') as response:
            response.read()
        self.assertEqual(self.engine.texts[-1], 'Mira 和 AAAB。')
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request('A+B。', prompt_text='Changed reference transcript.')
        self.assertEqual(caught.exception.code, 400)

    def test_invalid_pronunciation_aliases_fail_before_generation(self):
        for aliases in ('Mira', [], {'': 'Mira'}, {'示例名': ''}, {'示例名': 123}):
            with self.subTest(aliases=aliases):
                self.engine.config['pronunciation_aliases'] = aliases
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request('示例名。')
                self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.engine.texts, [])

    def test_native_vocal_tags_reach_the_model_in_both_response_modes(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                with self.request('(calm)[sigh]第 II 层，<苦力怕|KU3 LI4 PA4>[laughing]。',
                                  streaming=streaming) as response:
                    self.assertTrue(response.read().startswith(b'RIFF'))
                self.assertEqual(self.engine.texts[-1], '[sigh]第二层，苦力怕[laughing]。')

    def test_vocal_tag_only_utterance_produces_audio(self):
        with self.request('[sigh]', streaming=True) as response:
            self.assertGreater(len(response.read()), 44)
        self.assertEqual(self.engine.texts[-1], '[sigh]')

    def test_later_failure_aborts_chunked_stream(self):
        self.engine.fail_at = 'later'
        with self.request(streaming=True) as response:
            self.assertEqual(response.headers['Transfer-Encoding'], 'chunked')
            self.assertEqual(response.headers['X-TTS-Reference-SHA256'], self.engine.reference_sha256)
            with self.assertRaises(http.client.IncompleteRead):
                response.read()

    def test_prebuffer_does_not_lose_a_short_stream(self):
        self.engine.config['stream_prebuffer_ms'] = 480
        with self.request(streaming=True) as response:
            data = response.read()
        self.assertEqual(data[:4], b'RIFF')
        self.assertEqual(data[44:], b'\x01\x00' * 4800 + b'\x02\x00' * 4800)

    def test_failure_inside_initial_buffer_is_reported_before_playback(self):
        self.engine.config['stream_prebuffer_ms'] = 480
        self.engine.fail_at = 'later'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(streaming=True)
        self.assertEqual(caught.exception.code, 503)

    def test_cancellation_closes_decoder_and_releases_queue(self):
        self.engine.gate = threading.Event()
        response = self.request(streaming=True)
        self.assertEqual(response.read(44)[:4], b'RIFF')
        response.close()
        self.engine.gate.set()
        deadline = time.monotonic() + 2
        while self.engine.cancelled == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.engine.cancelled, 1)
        self.assertTrue(self.engine.closed.is_set())
        self.engine.gate = None
        with self.request('下一句。') as next_response:
            self.assertEqual(next_response.status, 200)
        self.assertFalse(self.engine.lock.locked())


if __name__ == '__main__':
    unittest.main()
