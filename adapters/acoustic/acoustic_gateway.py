"""Cached acoustic cues in the PCM16 speech protocol used by TtsClient."""
from __future__ import annotations

import argparse
from array import array
import asyncio
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import time
import uuid
import wave

from aiohttp import ClientSession, ClientTimeout, web

MAX_BODY = 2 * 1024 * 1024
HOP_HEADERS = {'connection', 'keep-alive', 'transfer-encoding', 'content-length',
               'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'upgrade'}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wav_header(rate: int, size: int = 0xFFFFFFFF) -> bytes:
    return struct.pack('<4sI4s4sIHHIIHH4sI', b'RIFF',
                       0xFFFFFFFF if size == 0xFFFFFFFF else size + 36,
                       b'WAVE', b'fmt ', 16, 1, 1, rate, rate * 2, 2, 16, b'data', size)


def check_stream_header(data: bytes, rate: int) -> None:
    expected = wav_header(rate)
    if len(data) != 44 or data[:4] != b'RIFF' or data[8:40] != expected[8:40]:
        raise RuntimeError('Upstream must return mono PCM16 with a 44-byte WAV header at the catalog sample rate')


class Catalog:
    def __init__(self, path: Path):
        raw = path.read_bytes()
        manifest = json.loads(raw)
        if manifest.get('version') != 1:
            raise ValueError('Unsupported acoustic catalog version')
        self.digest = sha256(raw)
        self.voice = manifest['voice']
        self.reference_sha256 = manifest['reference_sha256']
        self.reference_text_sha256 = manifest['reference_text_sha256']
        self.rate = manifest['sample_rate']
        if not isinstance(self.rate, int) or not 8000 <= self.rate <= 96000:
            raise ValueError('Invalid catalog sample rate')
        for digest in [self.reference_sha256, self.reference_text_sha256]:
            if not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('Invalid reference digest')
        self.assets: dict[str, list[dict]] = {}
        self.indices: dict[str, int] = {}
        ids = set()
        root = path.parent.resolve()
        for entry in manifest['assets']:
            if entry['id'] in ids or not re.fullmatch('[A-Za-z0-9_-]+', entry['id']):
                raise ValueError('Acoustic asset ids must be unique')
            ids.add(entry['id'])
            cue = entry['cue']
            if not re.fullmatch('[A-Za-z][A-Za-z0-9-]*', cue):
                raise ValueError('Invalid acoustic cue name')
            source = (root / entry['file']).resolve()
            if not source.is_relative_to(root):
                raise ValueError('Acoustic asset must be inside the catalog directory')
            raw = source.read_bytes()
            if sha256(raw) != entry['sha256']:
                raise ValueError(f"Acoustic asset hash mismatch: {entry['id']}")
            with wave.open(io.BytesIO(raw)) as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (1, 2, self.rate, 'NONE'):
                    raise ValueError('Acoustic assets must be mono PCM16 at the catalog sample rate')
                pcm = wav.readframes(wav.getnframes())
            if not 0 < len(pcm) <= self.rate * 2 * 5:
                raise ValueError('Acoustic assets must contain at most five seconds of audio')
            self.assets.setdefault(cue, []).append({**entry, 'pcm': pcm})
        if not self.assets:
            raise ValueError('Acoustic catalog contains no assets')
        self.pattern = re.compile(r'\[(' + '|'.join(re.escape(c) for c in self.assets) + r')\]')

    def plan(self, text: str) -> list[tuple[str, str | dict]]:
        parts = []
        start = 0
        for match in self.pattern.finditer(text):
            if text[start:match.start()].strip():
                parts.append(('text', text[start:match.start()]))
            cue = match.group(1)
            index = self.indices.get(cue, 0)
            choices = self.assets[cue]
            parts.append(('asset', choices[index % len(choices)]))
            self.indices[cue] = index + 1
            start = match.end()
        if text[start:].strip():
            parts.append(('text', text[start:]))
        return parts

    def matches(self, health: dict) -> bool:
        return health.get('voice') == self.voice and health.get('reference_sha256') == self.reference_sha256

    def validate(self, body: dict, text: str, health: dict) -> None:
        if not 0 < len(text) <= 1200 or not text.strip():
            raise ValueError('Input must contain 1 to 1200 characters')
        if body.get('voice', 'default') not in ('', 'default', self.voice):
            raise ValueError('Only the configured reference voice is available')
        if not math.isclose(float(body.get('speed', 1)), 1):
            raise ValueError('Cached acoustic cues require speed 1')
        if body.get('reference_audio'):
            reference = base64.b64decode(body['reference_audio'], validate=True)
            if sha256(reference) != self.reference_sha256:
                raise ValueError('Request reference differs from the acoustic catalog voice')
        if body.get('prompt_text') and sha256(body['prompt_text'].encode()) != self.reference_text_sha256:
            raise ValueError('Request transcript differs from the acoustic catalog reference')
        if 'seed' in body and not 0 <= int(body['seed']) < 2 ** 31:
            raise ValueError('Invalid seed')
        instructions = body.get('instructions')
        if instructions is not None:
            if not isinstance(instructions, str) or not 1 <= len(instructions.strip()) <= 64:
                raise ValueError('Style instructions must contain 1 to 64 characters')
            if any(c in instructions for c in '()（）<>[]【】\r\n'):
                raise ValueError('Style instructions cannot contain control markers')
            if not health.get('style_instructions_supported'):
                raise ValueError('Upstream does not support style instructions')


class UpstreamFailure(Exception):
    def __init__(self, status: int, body: bytes, content_type: str):
        self.status, self.body, self.content_type = status, body, content_type


async def trim_join_silence(source, rate, trim_start, trim_end, record, keep_ms=60):
    """Hold at most 800 ms of quiet PCM to trim only the adjoining boundary."""
    width = round(rate * .01) * 2
    maximum = round(rate * .8) * 2
    keep = round(rate * keep_ms / 1000) * 2
    carry = b''
    pending = bytearray()
    started = False
    removed = 0

    def quiet(frame):
        samples = array('h', frame)
        if sys.byteorder != 'little':
            samples.byteswap()
        return sum(s * s for s in samples) / len(samples) <= (32768 * .001) ** 2

    async def frames():
        nonlocal carry
        async for chunk in source:
            data = carry + chunk
            stop = len(data) // width * width
            for offset in range(0, stop, width):
                yield data[offset:offset + width]
            carry = data[stop:]
        if carry:
            yield carry

    try:
        async for frame in frames():
            if quiet(frame):
                pending.extend(frame)
                if len(pending) > maximum:
                    excess = len(pending) - maximum
                    yield bytes(pending[:excess])
                    del pending[:excess]
                    started = True
                continue
            if pending:
                if trim_start and not started:
                    removed += max(0, len(pending) - keep)
                    pending = pending[-keep:] if keep else bytearray()
                if pending:
                    yield bytes(pending)
                pending.clear()
            yield frame
            started = True
        if pending:
            if trim_end or (trim_start and not started):
                removed += max(0, len(pending) - keep)
                pending = pending[-keep:] if keep else bytearray()
            if pending:
                yield bytes(pending)
    finally:
        record['join_silence_trimmed_ms'] = round(record.get('join_silence_trimmed_ms', 0) + removed / 2 / rate * 1000, 2)
        await source.aclose()


class Gateway:
    def __init__(self, config: dict):
        self.config = config
        self.enabled = config.get('enabled', True)
        if not isinstance(self.enabled, bool):
            raise ValueError('Acoustic enabled must be a boolean')
        self.catalog = Catalog(Path(config['manifest_file']))
        self.upstream = config['upstream_url'].rstrip('/')
        if not re.fullmatch(r'https?://[^/?#]+', self.upstream):
            raise ValueError('Upstream URL must be an HTTP origin')
        self.session: ClientSession | None = None
        self.completed = self.failed = self.cancelled = 0
        self.active = 0
        self.last_request = None

    async def lifecycle(self, app):
        self.session = ClientSession(timeout=ClientTimeout(total=float(self.config.get('timeout_sec', 60))),
                                     auto_decompress=False)
        yield
        await self.session.close()

    async def health(self) -> dict:
        async with self.session.get(self.upstream + '/health', timeout=ClientTimeout(total=2)) as upstream:
            if upstream.status != 200:
                raise UpstreamFailure(upstream.status, await upstream.read(), upstream.headers.get('Content-Type', 'application/json'))
            return await upstream.json()

    async def get_health(self, request):
        try:
            health = await self.health()
            health['acoustic_assets'] = {
                'enabled': self.enabled and self.catalog.matches(health), 'gateway_version': '1.1.1',
                'gateway_pid': os.getpid(), 'manifest_sha256': self.catalog.digest,
                'voice': self.catalog.voice, 'reference_sha256': self.catalog.reference_sha256,
                'cues': {cue: [a['id'] for a in assets] for cue, assets in self.catalog.assets.items()},
                'active': self.active, 'completed': self.completed, 'failed': self.failed,
                'cancelled': self.cancelled, 'last_request': self.last_request,
                'fallback_reason': 'catalog-disabled' if not self.enabled else
                                   None if self.catalog.matches(health) else 'upstream-reference-mismatch',
            }
            return web.json_response(health)
        except UpstreamFailure as exc:
            return web.Response(status=exc.status, body=exc.body, headers={'Content-Type': exc.content_type})
        except Exception as exc:
            return web.json_response({'error': str(exc), 'service': 'acoustic-gateway'}, status=503)

    async def disconnect_watch(self, request, owner):
        while request.transport is not None and not request.transport.is_closing():
            await asyncio.sleep(.025)
        owner.cancel()

    async def handle(self, request):
        watcher = asyncio.create_task(self.disconnect_watch(request, asyncio.current_task()))
        try:
            raw = await request.read()
            if request.method == 'POST' and request.path.rstrip('/') in ('/v1/audio/speech', '/v1/audio/speech/stream'):
                try:
                    body = json.loads(raw)
                    text = body.get('input', body.get('text', ''))
                    if isinstance(text, str) and self.catalog.pattern.search(text):
                        if not self.enabled:
                            return web.json_response({'error': 'Acoustic catalog disabled by operator',
                                                      'code': 'acoustic_catalog_disabled'}, status=409)
                        health = await self.health()
                        if self.catalog.matches(health):
                            self.catalog.validate(body, text, health)
                            return await self.compose(request, body, text)
                        return await self.forward(request, raw, fallback='upstream-reference-mismatch')
                except (ValueError, TypeError, AttributeError) as exc:
                    return web.json_response({'error': str(exc)}, status=400)
            return await self.forward(request, raw)
        except UpstreamFailure as exc:
            return web.Response(status=exc.status, body=exc.body, headers={'Content-Type': exc.content_type})
        except ConnectionError as exc:
            raise asyncio.CancelledError() from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return web.json_response({'error': str(exc)}, status=503)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    async def forward(self, request, raw, fallback=None):
        headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_HEADERS | {'host'}}
        async with self.session.request(request.method, self.upstream + request.path_qs,
                                        data=raw, headers=headers) as upstream:
            headers = {key: value for key, value in upstream.headers.items() if key.lower() not in HOP_HEADERS}
            if fallback:
                headers['X-TTS-Acoustic-Fallback'] = fallback
            response = web.StreamResponse(status=upstream.status, headers=headers)
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
            await response.write_eof()
            return response

    async def speech_pcm(self, part, body, record):
        subbody = {**body, 'input': part}
        subbody.pop('text', None)
        async with self.session.post(self.upstream + '/v1/audio/speech/stream', json=subbody) as upstream:
            if upstream.status != 200:
                raise UpstreamFailure(upstream.status, await upstream.read(), upstream.headers.get('Content-Type', 'application/json'))
            record['upstream_request_ids'].append(upstream.headers.get('X-TTS-Request-Id'))
            check_stream_header(await upstream.content.readexactly(44), self.catalog.rate)
            carry = b''
            async for chunk in upstream.content.iter_any():
                chunk = carry + chunk
                carry = chunk[-1:] if len(chunk) % 2 else b''
                pcm = chunk[:-1] if carry else chunk
                if pcm:
                    yield pcm
            if carry:
                raise RuntimeError('Upstream returned incomplete PCM16')

    async def pcm(self, plan, body, record):
        for index, (kind, part) in enumerate(plan):
            if kind == 'asset':
                record['assets_played'].append(part['id'])
                yield part['pcm']
                continue
            stream = trim_join_silence(self.speech_pcm(part, body, record), self.catalog.rate,
                                      index > 0 and plan[index - 1][0] == 'asset',
                                      index + 1 < len(plan) and plan[index + 1][0] == 'asset', record)
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                await stream.aclose()

    async def compose(self, request, body, text):
        started = time.monotonic()
        plan = self.catalog.plan(text)
        record = {'id': str(uuid.uuid4()), 'input_sha256': sha256(text.encode()),
                  'manifest_sha256': self.catalog.digest,
                  'reference_sha256': self.catalog.reference_sha256,
                  'asset_ids': [part['id'] for kind, part in plan if kind == 'asset'],
                  'assets_played': [], 'upstream_request_ids': [], 'outcome': 'failed'}
        self.active += 1
        response = None
        pcm_hash = hashlib.sha256()
        size = 0
        iterator = self.pcm(plan, body, record)
        try:
            streaming = request.path.rstrip('/').endswith('/stream')
            parts = []
            async for chunk in iterator:
                if 'first_audio_ms' not in record:
                    record['first_audio_ms'] = round((time.monotonic() - started) * 1000, 2)
                size += len(chunk)
                pcm_hash.update(chunk)
                if streaming:
                    if response is None:
                        response = web.StreamResponse(headers={
                            'Content-Type': 'audio/wav', 'X-TTS-Request-Id': record['id'],
                            'X-TTS-Acoustic-Assets': ','.join(record['asset_ids']),
                            'X-TTS-Reference-SHA256': self.catalog.reference_sha256,
                            'X-TTS-Acoustic-Manifest-SHA256': self.catalog.digest,
                            'X-TTS-Request-First-Audio-Ms': str(record['first_audio_ms']),
                        })
                        await response.prepare(request)
                        await response.write(wav_header(self.catalog.rate))
                    await response.write(chunk)
                else:
                    parts.append(chunk)
            if not size:
                raise ValueError('Speech composition produced no audio')
            if streaming:
                await response.write_eof()
            else:
                response = web.Response(body=wav_header(self.catalog.rate, size) + b''.join(parts),
                                        headers={'Content-Type': 'audio/wav', 'X-TTS-Request-Id': record['id'],
                                                 'X-TTS-Acoustic-Assets': ','.join(record['asset_ids']),
                                                 'X-TTS-Reference-SHA256': self.catalog.reference_sha256,
                                                 'X-TTS-Acoustic-Manifest-SHA256': self.catalog.digest})
            self.completed += 1
            record['outcome'] = 'completed'
            return response
        except (ConnectionError, asyncio.CancelledError):
            self.cancelled += 1
            record['outcome'] = 'cancelled'
            raise
        except Exception as exc:
            self.failed += 1
            record['error'] = type(exc).__name__
            if response is not None:
                request.transport.close()
                return response
            raise
        finally:
            await iterator.aclose()
            self.active -= 1
            record.update(total_ms=round((time.monotonic() - started) * 1000, 2),
                          audio_ms=round(size / 2 / self.catalog.rate * 1000, 2), pcm_sha256=pcm_hash.hexdigest())
            self.last_request = record
            print(json.dumps({'event': 'acoustic-composition', **record}), flush=True)

    def app(self):
        app = web.Application(client_max_size=MAX_BODY)
        app.cleanup_ctx.append(self.lifecycle)
        app.router.add_get('/health', self.get_health)
        app.router.add_route('*', '/{path:.*}', self.handle)
        return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    gateway = Gateway(config)
    web.run_app(gateway.app(), host=config.get('host', '127.0.0.1'), port=int(config['port']), access_log=None)


if __name__ == '__main__':
    main()
