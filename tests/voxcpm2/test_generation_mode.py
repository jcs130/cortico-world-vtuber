"""Validate the mode passed to the official decoder without loading another GPU model."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'adapters/voxcpm2'))
from voxcpm2_server import VoiceEngine


class GenerationModeTest(unittest.TestCase):
    def test_description_reaches_reference_only_decoder_and_normal_speech_keeps_hifi(self):
        calls = []
        class Audio:
            def squeeze(self, _): return self
            def cpu(self): return self
            def numpy(self): return np.ones(8, dtype=np.float32) * 0.2
        def generate(**kwargs):
            calls.append(kwargs)
            yield Audio(), None, None
        engine = VoiceEngine.__new__(VoiceEngine)
        engine.config = {'max_steps': 200}
        engine.cuda_stream = None
        engine.torch = SimpleNamespace(manual_seed=lambda _: None)
        engine.model = SimpleNamespace(tts_model=SimpleNamespace(generate_with_prompt_cache_streaming=generate))
        engine.prompt_cache = {'mode': 'ref_continuation'}
        engine.style_cache = {'mode': 'reference'}
        self.assertTrue(list(engine._generate('群星啊，回应我的呼唤！', 42, '清晰有力，带一点戏剧感')))
        self.assertTrue(list(engine._generate('普通台词。', 42)))
        self.assertIs(calls[0]['prompt_cache'], engine.style_cache)
        self.assertEqual(calls[0]['target_text'], '(清晰有力，带一点戏剧感)群星啊，回应我的呼唤！')
        self.assertIs(calls[1]['prompt_cache'], engine.prompt_cache)
        self.assertEqual(calls[1]['target_text'], '普通台词。')
        engine.style_cache = None
        with self.assertRaisesRegex(ValueError, 'not enabled'):
            list(engine._generate('台词。', 42, '轻快'))


if __name__ == '__main__':
    unittest.main()
