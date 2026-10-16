import hashlib
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from voice_catalog import VoiceCatalog
from voxcpm2_server import VoiceEngine, VoiceServer


class Audio:
    def __init__(self, number): self.number = number
    def squeeze(self, *_): return self
    def cpu(self): return self
    def numpy(self): return np.full(4800, self.number, dtype=np.float32)


class FakeTts:
    def __init__(self):
        self.builds = []
        self.renders = []
        self.entered, self.release = threading.Event(), threading.Event()

    def build_prompt_cache(self, reference_wav_path, **kwargs):
        self.builds.append((Path(reference_wav_path).name, threading.get_ident()))
        return {'mode': 'ref_continuation' if kwargs else 'reference', 'reference': Path(reference_wav_path).name}

    def generate_with_prompt_cache_streaming(self, target_text, prompt_cache, **kwargs):
        self.renders.append((target_text, prompt_cache['reference'], threading.get_ident()))
        if target_text == 'hold':
            self.entered.set()
            if not self.release.wait(4): raise TimeoutError('fixture release')
        number = .1 if prompt_cache['reference'].startswith('Q5') else .2
        for _ in range(3): yield Audio(number), None, None


class FakeEngine(VoiceEngine):
    def _load_model(self):
        self.torch = SimpleNamespace(manual_seed=lambda _: None, __version__='offline-fixture')
        self.rate = 48000
        self.model = SimpleNamespace(tts_model=FakeTts())
        ref = self.voices.resolve()
        self.prompt_cache = self.model.tts_model.build_prompt_cache(ref.reference_file, prompt_wav_path=ref.reference_file)
        self.style_cache = None
        self._prompt_caches[self.voice] = (self.prompt_cache, None)
        self.cached_voice_ids = (self.voice,)


class VoiceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        rows = []
        for vid in ['F6', 'Q3', 'Q5']:
            path = self.root/(vid+'.wav'); path.write_bytes(('reference-'+vid).encode())
            rows.append({'id': 'cormi-'+vid, 'label': vid, 'reference_file': path.name,
                         'reference_text': 'reference '+vid, 'reference_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        self.rows = rows
        catalog = self.root/'voices.json'
        catalog.write_text(json.dumps({'schema':'tts-voice-catalog-v1','voices':rows[1:]}),encoding='utf-8')
        self.config = {**{key:rows[0][key] for key in ('reference_text','reference_sha256')},
                       'voice':'cormi-F6','reference_file':str(self.root/'F6.wav'),
                       'voice_catalog_file':str(catalog),'default_instructions':None,
                       'sentence_segmentation':False,'max_cached_voices':2}

    def tearDown(self): self.tmp.cleanup()

    def test_legacy_default_catalog_is_hash_pinned_and_public_paths_are_hidden(self):
        legacy = VoiceCatalog({k:v for k,v in self.config.items() if k!='voice_catalog_file'})
        self.assertEqual(legacy.resolve('default').id,'cormi-F6')
        voices = VoiceCatalog(self.config)
        self.assertEqual(voices.resolve('cormi-Q5').reference_text,'reference Q5')
        self.assertNotIn(self.tmp.name,json.dumps(voices.public()))
        for value in ['cormi-missing','../Q5',{'id':'cormi-Q5'},False]:
            with self.assertRaises(ValueError): voices.resolve(value)
        (self.root/'Q5.wav').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash mismatch'): VoiceCatalog(self.config)

    def test_duplicate_ids_are_rejected(self):
        path=Path(self.config['voice_catalog_file'])
        path.write_text(json.dumps({'schema':'tts-voice-catalog-v1','voices':[self.rows[1],self.rows[1]]}))
        with self.assertRaisesRegex(ValueError,'Duplicate'): VoiceCatalog(self.config)

    def test_three_http_clients_queue_cancel_and_keep_request_voice_and_cache_isolation(self):
        engine=FakeEngine(self.config)
        server=VoiceServer(('127.0.0.1',0),engine)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        port=server.server_port
        def request(voice,text):
            conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
            try:
                conn.request('POST','/v1/audio/speech/stream',json.dumps({'voice':voice,'input':text}),{'Content-Type':'application/json'})
                response=conn.getresponse(); data=response.read()
                return response.status,dict(response.getheaders()),data
            finally: conn.close()
        def wait_until(predicate):
            until=time.monotonic()+3
            while not predicate():
                if time.monotonic()>until: self.fail('fixture did not reach expected state')
                time.sleep(.01)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(request,'cormi-Q5','hold')
                self.assertTrue(engine.model.tts_model.entered.wait(2))
                second=pool.submit(request,'cormi-Q3','second')
                raw=socket.create_connection(('127.0.0.1',port),timeout=2)
                body=json.dumps({'voice':'cormi-Q5','input':'cancelled-before-render'}).encode()
                raw.sendall(b'POST /v1/audio/speech/stream HTTP/1.1\r\nHost: fixture\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
                wait_until(lambda:engine.metrics.snapshot()['waiting']==2)
                raw.shutdown(socket.SHUT_RDWR);raw.close()
                wait_until(lambda:engine.cancelled==1)
                engine.model.tts_model.release.set()
                a,b=first.result(),second.result()
            self.assertEqual((a[0],b[0]),(200,200))
            for result,row in [(a,self.rows[2]),(b,self.rows[1])]:
                self.assertEqual(result[1]['X-TTS-Voice'],row['id'])
                self.assertEqual(result[1]['X-TTS-Reference-SHA256'],row['reference_sha256'])
                self.assertTrue(result[2].startswith(b'RIFF'))
            self.assertEqual([(t,r) for t,r,_ in engine.model.tts_model.renders],[('hold','Q5.wav'),('second','Q3.wav')])
            self.assertEqual(len({owner for _,owner in engine.model.tts_model.builds}),1)
            self.assertEqual(len({owner for _,_,owner in engine.model.tts_model.renders}),1)
            self.assertEqual(len(engine.cached_voice_ids),2)
            self.assertIn('cormi-F6',engine.cached_voice_ids)
            self.assertEqual(request('missing','never')[0],400)
            self.assertEqual(len(engine.model.tts_model.renders),2)
        finally:
            engine.model.tts_model.release.set();server.shutdown();server.server_close();engine.worker.close()
        with self.assertRaisesRegex(RuntimeError,'stopping'): list(engine.generate('late',42,voice='cormi-Q5'))


if __name__=='__main__': unittest.main()
