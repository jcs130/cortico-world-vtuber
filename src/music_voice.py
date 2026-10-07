"""Private deployment configuration and bounded, cancellable SVC worker."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time


STAGES = {"checking-assets": "正在核对歌声参考与模型。", "separating": "正在分离歌声与伴奏。",
          "converting": "正在转换歌声音色，游戏可以继续。", "mixing": "正在将歌声与原伴奏混合。"}
DEFAULT_CPU_THREADS = 4


def set_worker_priority(device):
    if os.name != "nt":
        os.nice(10)
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.SetPriorityClass.restype = wintypes.BOOL
    handle = kernel.GetCurrentProcess()
    kernel.SetPriorityClass(handle, 0x4000)
    if device == "cuda":
        # Separate audio jobs yield GPU scheduling priority to live speech.
        set_priority = ctypes.WinDLL("gdi32").D3DKMTSetProcessSchedulingPriorityClass
        set_priority.argtypes = [wintypes.HANDLE, ctypes.c_int]
        set_priority.restype = ctypes.c_long
        if set_priority(handle, 1) != 0:
            raise RuntimeError("Could not set background GPU scheduling priority")


class VoiceConversionError(Exception):
    pass


class VoiceConversionCancelled(Exception):
    pass


def normalize_voice_config(value, base):
    if value is None:
        return {"enabled": False}
    if not isinstance(value, dict) or not isinstance(value.get("enabled", False), bool):
        raise ValueError("Invalid voice conversion configuration")
    cfg = copy.deepcopy(value)
    cfg.setdefault("enabled", False)
    if not cfg["enabled"]:
        return cfg
    if cfg.get("backend") != "seed-vc-v1-200m-svc" or cfg.get("referenceAuthorized") is not True:
        raise ValueError("An authorized SVC reference and supported backend are required")
    for field in ("pythonFile", "assetRoot", "manifestFile", "separatorCheckpoint", "whisperCheckpoint", "referenceFile"):
        if not isinstance(cfg.get(field), str) or not cfg[field].strip():
            raise ValueError("Missing local SVC dependency")
        cfg[field] = str((base / cfg[field]).resolve())
    for field in ("manifestSha256", "separatorSha256", "whisperSha256", "referenceSha256", "upstreamRevision"):
        if not isinstance(cfg.get(field), str) or not re.fullmatch("[a-f0-9]{40}" if field == "upstreamRevision" else "[a-f0-9]{64}", cfg[field]):
            raise ValueError("Pinned SVC dependencies require checksums")
    if cfg.get("referenceKind") not in ("original-speech", "recorded-singing", "tts-speech"):
        raise ValueError("Reference provenance is required")
    cfg.setdefault("steps", 30)
    cfg.setdefault("semiToneShift", 0)
    cfg.setdefault("referenceSeconds", 15)
    cfg.setdefault("timeoutSec", 3600)
    cfg.setdefault("cpuThreads", DEFAULT_CPU_THREADS)
    cfg.setdefault("device", "cpu")
    if cfg["device"] not in ("cpu", "cuda"):
        raise ValueError("SVC device must be cpu or cuda")
    if isinstance(cfg["cpuThreads"], bool) or not isinstance(cfg["cpuThreads"], int) or not 1 <= cfg["cpuThreads"] <= 16:
        raise ValueError("SVC CPU threads must be an integer from 1 to 16")
    if isinstance(cfg["steps"], bool) or not isinstance(cfg["steps"], int) or not 30 <= cfg["steps"] <= 50:
        raise ValueError("SVC requires 30–50 diffusion steps")
    if isinstance(cfg["semiToneShift"], bool) or not isinstance(cfg["semiToneShift"], int) or not -12 <= cfg["semiToneShift"] <= 12:
        raise ValueError("Invalid explicit pitch shift")
    for field, minimum, maximum in (("referenceSeconds", 1, 25), ("timeoutSec", 10, 7200)):
        val = cfg[field]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val) or not minimum <= val <= maximum:
            raise ValueError("Invalid SVC duration/timeout")
    # Never accept an arbitrary command/runner from an audience request.
    allowed = {"enabled", "backend", "referenceAuthorized", "referenceKind", "steps", "semiToneShift", "referenceSeconds", "timeoutSec", "cpuThreads", "device",
               "pythonFile", "assetRoot", "manifestFile", "separatorCheckpoint", "whisperCheckpoint", "referenceFile",
               "manifestSha256", "separatorSha256", "whisperSha256", "referenceSha256", "upstreamRevision"}
    if set(cfg) - allowed:
        raise ValueError("Unexpected SVC configuration field")
    return cfg


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run_conversion(cfg, source, directory, seed, cancelled, on_stage, timeout=None):
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    source = source.resolve(strict=True)
    source.relative_to(directory.parent)
    if digest(cfg["manifestFile"]) != cfg["manifestSha256"]:
        raise VoiceConversionError("Pinned model manifest changed")
    request_file = directory / "request.json"
    request_file.write_text(json.dumps({"sourceFile": str(source), "seed": seed, "voiceConversion": cfg}, ensure_ascii=False), encoding="utf-8")
    command = [cfg["pythonFile"], str(Path(__file__).with_name("music_voice_worker.py")), "--request", str(request_file)]
    environment = os.environ.copy()
    threads = str(cfg.get("cpuThreads", DEFAULT_CPU_THREADS))
    environment.update(CUDA_VISIBLE_DEVICES="0" if cfg.get("device", "cpu") == "cuda" else "", OMP_NUM_THREADS=threads, MKL_NUM_THREADS=threads, OPENBLAS_NUM_THREADS=threads,
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", PYTHONUNBUFFERED="1")
    # No provider credentials are needed in the separate local audio process.
    environment = {k: v for k, v in environment.items() if not any(word in k.upper() for word in ("API_KEY", "TOKEN", "SECRET"))}
    flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    deadline = time.monotonic() + (timeout if timeout is not None else cfg["timeoutSec"])
    current_stage = None
    with (directory / "worker.log").open("wb") as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   shell=False, env=environment, creationflags=flags)
        try:
            while process.poll() is None:
                if cancelled():
                    raise VoiceConversionCancelled()
                if time.monotonic() >= deadline:
                    raise VoiceConversionError("SVC worker timed out")
                progress = directory / "progress.json"
                if progress.is_file() and progress.stat().st_size < 4096:
                    try:
                        state = json.loads(progress.read_text(encoding="utf-8")).get("stage")
                    except (OSError, ValueError):
                        state = None
                    if state in STAGES and state != current_stage:
                        on_stage(state, STAGES[state])
                        current_stage = state
                time.sleep(0.2)
            if cancelled():
                raise VoiceConversionCancelled()
            if process.returncode != 0:
                raise VoiceConversionError("SVC worker failed")
        finally:
            if process.poll() is None:
                # Terminate only the child owned by this job, never shared Comfy/TTS.
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    manifest = directory / "result.json"
    if not manifest.is_file() or manifest.stat().st_size > 65536:
        raise VoiceConversionError("Missing SVC result")
    result = json.loads(manifest.read_text(encoding="utf-8"))
    audio = directory / "song.wav"
    if result.get("state") != "complete" or result.get("voiceConditioned") is not True or result.get("singingVoiceVerified") is not False:
        raise VoiceConversionError("Unverified SVC result")
    if result.get("sourceSha256") != digest(source) or result.get("referenceSha256") != cfg["referenceSha256"] or result.get("outputSha256") != digest(audio):
        raise VoiceConversionError("SVC result provenance mismatch")
    if result.get("semiToneShift") != cfg["semiToneShift"] or result.get("backend") != cfg["backend"]:
        raise VoiceConversionError("SVC parameters changed")
    return audio, result
