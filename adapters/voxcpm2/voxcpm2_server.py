"""Official VoxCPM2 Hi-Fi HTTP service with one configured reference and cached prompt features."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import struct
import sys
import threading
import time
import uuid
import wave
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'indextts'))
from corti_speech_style import strip_stage_directions
from speech_segments import punctuation_segments
from spoken_text import normalize_spoken_text
from inference_worker import InferenceWorker

VERSION = '1.2.3'
MAX_BODY = 2 * 1024 * 1024
CUE_RULES = [(re.compile(r'(?:开心|温柔|惊讶|害怕|生气|难过|平静|严肃|得意|委屈|happy|gentle|calm|serious)', re.I), 'style')]
_NUMBER_TOKEN = r'[-+]?\d+(?:\.\d+)?'
_COORDINATE_TUPLE = re.compile(
    rf'[（(]\s*({_NUMBER_TOKEN})\s*[,，]\s*({_NUMBER_TOKEN})\s*[,，]\s*({_NUMBER_TOKEN})\s*[）)]')
# Backend pronunciation fallback for unambiguous soil-dryness phrases.
_DRY_SOIL = re.compile(
    r'(?P<context>(?:泥土|土壤|耕地|田地|农田|麦田)'
    r'(?:(?:已经|还是|仍然|全是|都是|变得|有点|完全|太|很|更|都|也|又|全|是|就|快|晒|吹))*)'
    r'干(?=了|的|燥|旱|涸|裂|透|巴巴)')


def prepare_instructions(value, enabled: bool) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 64:
        raise ValueError('Instructions must contain 1 to 64 characters')
    value = value.strip()
    if re.search(r'[()（）\r\n<>\[\]【】]', value):
        raise ValueError('Instructions must be a plain description without brackets or newlines')
    if not enabled:
        raise ValueError('Style instructions are not enabled on this service')
    return value


def prepare_text(text: str, aliases: dict[str, str] | None = None) -> str:
    if aliases is not None and (
        not isinstance(aliases, dict)
        or any(not isinstance(key, str) or not key or not isinstance(value, str) or not value
               for key, value in aliases.items())
    ):
        raise ValueError('Pronunciation aliases must map nonempty strings to nonempty strings')
    clean, _, _, _ = strip_stage_directions(text, CUE_RULES, preserve_acoustic_cues=True)
    clean = re.sub(r'<([^<>|\n]+)\|[^<>\n]+>', r'\1', clean)
    if aliases:
        pattern = '|'.join(re.escape(key) for key in sorted(aliases, key=len, reverse=True))
        clean = re.sub(pattern, lambda match: aliases[match.group()], clean)
    clean = _COORDINATE_TUPLE.sub(lambda match: '，'.join(match.groups()), clean)
    clean = normalize_spoken_text(clean).replace('——', '，')
    return _DRY_SOIL.sub(lambda match: match['context'] + '{gan1}', clean)


def stream_header(rate: int) -> bytes:
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF', 0xFFFFFFFF, b'WAVE', b'fmt ',
                       16, 1, 1, rate, rate * 2, 2, 16, b'data', 0xFFFFFFFF)


def pcm_bytes(audio) -> bytes:
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not np.isfinite(samples).all():
        raise ValueError('Model generated nonfinite samples')
    return (np.clip(samples, -1, 1) * 32767).round().astype('<i2').tobytes()


class VoiceEngine:
    def __init__(self, config: dict):
        self.config = config
        self.default_instructions = prepare_instructions(
            config.get('default_instructions'), bool(config.get('enable_style_instructions', False)))
        self.voice = config['voice']
        self.reference_sha256 = hashlib.sha256(Path(config['reference_file']).read_bytes()).hexdigest()
        if self.reference_sha256 != config['reference_sha256']:
            raise ValueError('Configured reference audio hash mismatch')
        self.lock = threading.Lock()
        self.last_request = None
        self.completed = 0
        self.failed = 0
        self.cancelled = 0
        self.optimization = 'eager'
        self.cuda_stream = None
        self.worker = InferenceWorker(self._load_model, self._generate)

    def _load_model(self):
        import torch
        from voxcpm import VoxCPM

        config = self.config
        self.torch = torch
        torch.set_num_threads(int(config.get('threads', 4)))
        device = config.get('device', 'cuda')
        if device == 'cuda':
            free, _ = torch.cuda.mem_get_info()
            if free < int(config.get('minimum_free_gpu_mib', 7000)) * 1024 ** 2:
                raise RuntimeError('Insufficient free GPU memory to load the speech model')
            self.cuda_stream = torch.cuda.Stream(priority=int(config.get('cuda_priority', 0)))
        optimize = bool(config.get('optimize', False))
        if optimize:
            if device != 'cuda':
                raise ValueError('Compiled inference requires CUDA')
            import triton  # noqa: F401
        with self._stream_context():
            self.model = VoxCPM(config['model_dir'], enable_denoiser=False, optimize=optimize, device=device)
            self.rate = self.model.tts_model.sample_rate
            self.prompt_cache = self.model.tts_model.build_prompt_cache(
                reference_wav_path=config['reference_file'], prompt_wav_path=config['reference_file'],
                prompt_text=config['reference_text'])
            if self.prompt_cache['mode'] != 'ref_continuation':
                raise ValueError('Full reference plus continuation mode is required')
            self.style_cache = None
            if config.get('enable_style_instructions', False):
                self.style_cache = self.model.tts_model.build_prompt_cache(
                    reference_wav_path=config['reference_file'])
                if self.style_cache['mode'] != 'reference':
                    raise ValueError('Style instructions require reference-only cloning')
        if optimize:
            if not hasattr(self.model.tts_model.base_lm.forward_step, '_torchdynamo_orig_callable'):
                raise RuntimeError('Requested torch.compile optimization was not enabled')
            self.optimization = 'torch.compile-reduce-overhead'
        for text in config.get('warmup_texts', ['晚上好呀。']):
            warmup = self._generate(text, int(config.get('seed', 42)))
            try:
                if not sum(len(chunk) for chunk in warmup):
                    raise ValueError('Empty warmup audio')
            finally:
                warmup.close()
        if self.style_cache is not None:
            warmup = self._generate('这次发现很有趣。', int(config.get('seed', 42)),
                                    self.default_instructions or '轻快带笑，自然交谈')
            try:
                if not sum(len(chunk) for chunk in warmup):
                    raise ValueError('Empty controlled-cloning warmup audio')
            finally:
                warmup.close()

    def _stream_context(self):
        return self.torch.cuda.stream(self.cuda_stream) if self.cuda_stream is not None else nullcontext()

    def generate(self, text: str, seed: int, instructions: str | None = None):
        options = {'instructions': instructions} if instructions else {}
        yield from self.worker.generate(text, seed, **options)

    def _generate(self, text: str, seed: int, instructions: str | None = None):
        cache = self.prompt_cache
        if instructions:
            if self.style_cache is None:
                raise ValueError('Style instructions are not enabled')
            cache = self.style_cache
            text = f'({instructions}){text}'
        with self._stream_context():
            self.torch.manual_seed(seed)
            iterator = self.model.tts_model.generate_with_prompt_cache_streaming(
                target_text=text, prompt_cache=cache, min_len=2,
                max_len=int(self.config.get('max_steps', 200)),
                cfg_value=float(self.config.get('cfg_value', 2.0)),
                inference_timesteps=int(self.config.get('inference_timesteps', 10)),
                retry_badcase=False)
            try:
                count = 0
                for audio, _, _ in iterator:
                    count += 1
                    yield pcm_bytes(audio.squeeze(0).cpu().numpy())
                if count >= int(self.config.get('max_steps', 200)):
                    raise RuntimeError('Speech reached the generation limit; use shorter clauses')
            finally:
                iterator.close()

    def health(self):
        return {'status': 'ok', 'backend': 'voxcpm2-official-hifi', 'version': VERSION,
                'streaming': True, 'streaming_granularity': 'latent-patches',
                'voice': self.voice, 'voice_policy': 'configured-reference-only',
                'reference_sha256': self.reference_sha256,
                'prompt_mode': 'reference' if self.default_instructions else 'ref_continuation',
                'default_instructions': self.default_instructions,
                'reference_features_cached': True,
                'style_instructions_supported': self.style_cache is not None,
                'style_prompt_mode': 'reference' if self.style_cache is not None else None,
                'style_instruction_max_chars': 64,
                'native_acoustic_cues': True,
                'sentence_segmentation': True,
                'pronunciation_fallbacks': ['soil-dryness-gan1'],
                'device': self.config.get('device', 'cuda'), 'model_revision': self.config.get('model_revision'),
                'package': 'voxcpm==2.0.3', 'torch': self.torch.__version__,
                'optimization': self.optimization, 'inference_worker_alive': self.worker.thread.is_alive(),
                'cuda_priority': self.config.get('cuda_priority', 0),
                'stream_prebuffer_ms': self.config.get('stream_prebuffer_ms', 0),
                'pid': os.getpid(), 'completed': self.completed, 'failed': self.failed,
                'cancelled': self.cancelled, 'last_request': self.last_request}


class VoiceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, engine):
        self.engine = engine
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        print(json.dumps({'event': 'http', 'message': fmt % args}, ensure_ascii=False), flush=True)

    def reply(self, status: int, body: bytes, content_type='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        self.wfile.write(body)

    def disconnected(self) -> bool:
        readable, _, _ = select.select([self.connection], [], [], 0)
        return bool(readable) and not self.connection.recv(1, socket.MSG_PEEK)

    def do_GET(self):
        if self.path.rstrip('/') in ('/health', '/v1/health'):
            self.reply(200, json.dumps(self.server.engine.health()).encode())
        elif self.path.rstrip('/') == '/voices':
            self.reply(200, json.dumps({'voices': [self.server.engine.voice]}).encode())
        else:
            self.reply(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path.rstrip('/') not in ('/v1/audio/speech', '/v1/audio/speech/stream'):
            self.reply(404, b'{"error":"not found"}')
            return
        engine = self.server.engine
        started = time.monotonic()
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                raise ValueError('Request body size is invalid')
            body = json.loads(self.rfile.read(length))
            text = body.get('input', body.get('text', ''))
            if not isinstance(text, str) or not text.strip() or len(text) > 1200:
                raise ValueError('Input must contain 1 to 1200 characters')
            if body.get('voice', 'default') not in ('', 'default', engine.voice):
                raise ValueError('Only the configured reference voice is available')
            if not math.isclose(float(body.get('speed', 1.0)), 1.0):
                raise ValueError('This Hi-Fi service uses the reference speaking pace; speed must be 1')
            if body.get('reference_audio'):
                reference = base64.b64decode(body['reference_audio'], validate=True)
                if hashlib.sha256(reference).hexdigest() != engine.reference_sha256:
                    raise ValueError('Request reference differs from the configured voice')
            if body.get('prompt_text') and body['prompt_text'] != engine.config['reference_text']:
                raise ValueError('Request transcript differs from the configured reference')
            instructions = prepare_instructions(
                body.get('instructions', engine.config.get('default_instructions')),
                bool(engine.config.get('enable_style_instructions', False)))
            seed = int(body.get('seed', engine.config.get('seed', 42)))
            if not 0 <= seed < 2 ** 31:
                raise ValueError('Invalid seed')
            clean = prepare_text(text, engine.config.get('pronunciation_aliases'))
            limit = int(engine.config.get('segment_chars', 80))
            texts = punctuation_segments(clean, max_chars=limit)
            if any(len(segment) > 200 for segment in texts):
                raise ValueError('An unpunctuated clause exceeds 200 characters')
        except (ValueError, TypeError, AttributeError) as exc:
            self.reply(400, json.dumps({'error': str(exc)}).encode())
            return
        if not clean.strip():
            output = io.BytesIO()
            with wave.open(output, 'wb') as wav:
                wav.setparams((1, 2, engine.rate, 0, 'NONE', ''))
                wav.writeframes(bytes(round(engine.rate * 0.05) * 2))
            self.reply(200, output.getvalue(), 'audio/wav')
            return

        acquired, sent, iterator = False, False, None
        request_id = str(uuid.uuid4())
        pcm_count, pcm_hash, segments = 0, hashlib.sha256(), 0
        record = {'id': request_id, 'input_sha256': hashlib.sha256(text.encode()).hexdigest(),
                  'input_chars': len(text), 'spoken_chars': len(clean), 'seed': seed,
                  'segment_chars': limit, 'segment_lengths': [len(segment) for segment in texts],
                  'spoken_text_sha256': hashlib.sha256(clean.encode()).hexdigest(),
                  'reference_sha256': engine.reference_sha256,
                  'mode': 'reference' if instructions else 'ref_continuation',
                  'instructions_source': 'request' if 'instructions' in body else 'default' if instructions else 'none',
                  'instructions_chars': len(instructions or ''),
                  'instructions_sha256': hashlib.sha256(instructions.encode()).hexdigest() if instructions else None}
        try:
            deadline = started + float(engine.config.get('queue_timeout_sec', 30))
            while not engine.lock.acquire(timeout=0.1):
                if self.disconnected():
                    raise ConnectionAbortedError('Client disconnected while queued')
                if time.monotonic() > deadline:
                    raise TimeoutError('Speech queue timeout')
            acquired = True
            if self.disconnected():
                raise ConnectionAbortedError('Client disconnected before synthesis')
            record['queue_ms'] = round((time.monotonic() - started) * 1000)
            parts = []
            streaming = self.path.rstrip('/').endswith('/stream')
            pending = []
            pending_bytes = 0
            prebuffer_ms = min(2000, max(0, float(engine.config.get('stream_prebuffer_ms', 0))))
            prebuffer_bytes = round(prebuffer_ms * engine.rate * 2 / 1000)
            for segment in texts:
                if self.disconnected():
                    raise ConnectionAbortedError('Client disconnected between segments')
                iterator = engine.generate(segment, seed, instructions=instructions) if instructions else engine.generate(segment, seed)
                segments += 1
                for pcm in iterator:
                    if self.disconnected():
                        raise ConnectionAbortedError('Client disconnected during synthesis')
                    if not pcm:
                        continue
                    if 'first_pcm_ms' not in record:
                        record['first_pcm_ms'] = round((time.monotonic() - started) * 1000)
                    pcm_count += len(pcm)
                    pcm_hash.update(pcm)
                    if streaming:
                        if not sent:
                            pending.append(pcm)
                            pending_bytes += len(pcm)
                            if pending_bytes < prebuffer_bytes:
                                continue
                            record['first_output_ms'] = round((time.monotonic() - started) * 1000)
                            self.open_stream(engine, record)
                            sent = True
                            for buffered in pending:
                                self.chunk(buffered)
                            pending.clear()
                        else:
                            self.chunk(pcm)
                    else:
                        parts.append(pcm)
                iterator.close()
                iterator = None
            if not pcm_count:
                raise ValueError('Model returned no audio')
            if streaming:
                if not sent:
                    record['first_output_ms'] = round((time.monotonic() - started) * 1000)
                    self.open_stream(engine, record)
                    sent = True
                    for buffered in pending:
                        self.chunk(buffered)
                record['stream_prebuffer_ms'] = prebuffer_ms
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
            else:
                output = io.BytesIO()
                with wave.open(output, 'wb') as wav:
                    wav.setparams((1, 2, engine.rate, 0, 'NONE', ''))
                    wav.writeframes(b''.join(parts))
                self.reply(200, output.getvalue(), 'audio/wav')
            engine.completed += 1
            record['outcome'] = 'completed'
        except (BrokenPipeError, ConnectionError) as exc:
            engine.cancelled += 1
            record.update(outcome='cancelled', error=type(exc).__name__)
        except Exception as exc:
            engine.failed += 1
            record.update(outcome='failed', error=str(exc))
            if not sent:
                self.reply(503, json.dumps({'error': str(exc), 'request_id': request_id}).encode())
            else:
                self.connection.shutdown(socket.SHUT_RDWR)
        finally:
            if iterator is not None:
                iterator.close()
            if acquired:
                engine.lock.release()
            self.close_connection = True
            record.update(total_ms=round((time.monotonic() - started) * 1000), segments=segments,
                          audio_ms=round(pcm_count / 2 / engine.rate * 1000), pcm_sha256=pcm_hash.hexdigest())
            engine.last_request = record
            print(json.dumps({'event': 'synthesis', **record}, ensure_ascii=False), flush=True)

    def chunk(self, data: bytes):
        self.wfile.write(('%X\r\n' % len(data)).encode() + data + b'\r\n')
        self.wfile.flush()

    def open_stream(self, engine, record):
        self.send_response(200)
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Transfer-Encoding', 'chunked')
        self.send_header('Connection', 'close')
        self.send_header('X-TTS-Backend', 'voxcpm2-official-hifi')
        self.send_header('X-TTS-Reference-SHA256', engine.reference_sha256)
        self.send_header('X-TTS-Prompt-Mode', record['mode'])
        self.send_header('X-TTS-Request-Id', record['id'])
        self.send_header('X-TTS-Request-First-Audio-Ms', str(record['first_output_ms']))
        self.end_headers()
        self.chunk(stream_header(engine.rate))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    engine = VoiceEngine(config)
    server = VoiceServer((config.get('host', '127.0.0.1'), int(config.get('port', 8014))), engine)
    print(json.dumps({'event': 'ready', **engine.health()}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        engine.worker.close()


if __name__ == '__main__':
    main()
