"""Known mood tags share the adapter's registry and both request paths."""

import io
import json
import unittest
from unittest.mock import Mock, patch

from test_adapter_requests import load_adapter


class StageMoodRequestTests(unittest.TestCase):
    def setUp(self):
        self.adapter = load_adapter()
        self.adapter.classify_mood_decision = Mock(return_value=('happy', 0.8))
        self.adapter.load_prefs = Mock(return_value={'voice': 'taozi', 'speed': 1.0})
        self.adapter.retempo = Mock(side_effect=lambda wav, speed: wav)
        self.upstream = patch('urllib.request.urlopen', return_value=io.BytesIO(b'RIFFoffline'))
        self.urlopen = self.upstream.start()
        self.addCleanup(self.upstream.stop)
        self.handler = object.__new__(self.adapter.Handler)
        self.handler.log_message = Mock()
        self.handler._send = Mock()
        self.handler._stream = Mock()

    def request(self, text):
        raw = json.dumps({'input': text}).encode('utf-8')
        self.handler.headers = {'Content-Length': str(len(raw))}
        self.handler.rfile = io.BytesIO(raw)
        self.handler.do_POST()
        self.assertEqual(self.handler._send.call_args.args[0], 200)
        return json.loads(self.urlopen.call_args.args[0].data)

    def test_registered_unadorned_moods_do_not_reach_normal_or_stream_input(self):
        for cue in ('无奈', '无语', '委屈', '温暖', '激动', '炫耀', '恐慌', 'melancholic'):
            for path in ('/v1/audio/speech', '/v1/audio/speech/stream'):
                with self.subTest(cue=cue, path=path):
                    self.adapter._recent.clear()
                    self.adapter.classify_mood_decision.reset_mock()
                    self.handler.path = path
                    text = f'({cue}) 哎呀，背包满了！'
                    if path.endswith('/stream'):
                        self.handler._stream = Mock()
                        raw = json.dumps({'input': text}).encode('utf-8')
                        self.handler.headers = {'Content-Length': str(len(raw))}
                        self.handler.rfile = io.BytesIO(raw)
                        self.handler.do_POST()
                        payload, _, clean, _ = self.handler._stream.call_args.args
                        self.assertEqual(clean, '哎呀，背包满了！')
                    else:
                        payload = self.request(text)
                    self.assertEqual(payload['input'], '哎呀，背包满了！')
                    self.adapter.classify_mood_decision.assert_not_called()

    def test_registered_moods_only_match_complete_bracket_content(self):
        for text in ('我有点无奈，但先去收东西。', '（玩家无奈）来了。',
                     '（温暖的村庄）就在前方。', '（恐慌值3）是游戏参数。'):
            with self.subTest(text=text):
                self.assertEqual(self.adapter.strip_stage_dirs(text), (text, [], None, None))

    def test_each_registered_mood_alternative_is_recognized_without_duplicate_allowlist(self):
        for cue, mood in (('无奈', 'melancholic'), ('温暖', 'warm'), ('激动', 'happy'),
                          ('炫耀', 'playful'), ('恐慌', 'afraid')):
            with self.subTest(cue=cue):
                self.assertEqual(self.adapter.strip_stage_dirs(f'（{cue}）你好。'),
                                 ('你好。', [cue], mood, None))


if __name__ == '__main__':
    unittest.main()
