"""Offline stream framing, error, and real continuous tempo checks."""

import io
import math
from pathlib import Path
import struct
import threading
import unittest
import wave

from stream_audio import SegmentedPcmSource, sentence_segments, pcm_chunks, read_pcm_header, tempo_chunks, wav_stream_header
from test_adapter_requests import load_adapter
from unittest.mock import Mock


class Fragmented:
    def __init__(self, parts):
        self.parts = iter(parts)
        self.pending = b''
        self.closed = False

    def read(self, length):
        if not self.pending:
            self.pending = next(self.parts, b'')
        data, self.pending = self.pending[:length], self.pending[length:]
        return data

    def close(self):
        self.closed = True


class StreamTests(unittest.TestCase):
    def test_sentence_boundaries_preserve_input_without_midword_splitting(self):
        text = '先收好钓竿，再去岸上看看。魔力有12.5点。这里有伙伴，我去打个招呼。'
        segments = sentence_segments(text, 16)
        self.assertEqual(''.join(segments), text)
        self.assertGreater(len(segments), 1)
        self.assertTrue(any('12.5' in part for part in segments))
        self.assertEqual(sentence_segments('这是一句没有标点的完整说话内容' * 3, 16), ['这是一句没有标点的完整说话内容' * 3])

    def test_legacy_source_first_pcm_does_not_wait_for_later_synthesis(self):
        calls = []
        def synthesize(text):
            calls.append(text)
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setparams((1, 2, 22050, 0, 'NONE', ''))
                wav.writeframes(b'\x01\x00' * 12)
            return output.getvalue()
        source = SegmentedPcmSource(['first', 'second'], synthesize)
        self.assertEqual(read_pcm_header(source), 22050)
        self.assertEqual(source.read(8192), b'\x01\x00' * 12)
        self.assertEqual(calls, ['first'])
        source.close()
        self.assertEqual(source.read(8192), b'')
        self.assertEqual(calls, ['first'])

    def test_fragmented_header_and_pcm_preserve_all_samples(self):
        header = wav_stream_header(22050)
        response = Fragmented([header[:3], header[3:20], header[20:] + b'\x01', b'\x02\x03', b'\x04'])
        self.assertEqual(read_pcm_header(response), 22050)
        self.assertEqual(b''.join(pcm_chunks(response)), b'\x01\x02\x03\x04')

    def test_reject_header_extensions_stereo_and_truncated_sample(self):
        header = bytearray(wav_stream_header(22050))
        header[36:40] = b'LIST'
        with self.assertRaises(ValueError):
            read_pcm_header(io.BytesIO(header))
        header = bytearray(wav_stream_header(22050))
        struct.pack_into('<H', header, 22, 2)
        with self.assertRaises(ValueError):
            read_pcm_header(io.BytesIO(header))
        with self.assertRaises(ValueError):
            list(pcm_chunks(io.BytesIO(b'\x00')))

    def test_http_first_audio_precedes_later_source_consumption(self):
        adapter = load_adapter()
        original = adapter.urllib.request.urlopen
        events = []
        class Source(Fragmented):
            def read(self, length):
                events.append('read')
                return super().read(length)
        response = Source([wav_stream_header(22050), b'\x01\x00' * 8192, b'\x02\x00' * 4096])
        adapter.urllib.request.urlopen = Mock(return_value=response)
        handler = object.__new__(adapter.Handler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.log_message = Mock()
        handler._send = Mock()
        handler._chunk = lambda chunk: events.append(('sent', chunk))
        handler.wfile = io.BytesIO()
        try:
            handler._stream({'voice': 'test', 'input': 'hello'}, 1.0, 'hello')
        finally:
            adapter.urllib.request.urlopen = original
        first = next(i for i, event in enumerate(events) if isinstance(event, tuple))
        self.assertIn('read', events[first + 1:])
        self.assertTrue(events[first][1].startswith(b'RIFF'))
        self.assertTrue(response.closed)
        self.assertEqual(handler.wfile.getvalue(), b'0\r\n\r\n')
        handler._send.assert_not_called()

    def test_error_after_pcm_closes_http_without_success_terminator(self):
        adapter = load_adapter()
        original = adapter.urllib.request.urlopen
        response = Fragmented([wav_stream_header(22050), b'\x00\x01' * 8192, b'\x00'])
        adapter.urllib.request.urlopen = Mock(return_value=response)
        handler = object.__new__(adapter.Handler)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.log_message = Mock()
        handler._send = Mock()
        handler._chunk = Mock()
        handler.wfile = io.BytesIO()
        try:
            handler._stream({'voice': 'test', 'input': 'hello'}, 1.0, 'hello')
        finally:
            adapter.urllib.request.urlopen = original
        self.assertTrue(handler.close_connection)
        self.assertEqual(handler.wfile.getvalue(), b'')
        self.assertTrue(response.closed)
        handler._send.assert_not_called()

    @unittest.skipUnless(Path(r'C:\Python313\ffmpeg.exe').exists(), 'requires local ffmpeg')
    def test_tempo_outputs_audio_before_upstream_completes(self):
        release = threading.Event()
        input_pcm = b''.join(struct.pack('<h', int(6000 * math.sin(2 * math.pi * 440 * i / 22050))) for i in range(22050 * 3))
        class Source(Fragmented):
            def read(self, length):
                if not self.pending and self.parts_done:
                    if not release.wait(timeout=5):
                        raise TimeoutError('test did not release upstream')
                    return b''
                data = super().read(length)
                if not data:
                    self.parts_done = True
                    if not release.wait(timeout=5):
                        raise TimeoutError('test did not release upstream')
                return data
        response = Source([input_pcm])
        response.parts_done = False
        chunks = tempo_chunks(response, 22050, 1.04, r'C:\Python313\ffmpeg.exe')
        try:
            first = next(chunks)
            self.assertFalse(release.is_set())
            self.assertGreater(len(first), 0)
            release.set()
            output = first + b''.join(chunks)
            self.assertAlmostEqual(len(output) / len(input_pcm), 1 / 1.04, delta=0.02)
        finally:
            release.set()
            chunks.close()
        self.assertTrue(response.closed)


if __name__ == '__main__':
    unittest.main()
