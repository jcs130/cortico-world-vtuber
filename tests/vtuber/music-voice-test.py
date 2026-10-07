"""Offline timeline, reference and process-isolation tests; no model downloads."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(ROOT))
def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded
voice = module("music_voice_tests", "music_voice.py")
worker = module("music_voice_worker_tests", "music_voice_worker.py")


def config(root):
    return {"enabled": True, "backend": "seed-vc-v1-200m-svc", "referenceAuthorized": True,
            "referenceKind": "original-speech", "pythonFile": sys.executable, "assetRoot": str(root),
            "manifestFile": str(root / "manifest.json"), "separatorCheckpoint": str(root / "separator.pt"),
            "whisperCheckpoint": str(root / "whisper.pt"), "referenceFile": str(root / "ref.wav"),
            **{key: "a" * 64 for key in ("manifestSha256", "separatorSha256", "whisperSha256", "referenceSha256")},
            "upstreamRevision": "b" * 40}


class AudioTest(unittest.TestCase):
    def setUp(self):
        self.rate = 8000
        self.vocal = (np.sin(np.arange(2 * self.rate) * 2 * np.pi * 220 / self.rate) * 0.1).astype("float32")[:, None]

    def test_reference_trims_edges_without_repetition_or_synthesis(self):
        source = np.pad(self.vocal, ((4000, 6000), (0, 0)))
        reference, info = worker.prepare_reference(source, self.rate)
        self.assertGreater(info["trimStartSec"], 0.4)
        self.assertLess(info["usedSeconds"], 2.2)
        self.assertFalse(info["repeated"])
        self.assertFalse(info["ttsExpanded"])
        np.testing.assert_array_equal(reference, source[round(info["trimStartSec"] * self.rate):round(info["trimStartSec"] * self.rate) + len(reference)])

    def test_reference_needs_actual_speech_not_padded_silence(self):
        for source in (np.zeros_like(self.vocal), np.pad(self.vocal[:800], ((8000, 8000), (0, 0)))):
            with self.assertRaises(ValueError):
                worker.prepare_reference(source, self.rate)

    def test_reference_never_repeats_short_audio_to_reach_maximum(self):
        result, info = worker.prepare_reference(self.vocal, self.rate, 25)
        self.assertEqual(len(result), len(self.vocal))
        self.assertEqual(info["usedSeconds"], 2)

    def test_nonfinite_reference_and_converted_audio_are_rejected(self):
        for value in (float("nan"), float("inf")):
            damaged = self.vocal.copy()
            damaged[800] = value
            with self.assertRaises((ValueError, FloatingPointError)):
                worker.prepare_reference(damaged, self.rate)
            with self.assertRaises(FloatingPointError):
                worker.remix(self.vocal, np.zeros((len(self.vocal), 2)), damaged, self.rate)

    def test_remix_preserves_backing_without_original_vocal_double(self):
        backing = np.tile(self.vocal * 0.25, (1, 2))
        converted = self.vocal * -0.5
        mixed, meta = worker.remix(self.vocal, backing, converted, self.rate)
        np.testing.assert_allclose(mixed, backing + np.tile(converted * 2, (1, 2)), atol=1e-7)
        self.assertFalse(meta["originalVocalsAdded"])
        self.assertEqual(meta["masterGain"], 1)

    def test_hop_rounding_is_padded_at_tail_without_stretch(self):
        result, meta = worker.remix(self.vocal, np.zeros((len(self.vocal), 2)), self.vocal[:-70], self.rate)
        self.assertEqual(len(result), len(self.vocal))
        self.assertEqual(meta["alignmentPadFrames"], 70)
        self.assertTrue((result[-70:] == 0).all())
        np.testing.assert_allclose(result[:-70, :1], self.vocal[:-70] * meta["vocalGain"], atol=1e-7)

    def test_major_timeline_or_backing_mismatch_fails(self):
        for converted in (self.vocal[:8000], np.tile(self.vocal, (2, 1))):
            with self.assertRaises(ValueError):
                worker.remix(self.vocal, np.zeros((len(self.vocal), 2)), converted, self.rate)
        with self.assertRaises(ValueError):
            worker.remix(self.vocal, np.zeros((8000, 2)), self.vocal, self.rate)

    def test_mix_peak_has_headroom_and_dry_conversion_is_required(self):
        result, meta = worker.remix(self.vocal, np.ones((len(self.vocal), 2)) * 0.9, self.vocal * 4, self.rate)
        self.assertLessEqual(np.abs(result).max(), 0.920001)
        self.assertLess(meta["masterGain"], 1)
        with self.assertRaises(ValueError):
            worker.remix(self.vocal, np.tile(self.vocal, (1, 2)), np.zeros_like(self.vocal), self.rate)


class ProcessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = voice.normalize_voice_config(config(self.root), self.root)
        manifest = self.root / "manifest.json"
        manifest.write_text("[]", encoding="utf-8")
        self.cfg["manifestSha256"] = voice.digest(manifest)
        self.source = self.root / "source.wav"
        self.source.write_bytes(b"source")
        self.real_popen = subprocess.Popen

    def tearDown(self):
        self.tmp.cleanup()

    def launch(self, code):
        def popen(command, **options):
            return self.real_popen([sys.executable, "-c", code, command[-1]], **options)
        return patch.object(voice.subprocess, "Popen", popen)

    def test_configuration_requires_pinned_authorized_assets_and_explicit_pitch(self):
        for key, value in (("referenceAuthorized", False), ("referenceSha256", ""), ("steps", 4), ("steps", True),
                           ("semiToneShift", 13), ("timeoutSec", float("nan")), ("cpuThreads", True),
                           ("cpuThreads", 0), ("cpuThreads", 17), ("cpuThreads", 2.5), ("device", "gpu"), ("command", "anything")):
            bad = config(self.root)
            bad[key] = value
            with self.assertRaises(ValueError):
                voice.normalize_voice_config(bad, self.root)
        self.assertEqual(self.cfg["semiToneShift"], 0)
        self.assertEqual(self.cfg["steps"], 30)

    def test_private_device_and_cpu_budget_reach_the_owned_worker(self):
        code = "import os,sys,json; from pathlib import Path; p=Path(sys.argv[1]); (p.parent/'environment.json').write_text(json.dumps({k:os.environ[k] for k in ('CUDA_VISIBLE_DEVICES','OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS')})); sys.exit(1)"
        for device, visible in (("cpu", ""), ("cuda", "0")):
            with self.subTest(device=device):
                cfg = voice.normalize_voice_config({**config(self.root), "cpuThreads": 3, "device": device}, self.root)
                cfg["manifestSha256"] = self.cfg["manifestSha256"]
                directory = self.root / ("budget-" + device)
                with self.launch(code):
                    with self.assertRaises(voice.VoiceConversionError):
                        voice.run_conversion(cfg, self.source, directory, 1, lambda: False, lambda *_: None)
                environment = json.loads((directory / "environment.json").read_text())
                self.assertEqual(environment.pop("CUDA_VISIBLE_DEVICES"), visible)
                self.assertEqual(set(environment.values()), {str(cfg["cpuThreads"])})

    def test_changed_manifest_never_launches_a_child(self):
        self.cfg["manifestSha256"] = "0" * 64
        with patch.object(voice.subprocess, "Popen") as launch:
            with self.assertRaises(voice.VoiceConversionError):
                voice.run_conversion(self.cfg, self.source, self.root / "run", 1, lambda: False, lambda *_: None)
            launch.assert_not_called()

    def test_cancellation_and_timeout_stop_only_owned_child(self):
        unrelated = self.real_popen([sys.executable, "-c", "import time; time.sleep(10)"], creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with self.launch("import time; time.sleep(10)"):
                with self.assertRaises(voice.VoiceConversionCancelled):
                    voice.run_conversion(self.cfg, self.source, self.root / "cancel", 1, lambda: True, lambda *_: None)
                with self.assertRaises(voice.VoiceConversionError):
                    voice.run_conversion(self.cfg, self.source, self.root / "timeout", 1, lambda: False, lambda *_: None, timeout=0.1)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_failing_child_cannot_fallback_to_generated_audio(self):
        with self.launch("raise SystemExit(4)"):
            with self.assertRaises(voice.VoiceConversionError):
                voice.run_conversion(self.cfg, self.source, self.root / "fail", 1, lambda: False, lambda *_: None)
        self.assertFalse((self.root / "fail" / "song.wav").exists())

    def test_complete_worker_result_requires_matching_source_reference_and_output(self):
        code = '''import json,sys,hashlib
from pathlib import Path
p=Path(sys.argv[1]); req=json.loads(p.read_text(encoding="utf-8")); d=p.parent
def h(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
(d/"song.wav").write_bytes(b"converted")
data={"state":"complete","voiceConditioned":True,"singingVoiceVerified":False,"backend":req["voiceConversion"]["backend"],"semiToneShift":0,"sourceSha256":h(req["sourceFile"]),"referenceSha256":req["voiceConversion"]["referenceSha256"],"outputSha256":h(d/"song.wav")}
(d/"result.json").write_text(json.dumps(data),encoding="utf-8")
'''
        with self.launch(code):
            audio, result = voice.run_conversion(self.cfg, self.source, self.root / "complete", 1, lambda: False, lambda *_: None)
        self.assertEqual(audio.read_bytes(), b"converted")
        self.assertFalse(result["singingVoiceVerified"])
        with self.launch(code.replace('"singingVoiceVerified":False', '"singingVoiceVerified":True')):
            with self.assertRaises(voice.VoiceConversionError):
                voice.run_conversion(self.cfg, self.source, self.root / "false-claim", 1, lambda: False, lambda *_: None)
        with self.launch(code.replace('"outputSha256":h(d/"song.wav")', '"outputSha256":"0"*64')):
            with self.assertRaises(voice.VoiceConversionError):
                voice.run_conversion(self.cfg, self.source, self.root / "bad-hash", 1, lambda: False, lambda *_: None)


if __name__ == "__main__":
    unittest.main()
