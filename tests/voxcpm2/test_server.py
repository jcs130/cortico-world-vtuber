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
from voxcpm2_server import VoiceServer


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
        self.fail_at = None
        self.gate = None
        self.closed = threading.Event()

    def health(self):
        return {'status': 'ok', 'cancelled': self.cancelled}

    def generate(self, text, seed):
        self.texts.append(text)
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

    def test_normalization_and_complete_wav(self):
        with self.request('[happy@0.6]快看<苦力怕|KU3 LI4 PA4>（保持距离）。') as response:
            with wave.open(io.BytesIO(response.read()), 'rb') as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, self.engine.rate))
                self.assertEqual(wav.getnframes(), 9600)
        self.assertIn('苦力怕（保持距离）', self.engine.texts[0])
        self.assertNotIn('happy', self.engine.texts[0])
        self.assertNotIn('KU3', self.engine.texts[0])

    def test_first_audio_failure_returns_error_status(self):
        self.engine.fail_at = 'first'
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request(streaming=True)
        self.assertEqual(caught.exception.code, 503)
        self.assertTrue(self.engine.closed.is_set())

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
