"""Offline YuE2 provider contract: no models or network required."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('gateway_yue_test', Path(__file__).resolve().parents[2] / 'src/music-generation-gateway.py')
g = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(g)

ABC = '''X:1
T:
M:3/4
L:1/16
Q:1/4=90
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
% instrumental
V: Vocal
"C"C4E4G4|
V: Ins
Z|
'''
REQUEST = dict(requestKey='one', title='Piano', requestText='Original piano background music',
               instrumental=True, style='Solo piano, 3/4 waltz, no vocals', durationSec=45)


class YuETest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = dict(stateDir=self.temp.name, musicDir=self.temp.name + '/music', musicModel='yue2',
                        generationMode='text-to-music', voiceConversion={'enabled': False},
                        comfy={'endpoint': 'http://offline.invalid'}, review={'endpoint': 'http://review.invalid', 'model': 'review'})
        self.cfg = g.normalize_config(self.cfg)
        self.posts, self.history = [], {}
        self.fail_post = False
        def call(url, data=None, timeout=0):
            if url.endswith('/prompt'):
                self.posts.append(data['prompt'])
                if self.fail_post:
                    raise TimeoutError('Outcome unknown')
                return {'prompt_id': 'p' + str(len(self.posts))}
            pid = url.rsplit('/', 1)[-1]
            return {pid: self.history[pid]} if pid in self.history else {}
        self.provider = g.yue_module.YuE2(self.cfg, call, g.atomic_json, g.NeedsReview)
        self.req = g.validate_request(REQUEST)
        self.identity = self.provider.submit(self.req, 'a' * 32)

    def plan_ready(self, abc=ABC):
        self.history['p1'] = {'status': {'completed': True, 'status_str': 'success'}, 'outputs': {'3': {'text': [abc]}}}

    def test_transfer_preserves_pitch_timing_meter_and_omits_words(self):
        self.plan_ready()
        self.assertEqual(self.provider.poll(self.identity)['state'], 'running')
        render = self.posts[-1]
        score = g.yue_module.parse_abc(render['4']['inputs']['abc'])
        self.assertEqual(score.voices['Vocal'].notes, [])
        self.assertEqual(len(score.voices['Ins'].notes), 3)
        self.assertIn('M:3/4', render['4']['inputs']['abc'])
        self.assertEqual(render['4']['inputs']['lyrics'], '[Instrumental]')
        self.assertEqual(render['8']['inputs']['steps'], 32)
        self.assertEqual(render['20']['inputs']['volume'], -6)
        self.history['p2'] = {'status': {'completed': True, 'status_str': 'success'}, 'outputs': {'10': {'audio': [{'filename': 'a.flac', 'type': 'output', 'subfolder': 'audio'}]}}}
        self.assertEqual(self.provider.poll(self.identity)['state'], 'done')
        self.assertEqual(len(self.posts), 2)

    def test_cancel_after_plan_never_launches_audio(self):
        self.plan_ready()
        self.assertEqual(self.provider.poll(self.identity, lambda: True)['state'], 'cancelled')
        self.assertEqual(len(self.posts), 1)

    def test_cancel_during_completed_history_check_never_launches_audio(self):
        self.plan_ready()
        checks = iter([False, True])
        self.assertEqual(self.provider.poll(self.identity, lambda: next(checks))['state'], 'cancelled')
        self.assertEqual(len(self.posts), 1)

    def test_ambiguous_second_post_never_retries_after_restart(self):
        self.plan_ready()
        self.fail_post = True
        with self.assertRaises(TimeoutError):
            self.provider.poll(self.identity)
        restarted = g.yue_module.YuE2(self.cfg, self.provider.call, g.atomic_json, g.NeedsReview)
        with self.assertRaises(g.NeedsReview):
            restarted.poll(self.identity)
        self.assertEqual(len(self.posts), 2)

    def test_restart_resumes_without_duplicate_submission(self):
        restarted = g.yue_module.YuE2(self.cfg, self.provider.call, g.atomic_json, g.NeedsReview)
        self.assertEqual(restarted.poll(self.identity)['state'], 'running')
        self.assertEqual(len(self.posts), 1)

    def test_invalid_score_gets_one_retry_with_same_meter_request(self):
        self.plan_ready('invalid')
        self.provider.poll(self.identity)
        style = self.posts[-1]['2']['inputs']['style']
        self.assertIn('3/4', style)
        self.assertNotIn('4/4', style)
        self.history['p2'] = self.history['p1']
        with self.assertRaises(g.NeedsReview):
            self.provider.poll(self.identity)
        self.assertEqual(len(self.posts), 2)

    def test_instrumental_request_and_decode_fail_closed(self):
        self.assertEqual(self.req['lyrics'], '')
        for changes in ({'lyrics': 'lalala'}, {'instrumental': 'true'}):
            with self.assertRaises(g.GatewayError):
                g.validate_request({**REQUEST, **changes})
        empty = dict(text='', segments=[], recognizer='qwen3', noSpeechDecoded=True, decodeCompleted=True)
        g.validate_instrumental_transcript(empty, 'qwen3')
        for changes in ({'text': 'hello'}, {'decodeCompleted': False}, {'noSpeechDecoded': False}, {'recognizer': 'whisper'}):
            with self.assertRaises(g.NeedsReview):
                g.validate_instrumental_transcript({**empty, **changes}, 'qwen3')


if __name__ == '__main__':
    unittest.main()
