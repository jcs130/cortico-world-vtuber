import asyncio
import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
import wave

from aiohttp import ClientSession, web
from acoustic_gateway import Catalog, Gateway, sha256, wav_header


class AcousticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pcm = b'\x03\x00' * 80
        output = io.BytesIO()
        with wave.open(output, 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', ''))
            wav.writeframes(self.pcm)
        raw = output.getvalue()
        (self.root / 'cue.wav').write_bytes(raw)
        self.manifest = {'version': 1, 'voice': 'test-voice', 'sample_rate': 8000,
                         'reference_sha256': sha256(b'reference'),
                         'reference_text_sha256': sha256(b'reference text'),
                         'assets': [{'id': 'soft-1', 'cue': 'laughing', 'file': 'cue.wav', 'sha256': sha256(raw)}]}
        self.manifest_path = self.root / 'catalog.json'
        self.save_manifest()
        self.seen = []
        self.closed = asyncio.Event()
        self.healthy_ref = self.manifest['reference_sha256']
        self.runners = []
        upstream = web.Application()
        upstream.router.add_get('/health', self.health)
        upstream.router.add_post('/v1/audio/speech/stream', self.speech)
        self.upstream_url = await self.serve(upstream)
        self.gateway = Gateway({'upstream_url': self.upstream_url, 'manifest_file': str(self.manifest_path)})
        self.url = await self.serve(self.gateway.app())
        self.client = ClientSession()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.manifest))

    async def serve(self, app):
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '127.0.0.1', 0)
        await site.start()
        self.runners.append(runner)
        return 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])

    async def asyncTearDown(self):
        await self.client.close()
        for runner in reversed(self.runners):
            await runner.cleanup()
        self.temp.cleanup()

    async def health(self, request):
        return web.json_response({'status': 'ok', 'voice': 'test-voice',
                                  'reference_sha256': self.healthy_ref, 'style_instructions_supported': True})

    async def speech(self, request):
        body = await request.json()
        self.seen.append(body)
        text = body['input']
        if text == 'error':
            return web.json_response({'error': 'invalid request'}, status=422)
        response = web.StreamResponse(headers={'Content-Type': 'audio/wav', 'X-TTS-Request-Id': text})
        await response.prepare(request)
        try:
            # Header and samples may cross arbitrary network boundaries.
            data = wav_header(8000) + (b'\x01\x00' if text == 'before' else b'\x02\x00') * 80
            await response.write(data[:13])
            await response.write(data[13:47])
            if text == 'wait':
                for _ in range(100):
                    await asyncio.sleep(.02)
                    await response.write(b'\x02\x00' * 80)
            else:
                await response.write(data[47:])
            await response.write_eof()
        except ConnectionError:
            self.closed.set()
        return response

    async def post(self, text, stream=True, **extra):
        response = await self.client.post(self.url + '/v1/audio/speech' + ('/stream' if stream else ''),
                                          json={'input': text, **extra})
        return response, await response.read()

    async def test_stream_preserves_pcm_order_and_one_header(self):
        response, raw = await self.post('before[laughing]after', instructions='smiling', seed=7)
        self.assertEqual(response.status, 200)
        self.assertEqual(raw[:44], wav_header(8000))
        self.assertEqual(raw[44:], b'\x01\x00' * 80 + self.pcm + b'\x02\x00' * 80)
        self.assertEqual([b['input'] for b in self.seen], ['before', 'after'])
        self.assertTrue(all(b['instructions'] == 'smiling' and b['seed'] == 7 for b in self.seen))
        self.assertEqual(response.headers['X-TTS-Acoustic-Assets'], 'soft-1')
        self.assertEqual(self.gateway.last_request['assets_played'], ['soft-1'])

    async def test_nonstream_returns_valid_finite_wav(self):
        response, raw = await self.post('before[laughing]after', stream=False)
        self.assertEqual(response.status, 200)
        with wave.open(io.BytesIO(raw)) as wav:
            self.assertEqual(wav.getnframes(), 240)
            self.assertEqual(wav.readframes(240), b'\x01\x00' * 80 + self.pcm + b'\x02\x00' * 80)

    async def test_pure_cue_uses_memory_without_synthesis(self):
        (self.root / 'cue.wav').unlink()
        response, raw = await self.post('[laughing]')
        self.assertEqual(raw[44:], self.pcm)
        self.assertEqual(self.seen, [])
        self.assertEqual(self.gateway.completed, 1)

    async def test_plain_request_forwards_without_catalog_processing(self):
        response, raw = await self.post('before', instructions='smiling')
        self.assertEqual(raw[44:], b'\x01\x00' * 80)
        self.assertNotIn('X-TTS-Acoustic-Assets', response.headers)
        self.assertEqual(self.gateway.completed, 0)

    async def test_reference_change_falls_back_to_native_with_visible_reason(self):
        self.healthy_ref = sha256(b'changed')
        response, raw = await self.post('[laughing]')
        self.assertEqual(response.headers['X-TTS-Acoustic-Fallback'], 'upstream-reference-mismatch')
        self.assertEqual(self.seen[0]['input'], '[laughing]')
        health = await (await self.client.get(self.url + '/health')).json()
        self.assertFalse(health['acoustic_assets']['enabled'])

    async def test_pure_cue_validates_voice_reference_transcript_speed_and_style(self):
        for extra in [{'voice': 'wrong'}, {'reference_audio': base64.b64encode(b'wrong').decode()},
                      {'prompt_text': 'wrong'}, {'speed': .8}, {'instructions': '[bad]'}, {'seed': -1}]:
            response, raw = await self.post('[laughing]', **extra)
            self.assertEqual(response.status, 400, extra)
            self.assertIn('error', json.loads(raw))
        self.assertEqual(self.gateway.completed, 0)
        self.assertEqual(self.seen, [])

    async def test_upstream_error_before_audio_keeps_error_status(self):
        response, raw = await self.post('error[laughing]')
        self.assertEqual(response.status, 422)
        self.assertEqual(json.loads(raw)['error'], 'invalid request')
        self.assertEqual(self.gateway.failed, 1)

    async def test_disconnect_cancels_current_upstream_and_skips_following_parts(self):
        response = await self.client.post(self.url + '/v1/audio/speech/stream',
                                          json={'input': '[laughing]wait[laughing]after'})
        await response.content.readexactly(44 + len(self.pcm) + 2)
        response.close()
        await asyncio.wait_for(self.closed.wait(), timeout=1)
        for _ in range(30):
            if self.gateway.cancelled:
                break
            await asyncio.sleep(.01)
        self.assertEqual([b['input'] for b in self.seen], ['wait'])
        self.assertEqual(self.gateway.cancelled, 1)
        self.assertEqual(self.gateway.last_request['assets_played'], ['soft-1'])
        self.assertEqual(self.gateway.active, 0)

    async def test_catalog_rejects_corrupt_asset_and_path_escape(self):
        self.manifest['assets'][0]['sha256'] = '0' * 64
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            Catalog(self.manifest_path)
        self.manifest['assets'][0]['file'] = '../outside.wav'
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'inside'):
            Catalog(self.manifest_path)


if __name__ == '__main__':
    unittest.main()
