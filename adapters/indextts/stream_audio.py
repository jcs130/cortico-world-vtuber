"""PCM16 stream transport and continuous pitch-preserving tempo conversion."""

from __future__ import annotations

import struct
import subprocess
import threading
import io
import wave
from pronunciation import pronunciation_segments


PCM_READ_BYTES = 8192


def sentence_segments(text: str, max_chars: int = 40) -> list[str]:
    """Share the same punctuation policy for plain and annotated speech."""
    return pronunciation_segments(text, max_chars)


class NativeSegmentedPcmSource:
    """Join native clause streams with one WAV header and no added audio gap.

    The adapter owns text boundaries, including on gateways which would merge
    small clauses into a token quota. Only the first response is opened eagerly;
    cancellation closes it and never starts later requests.
    """

    def __init__(self, segments, open_stream):
        self._segments = iter(segments)
        self._open_stream = open_stream
        self._response = None
        self._pending = b''
        self._rate = None
        self._pcm_bytes = 0
        self.headers = {}
        self.closed = False
        self.segment_count = 0
        self._open_next()

    def _open_next(self):
        if self.closed:
            return False
        text = next(self._segments, None)
        if text is None:
            return False
        response = self._open_stream(text)
        try:
            rate = read_pcm_header(response)
            if self._rate is not None and self._rate != rate:
                raise ValueError('segment sample rate changed')
        except Exception:
            response.close()
            raise
        if self._rate is None:
            self._pending = wav_stream_header(rate)
            self.headers = getattr(response, 'headers', {})
        self._rate = rate
        self._pcm_bytes = 0
        self._response = response
        self.segment_count += 1
        return True

    def read(self, length):
        if self.closed:
            return b''
        if self._pending:
            data, self._pending = self._pending[:length], self._pending[length:]
            return data
        while self._response is not None:
            data = self._response.read(length)
            if data:
                self._pcm_bytes += len(data)
                return data
            self._response.close()
            self._response = None
            if not self._pcm_bytes or self._pcm_bytes % 2:
                raise ValueError('segment requires complete nonempty PCM16 samples')
            if not self._open_next():
                break
        return b''

    def close(self):
        self.closed = True
        self._pending = b''
        if self._response is not None:
            self._response.close()
            self._response = None


class SegmentedPcmSource:
    """Compatibility source: generate the next complete text segment on demand.

    Old gateways can generate real text segments with their existing WAV endpoint.
    This source returns their PCM as one stream and retains voice/emotion settings.
    """

    def __init__(self, segments, synthesize):
        self._segments = iter(segments)
        self._synthesize = synthesize
        self._pending = b''
        self._rate = None
        self.closed = False
        self.segment_count = 0

    def read(self, length):
        if self.closed:
            return b''
        if not self._pending:
            text = next(self._segments, None)
            if text is None:
                return b''
            data = self._synthesize(text)
            if self.closed:
                return b''
            with wave.open(io.BytesIO(data), 'rb') as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE':
                    raise ValueError('segment requires uncompressed mono PCM16')
                rate = wav.getframerate()
                pcm = wav.readframes(wav.getnframes())
            if not pcm:
                raise ValueError('empty audio segment')
            if self._rate is not None and self._rate != rate:
                raise ValueError('segment sample rate changed')
            self._pending = (wav_stream_header(rate) if self._rate is None else b'') + pcm
            self._rate = rate
            self.segment_count += 1
        data, self._pending = self._pending[:length], self._pending[length:]
        return data

    def close(self):
        self.closed = True
        self._pending = b''


def wav_stream_header(sample_rate: int) -> bytes:
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
            + b"data" + struct.pack("<I", 0xFFFFFFFF))


def read_pcm_header(response) -> int:
    header = bytearray()
    while len(header) < 44:
        part = response.read(44 - len(header))
        if not part:
            raise ValueError("truncated stream WAV header")
        header.extend(part)
    if header[:4] != b"RIFF" or header[8:16] != b"WAVEfmt " or header[36:40] != b"data":
        raise ValueError("stream requires a 44-byte PCM WAV header")
    size, encoding, channels, rate, byte_rate, alignment, bits = struct.unpack("<IHHIIHH", header[16:36])
    if size != 16 or encoding != 1 or channels != 1 or bits != 16 or alignment != 2 or byte_rate != rate * 2 or not 8000 <= rate <= 192000:
        raise ValueError("stream requires mono PCM16 with a valid sample rate")
    return rate


def pcm_chunks(response):
    carry = b""
    while True:
        chunk = response.read(PCM_READ_BYTES)
        if not chunk:
            break
        chunk = carry + chunk
        boundary = len(chunk) - len(chunk) % 2
        carry = chunk[boundary:]
        if boundary:
            yield chunk[:boundary]
    if carry:
        raise ValueError("stream ended inside a PCM16 sample")


def tempo_chunks(response, sample_rate: int, speed: float, ffmpeg: str):
    """Keep one atempo filter across segments; stopping the reader closes upstream."""
    if abs(speed - 1.0) < 0.01:
        yield from pcm_chunks(response)
        return
    # atempo accepts 0.5..2 per filter; preserve the existing global speed range.
    factors = []
    remaining = speed
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    factors.append(remaining)
    process = subprocess.Popen(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-probesize", "32", "-analyzeduration", "0",
         "-f", "s16le", "-ar", str(sample_rate), "-ac", "1", "-i", "pipe:0",
         "-filter:a", ",".join("atempo=%.6f" % factor for factor in factors),
         "-f", "s16le", "-flush_packets", "1", "pipe:1"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
    )
    feed_errors = []

    def feed():
        try:
            for chunk in pcm_chunks(response):
                offset = 0
                while offset < len(chunk):
                    written = process.stdin.write(chunk[offset:])
                    if not written:
                        raise BrokenPipeError("tempo input closed")
                    offset += written
        except Exception as error:
            feed_errors.append(error)
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    worker = threading.Thread(target=feed, name="tts-tempo-input", daemon=True)
    worker.start()
    try:
        yield from pcm_chunks(process.stdout)
        code = process.wait(timeout=5)
        worker.join(timeout=1)
        if feed_errors:
            raise feed_errors[0]
        if code:
            raise RuntimeError("tempo converter failed (%d)" % code)
    finally:
        # Closing HTTP first also unblocks a producer waiting for another text segment.
        response.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        process.stdout.close()
        worker.join(timeout=1)
