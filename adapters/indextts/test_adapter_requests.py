"""Mock adapter HTTP boundaries; no network, synthesis, audio playback, or subprocesses."""

import ast
import io
import json
from pathlib import Path
import types
import unittest
from unittest.mock import Mock
from unittest.mock import patch
import pronunciation


def load_adapter():
    """Keep the actual request handler, skip the process-only stdout wrapper."""
    path = Path(__file__).with_name("indextts_adapter.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    tree.body = [node for node in tree.body if not (
        isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
            and target.value.id == "sys" and target.attr == "stdout" for target in node.targets
        )
    )]
    module = types.ModuleType("adapter_request_test")
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.adapter = load_adapter()
        self.adapter.classify_mood_decision = Mock(return_value=("happy", 0.8))
        self.adapter.load_prefs = Mock(return_value={"voice": "taozi", "speed": 1.0})
        self.adapter.retempo = Mock(side_effect=lambda wav, speed: wav)
        self.adapter.urllib.request.urlopen = Mock(return_value=io.BytesIO(b"RIFFoffline"))
        self.handler = object.__new__(self.adapter.Handler)
        self.handler.path = "/v1/audio/speech"
        self.handler.log_message = Mock()
        self.handler._send = Mock()

    def tearDown(self):
        # urllib is a shared module; avoid leaving the mock installed for other tests.
        self.adapter.urllib.request.urlopen = self.original_urlopen

    def run(self, result=None):
        import urllib.request
        self.original_urlopen = urllib.request.urlopen
        return super().run(result)

    def post(self, body):
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.handler.headers = {"Content-Length": str(len(raw))}
        self.handler.rfile = io.BytesIO(raw)
        self.handler.do_POST()

    def request(self, text):
        self.post({"input": text})
        self.assertEqual(self.handler._send.call_args.args[0], 200)
        request = self.adapter.urllib.request.urlopen.call_args.args[0]
        return json.loads(request.data), self.adapter.retempo.call_args.args[1]

    def test_request_settings_override_preferences_for_whole_and_stream_without_mutating_them(self):
        prefs = {"voice": "fixture-default", "speed": 1.0, "emotion_mix": 0.35}
        self.adapter.load_prefs.return_value = prefs
        self.handler._stream = Mock()
        for path in ('/v1/audio/speech', '/v1/audio/speech/stream'):
            with self.subTest(path=path):
                self.handler.path = path
                self.post({'input': '我先去看看。' if path.endswith('/stream') else '大家好。', 'voice': ' fixture-override ', 'speed': 0.9,
                           'emotion_mix': 0, 'emotion_min_confidence': 0.9})
                if path.endswith('/stream'):
                    payload, speed, _, _ = self.handler._stream.call_args.args
                else:
                    payload = json.loads(self.adapter.urllib.request.urlopen.call_args.args[0].data)
                    speed = self.adapter.retempo.call_args.args[1]
                self.assertEqual(payload['voice'], 'fixture-override')
                self.assertEqual(speed, 0.9)
                self.assertNotIn('mood', payload)
                self.assertNotIn('emo_vector', payload)
                self.assertEqual(prefs, {'voice': 'fixture-default', 'speed': 1.0, 'emotion_mix': 0.35})

    def test_default_request_voice_preserves_adapter_preference_and_bounds_speed(self):
        self.adapter.load_prefs.return_value = {'voice': 'fixture-default', 'speed': 0.95, 'emotion_mix': 0}
        self.post({'input': '你好。', 'voice': 'default', 'speed': 9})
        payload = json.loads(self.adapter.urllib.request.urlopen.call_args.args[0].data)
        self.assertEqual(payload['voice'], 'fixture-default')
        self.assertEqual(self.adapter.retempo.call_args.args[1], 2)

    def test_low_confidence_omits_both_gateway_emotion_fields(self):
        self.adapter.classify_mood_decision.return_value = ("happy", 0.49)
        payload, speed = self.request("我先去河边看看。")
        self.assertNotIn("mood", payload)
        self.assertNotIn("emo_vector", payload)
        self.assertEqual(speed, 1)

    def test_calm_omits_both_fields_even_with_explicit_tag(self):
        payload, speed = self.request("（平静@0.8）我在河边。")
        self.assertNotIn("mood", payload)
        self.assertNotIn("emo_vector", payload)
        self.assertEqual(payload["input"], "我在河边。")
        self.assertEqual(speed, 1)
        self.adapter.classify_mood_decision.assert_not_called()

    def test_reported_sigh_never_enters_index_request_text(self):
        payload, _ = self.request('哎呀，又掉河里了[sigh] 不过这次好像离村庄更近了~ (calm) 正在游过去。')
        self.assertEqual(payload['input'], '哎呀，又掉河里了 不过这次好像离村庄更近了~ 正在游过去。')
        self.assertEqual(payload['voice'], 'taozi')
        self.assertNotIn('mood', payload)
        self.assertNotIn('emo_vector', payload)
        self.adapter.classify_mood_decision.assert_not_called()

    def test_stream_strips_acoustic_cues_before_segmentation(self):
        self.handler.path = '/v1/audio/speech/stream'
        self.handler._stream = Mock()
        raw = json.dumps({'input': '[SIGH]我先上岸。[breath]大家等一下。'}).encode('utf-8')
        self.handler.headers = {'Content-Length': str(len(raw))}
        self.handler.rfile = io.BytesIO(raw)
        self.handler.do_POST()
        payload, _, clean, _ = self.handler._stream.call_args.args
        self.assertEqual(payload['input'], '我先上岸。大家等一下。')
        self.assertEqual(clean, payload['input'])
        self.assertEqual(payload['voice'], 'taozi')

    def test_stream_budget_uses_preference_or_latency_default_and_reports_preparation(self):
        self.handler.path = '/v1/audio/speech/stream'
        self.handler._stream = Mock()
        for override in (None, 48, float('nan')):
            self.adapter._recent.clear()
            prefs = {'voice': 'fixture', 'speed': 1.0}
            if override is not None:
                prefs['stream_segment_tokens'] = override
            self.adapter.load_prefs.return_value = prefs
            self.post({'input': '我先去看看，再和大家打招呼。'})
            payload = self.handler._stream.call_args.args[0]
            expected = 48 if override == 48 else self.adapter.DEFAULT_STREAM_SEGMENT_TOKENS
            self.assertEqual(payload['max_text_tokens_per_segment'], expected)
            self.assertIsInstance(self.handler._stream.call_args.kwargs['request_started'], float)
            trace = self.adapter._last_preparation
            self.assertGreaterEqual(trace['preparation_ms'], trace['pronunciation_ms'])
            self.assertGreaterEqual(trace['preparation_ms'], trace['mood_ms'])
            self.handler.path = '/health'
            self.handler.do_GET()
            health = json.loads(self.handler._send.call_args.args[1])
            self.assertEqual(health['stream_segment_tokens'], expected)
            self.handler.path = '/v1/audio/speech/stream'

    def test_acoustic_cue_only_returns_silence_without_model_calls(self):
        self.handler._stream = Mock()
        for path in ('/v1/audio/speech', '/v1/audio/speech/stream'):
            self.handler.path = path
            raw = json.dumps({'input': '[sigh][breath][Uhm]'}).encode('utf-8')
            self.handler.headers = {'Content-Length': str(len(raw))}
            self.handler.rfile = io.BytesIO(raw)
            self.handler.do_POST()
            self.handler._send.assert_called_with(200, self.adapter.SILENT_WAV, 'audio/wav')
        self.adapter.classify_mood_decision.assert_not_called()
        self.adapter.urllib.request.urlopen.assert_not_called()
        self.handler._stream.assert_not_called()

    def test_unknown_tag_is_removed_and_body_selects_mood(self):
        payload, _ = self.request("（轻松@0.4）我在河边。")
        self.assertEqual(payload["input"], "我在河边。")
        self.adapter.classify_mood_decision.assert_called_once_with("我在河边。")
        self.assertEqual(payload["mood"], "happy")

    def test_tag_only_clip_reuses_silent_wav_without_classification_or_synthesis(self):
        self.handler._stream = Mock()
        for path in ("/v1/audio/speech", "/v1/audio/speech/stream"):
            with self.subTest(path=path):
                self.handler.path = path
                raw = json.dumps({"input": "（轻松@0.4）"}, ensure_ascii=False).encode("utf-8")
                self.handler.headers = {"Content-Length": str(len(raw))}
                self.handler.rfile = io.BytesIO(raw)
                self.handler.do_POST()
                self.handler._send.assert_called_with(200, self.adapter.SILENT_WAV, "audio/wav")
        self.adapter.classify_mood_decision.assert_not_called()
        self.adapter.urllib.request.urlopen.assert_not_called()
        self.handler._stream.assert_not_called()

    def test_explicit_happy_preserves_reference_and_requested_voice(self):
        payload, speed = self.request("（开心@0.8）这把锋利 II 的剑给你。")
        self.assertEqual(payload["voice"], "taozi")
        self.assertEqual(payload["input"], "这把锋利二级的剑给你。")
        self.assertEqual(payload["mood"], "happy")
        self.assertLessEqual(sum(payload["emo_vector"]), 0.45)
        self.assertEqual(payload["emo_vector"][7], 0)
        self.assertEqual(speed, 1.04)
        self.adapter.classify_mood_decision.assert_not_called()

    def test_natural_mode_preference_and_global_speed_remain_compatible(self):
        self.adapter.load_prefs.return_value = {"voice": "xiaoxue", "speed": 0.97, "emotion_mix": 0}
        payload, speed = self.request("这把（锋利 Ⅲ）给你。")
        self.assertEqual(payload["voice"], "xiaoxue")
        self.assertEqual(payload["input"], "这把（锋利三级）给你。")
        self.assertNotIn("mood", payload)
        self.assertNotIn("emo_vector", payload)
        self.assertEqual(speed, 0.97)

    def test_sentence_and_coordinates_are_not_split_or_removed(self):
        self.adapter.classify_mood_decision.return_value = ("calm", 0.8)
        text = "我在（1,2,3）。我先去河边，等你回来！"
        payload, _ = self.request(text)
        self.assertEqual(payload["input"], "我在（一,二,三）。我先去河边，等你回来！")
        self.adapter.urllib.request.urlopen.assert_called_once()

    def test_pronunciation_hints_reach_synthesis_without_entering_mood_or_dedup(self):
        text = '小麦长大了，看看木板的长短。'
        payload, _ = self.request(text)
        self.assertEqual(payload['input'], '小麦<长|ZHANG3>大了，看看木板的长短。')
        self.adapter.classify_mood_decision.assert_called_once_with(text)
        self.assertTrue(self.adapter.is_repeat(text))
        self.assertEqual(payload['voice'], 'taozi')

    def test_stream_keeps_readable_text_and_sends_phonetic_input(self):
        self.handler.path = '/v1/audio/speech/stream'
        self.handler._stream = Mock()
        raw = json.dumps({'input': '（平静）树苗正在生长，枝条的长度不同。'}).encode('utf-8')
        self.handler.headers = {'Content-Length': str(len(raw))}
        self.handler.rfile = io.BytesIO(raw)
        self.handler.do_POST()
        payload, _, clean, _ = self.handler._stream.call_args.args
        self.assertEqual(clean, '树苗正在生长，枝条的长度不同。')
        self.assertEqual(payload['input'], '树苗正在生<长|ZHANG3>，枝条的长度不同。')

    def test_both_endpoints_preserve_neutral_particles_and_trace_the_actual_model_text(self):
        alternatives = {'了': ['le5', 'liao3'], '的': ['de5', 'di4'],
                        '也': ['ye3', 'yi2'], '长': ['zhang3', 'chang2']}
        resolver = pronunciation.ContextPronunciation(
            lambda text: [[alternatives.get(char, [char])[0] for char in text]],
            phrases={'长大': ['ZHANG3', 'DA4']},
            alternatives=lambda char: alternatives.get(char, [char]))
        original = '小麦长大了，我的包也满了。'
        expected = '小麦<长|ZHANG3>大了，我的包也满了。'
        with patch.object(pronunciation, '_resolver', resolver):
            for path in ('/v1/audio/speech', '/v1/audio/speech/stream'):
                with self.subTest(path=path):
                    self.adapter._recent.clear()
                    self.handler.path = path
                    self.handler._stream = Mock()
                    self.post({'input': original})
                    payload = (self.handler._stream.call_args.args[0] if path.endswith('/stream') else
                               json.loads(self.adapter.urllib.request.urlopen.call_args.args[0].data))
                    self.assertEqual(payload['input'], expected)
                    traces = [args.args[1] for args in self.handler.log_message.call_args_list
                              if args.args[0] == 'text-prepared: %s']
                    trace = json.loads(traces[-1])
                    self.assertEqual((trace['script'], trace['spoken'], trace['synthesis']),
                                     (original, original, payload['input']))
                    self.assertEqual(trace['phonetic_hints'], 1)

    def test_health_reports_version_port_and_bounded_mix(self):
        self.adapter.load_prefs.return_value = {"voice": "taozi", "emotion_mix": 10}
        self.handler.path = "/health"
        self.handler.do_GET()
        health = json.loads(self.handler._send.call_args.args[1])
        self.assertEqual(health["version"], self.adapter.ADAPTER_VERSION)
        self.assertTrue(health["streaming"])
        self.assertEqual(health["streaming_granularity"], "text-segments")
        self.assertEqual(health["port"], 8010)
        self.assertEqual(health["emotion_mix"], 0.45)
        self.assertTrue(health["reference_prosody"])
        self.assertEqual(health['voice_cue_policy'], 'emotion-hints')
        self.assertFalse(health['native_acoustic_cues'])
        self.assertEqual(health['pronunciation_policy'], 'phrase-pinyin-fallback')
        self.assertFalse(health['pronunciation']['context_ready'])

    def test_empty_stream_probe_returns_without_synthesis(self):
        self.handler.path = '/v1/audio/speech/stream'
        self.handler.headers = {'Content-Length': '2'}
        self.handler.rfile = io.BytesIO(b'{}')
        self.handler.do_POST()
        self.assertEqual(self.handler._send.call_args.args[0], 400)
        self.adapter.urllib.request.urlopen.assert_not_called()

    def test_stream_uses_same_voice_emotion_and_level_normalization(self):
        self.handler.path = '/v1/audio/speech/stream'
        self.handler._stream = Mock()
        raw = json.dumps({'input': '（开心@0.3）我带着锋利 II 的剑。'}).encode('utf-8')
        self.handler.headers = {'Content-Length': str(len(raw))}
        self.handler.rfile = io.BytesIO(raw)
        self.handler.do_POST()
        payload, speed, clean, max_chars = self.handler._stream.call_args.args
        self.assertEqual(payload['voice'], 'taozi')
        self.assertEqual(clean, '我带着锋利二级的剑。')
        self.assertEqual(payload['input'], clean)
        self.assertLessEqual(sum(payload['emo_vector']), 0.45)
        self.assertEqual(speed, 1.04)
        self.assertEqual(payload['max_text_tokens_per_segment'], self.adapter.DEFAULT_STREAM_SEGMENT_TOKENS)
        self.assertEqual(max_chars, 40)
        self.adapter.classify_mood_decision.assert_not_called()

    def test_normal_request_reads_recorded_levels_and_growth_fields(self):
        text = '（平静）保护I耐久II荆棘III，数一下1、2、3。小麦age才5/7。'
        payload, _ = self.request(text)
        self.assertEqual(payload['input'],
                         '保护一级耐久二级荆棘三级，数一下一、二、三。小麦生<长|ZHANG3>进度五，成熟需要到七。')

    def test_stream_uses_readable_text_before_legacy_segmentation(self):
        self.handler.path = '/v1/audio/speech/stream'
        self.handler._stream = Mock()
        text = '（平静）效率V耐久II，age 5/7，cooldownRemainingMs=1250。'
        raw = json.dumps({'input': text}).encode('utf-8')
        self.handler.headers = {'Content-Length': str(len(raw))}
        self.handler.rfile = io.BytesIO(raw)
        self.handler.do_POST()
        payload, _, clean, _ = self.handler._stream.call_args.args
        self.assertEqual(clean, '效率五级耐久二级，生长进度五，成熟需要到七，冷却还剩一点二五秒。')
        self.assertEqual(payload['input'], '效率五级耐久二级，生<长|ZHANG3>进度五，成熟需要到七，冷却还剩一点二五秒。')
        self.assertNotIn('cooldownRemainingMs', payload['input'])


if __name__ == "__main__":
    unittest.main()
