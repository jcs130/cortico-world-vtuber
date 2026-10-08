# -*- coding: utf-8 -*-
"""IndexTTS HTTP adapter: reference prosody, contextual pronunciation and segment streaming."""
import json, os, re, subprocess, sys, io, time, difflib, urllib.request, shutil, hashlib, threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from corti_speech_style import (
    DEFAULT_CONFIDENCE, DEFAULT_EMOTION_MIX, MAX_EMOTION_MIX,
    bounded_number, emotion_mix_vector, strip_stage_directions,
)
from stream_audio import NativeSegmentedPcmSource, SegmentedPcmSource, read_pcm_header, sentence_segments, tempo_chunks, wav_stream_header
from pronunciation import initialize_pronunciation, normalize_pronunciation, pronunciation_health, pronunciation_segments
from spoken_text import normalize_spoken_text

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PORT = int(os.environ.get('CORTI_TTS_PORT', '8010'))
if not 1 <= PORT <= 65535:
    raise ValueError('CORTI_TTS_PORT must be an integer between 1 and 65535')
ADAPTER_VERSION = '24-word-prosody'
# The adapter bounds complete phrases. Leave the native model enough token
# room to retain that context, including pronunciation annotations.
DEFAULT_STREAM_SEGMENT_TOKENS = 120
_last_preparation = None
_last_stream = None
_upstream_stream_path = 'unverified'
_stream_lock = threading.Lock()
INDEXTTS_BASE = os.environ.get('CORTICO_INDEXTTS_URL', 'http://127.0.0.1:8087').rstrip('/')
INDEXTTS = INDEXTTS_BASE + '/tts_raw'
INDEXTTS_STREAM = INDEXTTS_BASE + '/tts_stream'
FFMPEG = os.environ.get('CORTICO_FFMPEG') or shutil.which('ffmpeg') or 'ffmpeg'
DECISION_URL = os.environ.get('CORTICO_DECISION_URL', 'http://127.0.0.1:8090/v1/systemone')
DECISION_TIMEOUT = 3  # urllib timeout is in seconds; unavailable service falls back

# ── StartLux-Decision mood classifier ──

MOOD_CHOICES = {
    "calm":      "Feeling peaceful and composed, neutral narration",
    "happy":     "Feeling joyful, excited, or celebrating",
    "angry":     "Feeling frustrated, irritated, or mad",
    "sad":       "Feeling down, disappointed, or melancholy",
    "afraid":    "Feeling scared, in danger, or panicking",
    "surprised": "Feeling amazed, shocked, or caught off guard",
    "gentle":    "Speaking softly, tenderly, or comfortingly",
    "playful":   "Being silly, teasing, or joking around",
    "warm":      "Feeling grateful, appreciative, or affectionate",
    "serious":   "Speaking in a focused, no-nonsense tone",
}


def classify_mood_decision(text: str) -> tuple:
    """Call StartLux-Decision for mood classification. Returns (mood, confidence) or (None, 0)."""
    payload = {
        "state": {"dialogue": text[:500]},
        "questions": {
            "mood": {
                "type": "choice",
                "instructions": "What emotional tone is this dialogue delivering?",
                "criteria": MOOD_CHOICES,
            }
        }
    }
    try:
        req = urllib.request.Request(
            DECISION_URL,
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'})
        resp = json.load(urllib.request.urlopen(req, timeout=DECISION_TIMEOUT))
        ans = resp.get('answers', {}).get('mood', {})
        mood = ans.get('choice')
        conf = ans.get('confidence', 0)
        return (mood, conf) if mood else (None, 0)
    except Exception:
        return (None, 0)


# ── Legacy regex fallback (kept for when decision server is down) ──

TAG_MOOD_RULES = [
    (re.compile(r'(害怕|惊吓|恐慌|恐惧|后退|afraid)', re.I), 'afraid'),
    (re.compile(r'(生气|怒|气鼓鼓|angry)', re.I), 'angry'),
    (re.compile(r'(惊讶|瞠|愣|瞪大|没想到|surprised)', re.I), 'surprised'),
    (re.compile(r'(垂头|丧气|哭|泪|难过|sad)', re.I), 'sad'),
    (re.compile(r'(laughing|哈哈|笑|开心|喜悦|happy)', re.I), 'happy'),
    (re.compile(r'(sigh|叹气|唉|melancholic)', re.I), 'melancholic'),
    (re.compile(r'(眨眼|吐舌|俏皮|歪头|playful)', re.I), 'playful'),
    (re.compile(r'(温柔|微笑|轻声|gentle)', re.I), 'gentle'),
    (re.compile(r'(calm|平静|深呼吸)', re.I), 'calm'),
    (re.compile(r'(serious|严肃|沉稳)', re.I), 'serious'),
    (re.compile(r'(warm|温暖)', re.I), 'warm'),
    (re.compile(r'(无奈|无语|委屈|叹气|苦笑)'), 'melancholic'),
    (re.compile(r'(得意|炫耀|骄傲)'), 'playful'),
    (re.compile(r'(紧张|慌)'), 'afraid'),
    (re.compile(r'(兴奋|激动)'), 'happy'),
]

BARE_TAG_RE = re.compile(
    r'[\u4e00-\u9fffA-Za-z]{1,8}@[0-9]+(?:\.[0-9]+)?')

MOOD_SPEED = {
    'happy': 1.04, 'playful': 1.03, 'surprised': 1.03, 'afraid': 1.04,
    'angry': 1.03, 'sad': 0.95, 'melancholic': 0.96, 'gentle': 0.98,
    'warm': 0.98, 'calm': 0.98, 'serious': 0.98, 'hearty': 1.02,
}

MOOD_VECS = {
    'calm':        [0, 0, 0, 0, 0, 0, 0, 1],
    'happy':       [1, 0, 0, 0, 0, 0, 0, 0],
    'angry':       [0, 1, 0, 0, 0, 0, 0, 0],
    'sad':         [0, 0, 1, 0, 0, 0, 0, 0],
    'afraid':      [0, 0, 0, 1, 0, 0, 0, 0],
    'disgusted':   [0, 0, 0, 0, 1, 0, 0, 0],
    'melancholic': [0, 0, 0, 0, 0, 1, 0, 0],
    'surprised':   [0, 0, 0, 0, 0, 0, 1, 0],
    'gentle':      [0.2, 0, 0, 0, 0, 0, 0, 0.8],
    'hearty':      [0.7, 0.2, 0, 0, 0, 0, 0, 0.1],
    'serious':     [0, 0, 0, 0, 0, 0.2, 0, 0.8],
    'playful':     [0.6, 0, 0, 0, 0, 0, 0.3, 0.1],
    'warm':        [0.4, 0, 0, 0, 0, 0, 0, 0.6],
    'cold':        [0, 0.2, 0, 0, 0, 0.3, 0, 0.5],
}

MOOD_INTENSITY = {
    'afraid': 0.35, 'surprised': 0.40, 'angry': 0.45, 'disgusted': 0.45,
    'sad': 0.50, 'melancholic': 0.50, 'cold': 0.50,
    'happy': 0.60, 'playful': 0.60, 'hearty': 0.60, 'serious': 0.60,
    'gentle': 0.70, 'warm': 0.70, 'calm': 0.60,
}


def damped_vec(mood: str, k: float = None):
    """Compatibility helper: never replace the remaining reference weight with calm."""
    return emotion_mix_vector(mood, MOOD_VECS.get(mood), source='tag', intensity=k)


def strip_stage_dirs(text: str):
    return strip_stage_directions(text, TAG_MOOD_RULES)


def pick_mood_regex(text: str):
    for rx, mood in TAG_MOOD_RULES:
        if rx.search(text):
            return mood
    return None


def _silent_wav(rate=24000, secs=0.05):
    import struct as _st
    n = int(rate * secs)
    data = b'\x00\x00' * n
    return (b'RIFF' + _st.pack('<I', 36 + len(data)) + b'WAVEfmt '
            + _st.pack('<IHHIIHH', 16, 1, 1, rate, rate * 2, 2, 16)
            + b'data' + _st.pack('<I', len(data)) + data)


SILENT_WAV = _silent_wav()

DEDUP_WINDOW_SEC = 90
DEDUP_RATIO = 0.90
_recent = deque()

PREFS = os.environ.get('CORTI_TTS_PREFS', os.path.expanduser('~/.config/cortico/tts-prefs.json'))
_prefs_cache = {'mtime': 0.0, 'data': {}}


def load_prefs():
    try:
        mt = os.path.getmtime(PREFS)
        if mt != _prefs_cache['mtime']:
            with open(PREFS, encoding='utf-8') as f:
                _prefs_cache['data'] = json.load(f)
                _prefs_cache['mtime'] = mt
    except FileNotFoundError:
        _prefs_cache['data'] = {}
    except Exception:
        pass
    return _prefs_cache['data']


def retempo(wav: bytes, speed: float) -> bytes:
    if abs(speed - 1.0) < 0.01:
        return wav
    proc = subprocess.run(
        [FFMPEG, '-hide_banner', '-loglevel', 'error', '-i', 'pipe:0',
         '-filter:a', 'atempo=%.3f' % speed, '-f', 'wav', 'pipe:1'],
        input=wav, capture_output=True, timeout=60)
    if proc.returncode == 0 and proc.stdout[:4] == b'RIFF':
        return proc.stdout
    return wav


def norm_text(t):
    return re.sub(r'[\s\W_]+', '', t)


def is_repeat(t):
    now = time.monotonic()
    while _recent and now - _recent[0][0] > DEDUP_WINDOW_SEC:
        _recent.popleft()
    n = norm_text(t)
    if not n:
        return False
    for _, prev in _recent:
        if n == prev:
            return True
        if len(n) >= 12 and len(prev) >= 12 and \
           difflib.SequenceMatcher(None, n[:48], prev[:48]).ratio() >= DEDUP_RATIO:
            return True
    return False


def remember(t):
    _recent.append((time.monotonic(), norm_text(t)))


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        sys.stdout.write('[adapter] %s\n' % (fmt % args))
        sys.stdout.flush()

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chunk(self, data: bytes):
        self.wfile.write(('%x\r\n' % len(data)).encode('ascii'))
        self.wfile.write(data)
        self.wfile.write(b'\r\n')
        self.wfile.flush()

    def _stream(self, payload, speed, clean, max_chars=40, *, request_started=None):
        global _upstream_stream_path, _last_stream
        started = time.monotonic()
        request_started = started if request_started is None else request_started
        preparation_ms = round((started - request_started) * 1000)
        response = None
        chunks = None
        sent = False
        acquired = False
        try:
            # Keep one utterance together at the shared model queue. Otherwise
            # a prefetched later utterance could cut between its clause requests.
            _stream_lock.acquire()
            acquired = True
            adapter_queue_ms = round((time.monotonic() - started) * 1000)
            segments = pronunciation_segments(payload['input'], max_chars)
            self.log_message('stream clauses: %s', json.dumps(segments, ensure_ascii=False))
            def open_stream(segment):
                request = urllib.request.Request(
                    INDEXTTS_STREAM, data=json.dumps(dict(payload, input=segment)).encode('utf-8'),
                    headers={'Content-Type': 'application/json'})
                return urllib.request.urlopen(request, timeout=90)
            try:
                response = NativeSegmentedPcmSource(segments, open_stream)
                stream_path = 'native-clause-segments'
            except urllib.error.HTTPError as error:
                if error.code not in (404, 405):
                    raise
                error.close()
                def synthesize(segment):
                    part = dict(payload, input=segment)
                    part.pop('max_text_tokens_per_segment', None)
                    req = urllib.request.Request(
                        INDEXTTS, data=json.dumps(part).encode('utf-8'),
                        headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req, timeout=90) as audio:
                        return audio.read()
                response = SegmentedPcmSource(segments, synthesize)
                stream_path = 'compatibility-text-segments'
                self.log_message('stream uses legacy segment synthesis: max_chars=%d', max_chars)
            _upstream_stream_path = stream_path
            queue_ms = getattr(response, 'headers', {}).get('X-TTS-Queue-Ms')
            queue_ms = int(queue_ms) if queue_ms is not None and queue_ms.isdigit() else None
            rate = read_pcm_header(response)
            chunks = tempo_chunks(response, rate, speed, FFMPEG)
            first = next(chunks, None)
            if not first:
                raise ValueError('empty audio stream')
            first_ms = round((time.monotonic() - started) * 1000)
            request_first_ms = round((time.monotonic() - request_started) * 1000)
            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-TTS-Streaming', 'text-segments')
            self.send_header('X-TTS-Upstream-Mode', stream_path)
            self.send_header('X-TTS-First-Audio-Ms', str(first_ms))
            self.send_header('X-TTS-Preparation-Ms', str(preparation_ms))
            self.send_header('X-TTS-Request-First-Audio-Ms', str(request_first_ms))
            self.end_headers()
            sent = True
            # Remember as soon as playback can start, including interrupted phrases.
            remember(clean)
            self._chunk(wav_stream_header(rate) + first)
            byte_count = len(first)
            for chunk in chunks:
                self._chunk(chunk)
                byte_count += len(chunk)
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
            _last_stream = {
                'at_ms': round(time.time() * 1000), 'voice': payload['voice'],
                'upstream_mode': stream_path, 'preparation_ms': preparation_ms,
                'first_audio_ms': request_first_ms, 'upstream_first_audio_ms': first_ms,
                'upstream_queue_ms': queue_ms,
                'adapter_queue_ms': adapter_queue_ms,
                'total_ms': round((time.monotonic() - request_started) * 1000),
                'audio_ms': round(byte_count * 1000 / (rate * 2)),
                'segment_tokens': payload.get('max_text_tokens_per_segment'),
                'speech_segments': getattr(response, 'segment_count', None),
            }
            self.log_message('stream ok: %s', json.dumps(_last_stream))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            self.log_message('stream cancelled: upstream closed')
        except Exception as error:
            self.log_message('stream FAIL: %s', error)
            if sent:
                # A missing final HTTP chunk reports failure without replaying heard audio.
                self.close_connection = True
            else:
                self._send(502, json.dumps({'error': str(error)}).encode('utf-8'), 'application/json')
        finally:
            if chunks is not None:
                chunks.close()
            if response is not None:
                response.close()
            if acquired:
                _stream_lock.release()

    def do_GET(self):
        if self.path.rstrip('/') == '/health':
            prefs = load_prefs()
            self._send(200, json.dumps({
                'status': 'ok', 'streaming': True, 'streaming_granularity': 'text-segments', 'backend': 'indextts-8087',
                'voice': (prefs.get('voice') or 'taozi'),
                'speed': float(prefs.get('speed', 1.0) or 1.0),
                'mood_engine': 'startlux-decision',
                'version': ADAPTER_VERSION,
                'instance_id': os.environ.get('CORTICO_TTS_MANAGED_TOKEN'),
                'pid': os.getpid(),
                'upstream_url': INDEXTTS_BASE,
                'preferences_file': PREFS,
                'port': PORT,
                'emotion_mix': bounded_number(prefs.get('emotion_mix'), DEFAULT_EMOTION_MIX, 0.0, MAX_EMOTION_MIX),
                'emotion_min_confidence': bounded_number(prefs.get('emotion_min_confidence'), DEFAULT_CONFIDENCE, 0.0, 1.0),
                'reference_prosody': True,
                'voice_cue_policy': 'emotion-hints',
                'native_acoustic_cues': False,
                'pronunciation_policy': pronunciation_health()['policy'],
                'pronunciation': pronunciation_health(),
                'spoken_text_policy': 'chinese-levels-fields-numbers',
                'upstream_stream_path': _upstream_stream_path,
                'last_preparation': _last_preparation,
                'last_stream': _last_stream,
                'stream_segment_tokens': int(bounded_number(prefs.get('stream_segment_tokens'), DEFAULT_STREAM_SEGMENT_TOKENS, 16, 120)),
                'stream_segment_policy': 'sentences-soft-clause-limit',
                'stream_segment_chars': int(bounded_number(prefs.get('stream_segment_chars'), 40, 16, 120)),
            }).encode(), 'application/json')
        else:
            self._send(404, b'{"error":"not found"}', 'application/json')

    def do_POST(self):
        global _last_preparation
        request_started = time.monotonic()
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b''
        if self.path.rstrip('/') in ('/v1/audio/speech', '/v1/audio/speech/stream'):
            streaming = self.path.rstrip('/').endswith('/stream')
            try:
                body = json.loads(raw.decode('utf-8')) if raw else {}
            except Exception:
                body = {}
            text = (body.get('input') or body.get('text') or '').strip()
            if not text:
                self._send(400, b'{"error":"empty input"}', 'application/json')
                return

            clean, tags, tag_mood, tag_k = strip_stage_dirs(text)
            clean = normalize_spoken_text(clean)
            if not clean:
                self.log_message('tag-only clip -> silent wav')
                self._send(200, SILENT_WAV, 'audio/wav')
                return

            if is_repeat(clean):
                self.log_message('dedup-skip: near-identical line')
                self._send(410, b'{"error":"dedup"}', 'application/json')
                return

            # ── Mood selection: StartLux-Decision → regex fallback ──
            decision_mood, decision_conf = None, 0
            decision_ms = 0
            if not tag_mood:
                t0 = time.time()
                decision_mood, decision_conf = classify_mood_decision(clean)
                decision_ms = round((time.time() - t0) * 1000)
                if decision_ms > 100:
                    self.log_message('decision slow: %dms', decision_ms)

            mood = tag_mood or decision_mood or pick_mood_regex(clean)
            mood_source = 'tag' if tag_mood else ('decision' if decision_mood else ('regex' if mood else 'neutral'))

            prefs = load_prefs()
            requested_voice = body.get('voice')
            voice = (requested_voice.strip() if isinstance(requested_voice, str)
                     and requested_voice.strip() not in ('', 'default') else
                     (prefs.get('voice') or 'taozi').strip() or 'taozi')
            gspeed = bounded_number(body.get('speed', prefs.get('speed')), 1.0, 0.5, 2.0)
            vec = emotion_mix_vector(
                mood, MOOD_VECS.get(mood), source=mood_source,
                confidence=decision_conf, intensity=tag_k,
                max_mix=body.get('emotion_mix', prefs.get('emotion_mix', DEFAULT_EMOTION_MIX)),
                minimum_confidence=body.get('emotion_min_confidence', prefs.get('emotion_min_confidence', DEFAULT_CONFIDENCE)),
            )
            # A mood label without a vector would cause the gateway to recreate a
            # full-strength preset. Natural requests must omit both fields.
            effective_mood = mood if vec is not None else None
            speed = gspeed * MOOD_SPEED.get(effective_mood, 1.0)

            # Keep mood classification and dedup on readable text; phonetic hints
            # are solely a synthesis concern, including both streaming paths.
            pronunciation_started = time.monotonic()
            resolved = normalize_pronunciation(clean)
            preparation = {
                'at_ms': round(time.time() * 1000),
                'input_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'input_chars': len(text), 'spoken_chars': len(clean),
                'synthesis_chars': len(resolved), 'voice': voice,
                'phonetic_hints': len(re.findall(r'<[^<>|]+\|[^<>]+>', resolved)),
                'streaming': streaming,
                'mood_ms': decision_ms,
                'pronunciation_ms': round((time.monotonic() - pronunciation_started) * 1000),
                'preparation_ms': round((time.monotonic() - request_started) * 1000),
            }
            _last_preparation = preparation
            self.log_message('text-prepared: %s', json.dumps({
                **preparation, 'script': text, 'spoken': clean, 'synthesis': resolved,
            }, ensure_ascii=False))
            payload = {'input': resolved, 'voice': voice, 'language': 'Chinese'}
            if vec is not None:
                payload['mood'] = mood
                payload['emo_vector'] = vec

            if clean != text:
                self.log_message('stripped: %s | mood=%s via %s (dec:%dms conf:%.2f)',
                                tags, effective_mood or 'reference', mood_source, decision_ms, decision_conf)
            else:
                self.log_message('mood=%s via %s (dec:%dms conf:%.2f) speed=%.2f',
                                effective_mood or 'reference', mood_source, decision_ms, decision_conf, speed)

            if streaming:
                speed = bounded_number(speed, 1.0, 0.25, 4.0)
                tokens = bounded_number(prefs.get('stream_segment_tokens'), DEFAULT_STREAM_SEGMENT_TOKENS, 16, 120)
                payload['max_text_tokens_per_segment'] = int(tokens)
                payload['num_beams'] = 1
                max_chars = int(bounded_number(prefs.get('stream_segment_chars'), 40, 16, 120))
                self._stream(payload, speed, clean, max_chars, request_started=request_started)
                return

            try:
                req = urllib.request.Request(
                    INDEXTTS, data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'})
                wav = urllib.request.urlopen(req, timeout=90).read()
                remember(clean)
                before = len(wav)
                wav = retempo(wav, speed)
                self.log_message('synth ok: voice=%s mood=%s src=%s dec=%dms speed=%.2f bytes=%d->%d',
                                voice, effective_mood or 'reference', mood_source, decision_ms, speed, before, len(wav))
                self._send(200, wav, 'audio/wav')
            except Exception as e:
                self.log_message('synth FAIL: %s', e)
                self._send(502, ('{"error":"%s"}' % e).encode(), 'application/json')
        else:
            self._send(404, b'{"error":"not found"}', 'application/json')


if __name__ == '__main__':
    from managed_lifecycle import watch_parent
    watch_parent()
    initialize_pronunciation()
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
    print(f'[adapter {ADAPTER_VERSION}] Segment streaming + reference prosody on :{PORT} -> {INDEXTTS}')
    print(f'[adapter {ADAPTER_VERSION}] Decision server: {DECISION_URL} (timeout {DECISION_TIMEOUT}s), prefs={PREFS}')
    sys.stdout.flush()
    srv.serve_forever()
