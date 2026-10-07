"""Separate vocals, convert their timbre with Seed-VC SVC, then remix.

Run in the deployment's isolated audio environment. No network downloads,
playback, catalog mutation or speaker-identity acceptance happens here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

# Embedded Python runtimes may omit the executable script's directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from music_voice import DEFAULT_CPU_THREADS, set_worker_priority


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, data):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def stage(directory, name):
    save_json(directory / "progress.json", {"stage": name, "updatedAt": time.time()})


def audio_stats(audio, rate):
    import numpy as np
    if audio.ndim != 2 or audio.shape[1] not in (1, 2) or not len(audio) or rate < 8000:
        raise ValueError("Invalid audio shape")
    if not np.isfinite(audio).all():
        raise FloatingPointError("Non-finite audio")
    energy = np.mean(audio.astype("float64") ** 2, axis=1)
    windows = [math.sqrt(float(energy[i:i + rate // 10].mean())) for i in range(0, len(audio), rate // 10)]
    return {"seconds": len(audio) / rate, "sampleRate": rate, "channels": audio.shape[1],
            "peak": float(np.abs(audio).max()), "rms": math.sqrt(float(energy.mean())),
            "activeFraction": sum(value >= 0.002 for value in windows) / len(windows),
            "nearFullScaleFraction": float(np.mean(np.abs(audio) >= 0.999)), "finite": True}


def require_audio(audio, rate, duration=None):
    stats = audio_stats(audio, rate)
    if stats["rms"] < 0.002 or stats["activeFraction"] < 0.15 or stats["peak"] > 1.5:
        raise ValueError("Silent, sparse or overloaded audio")
    if duration is not None and abs(stats["seconds"] - duration) > 0.1:
        raise ValueError("Conversion changed the timeline")
    return stats


def prepare_reference(audio, rate, max_seconds=15):
    """Trim only edge silence; no repeated/TTS-expanded identity reference."""
    import numpy as np
    mono = audio.mean(axis=1)
    if not np.isfinite(mono).all() or not 1 <= len(mono) / rate <= 30:
        raise ValueError("Reference must contain 1–30 seconds of finite speech")
    width = max(1, rate // 50)
    energy = [math.sqrt(float(np.mean(mono[i:i + width].astype("float64") ** 2))) for i in range(0, len(mono), width)]
    voiced = [i for i, value in enumerate(energy) if value >= max(0.003, max(energy) * 0.025)]
    if not voiced or len(voiced) * width / rate < 1:
        raise ValueError("Too little audible reference speech")
    start = max(0, voiced[0] * width - rate // 20)
    stop = min(len(mono), (voiced[-1] + 1) * width + rate // 20, start + round(max_seconds * rate))
    prepared = mono[start:stop, None]
    require_audio(prepared, rate)
    return prepared, {"trimStartSec": start / rate, "usedSeconds": len(prepared) / rate,
                      "repeated": False, "ttsExpanded": False}


def verify_assets(cfg):
    root = Path(cfg["assetRoot"])
    if sha256(cfg["manifestFile"]) != cfg["manifestSha256"]:
        raise ValueError("SVC manifest checksum mismatch")
    rows = json.loads(Path(cfg["manifestFile"]).read_text(encoding="utf-8-sig"))
    if not isinstance(rows, list) or len(rows) != 7:
        raise ValueError("Incomplete pinned SVC model manifest")
    checked = {}
    for row in rows:
        path = (root / "models" / row["directory"] / row["file"]).resolve()
        path.relative_to(root.resolve())
        if path.stat().st_size != row["bytes"] or sha256(path) != row["sha256"]:
            raise ValueError("SVC model checksum mismatch")
        checked[row["directory"] + "/" + row["file"]] = row["sha256"]
    for field, hash_field in (("separatorCheckpoint", "separatorSha256"), ("whisperCheckpoint", "whisperSha256"), ("referenceFile", "referenceSha256")):
        if sha256(cfg[field]) != cfg[hash_field]:
            raise ValueError("Pinned audio/model checksum mismatch")
        checked[field] = cfg[hash_field]
    repo = root / "seed-vc"
    revision = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, timeout=10).stdout.decode().strip()
    if revision != cfg["upstreamRevision"]:
        raise ValueError("Unexpected Seed-VC revision")
    subprocess.run(["git", "-C", str(repo), "diff", "--quiet", "HEAD", "--"], check=True, timeout=10)
    return checked


def separate(source, directory, cfg):
    import numpy as np
    import soundfile as sf
    import torch
    import torchaudio
    waveform, rate = sf.read(source, dtype="float32", always_2d=True)
    source_stats = require_audio(waveform, rate)
    if not 1 <= source_stats["seconds"] <= 120:
        raise ValueError("Song must be between 1 and 120 seconds")
    audio = torch.from_numpy(waveform.T.copy())
    if audio.shape[0] == 1:
        audio = audio.repeat(2, 1)
    bundle = torchaudio.pipelines.HDEMUCS_HIGH_MUSDB_PLUS
    if rate != bundle.sample_rate:
        audio = torchaudio.functional.resample(audio, rate, bundle.sample_rate)
    # Construct without get_model(), which may download a checkpoint.
    model = bundle._model_factory_func()
    model.load_state_dict(torch.load(cfg["separatorCheckpoint"], map_location="cpu", weights_only=True), strict=True)
    model.eval()
    mean, deviation = audio.mean(), audio.mean(0).std().clamp_min(1e-6)
    normal = (audio - mean) / deviation
    length = audio.shape[-1]
    chunk, overlap = bundle.sample_rate * 8, bundle.sample_rate // 2
    result = torch.zeros(len(model.sources), 2, length)
    weight = torch.zeros(length)
    with torch.inference_mode():
        for offset in range(0, length, chunk - overlap):
            end = min(length, offset + chunk)
            stems = model(normal[:, offset:end].unsqueeze(0))[0] * deviation
            if not torch.isfinite(stems).all():
                raise FloatingPointError("Non-finite separated stems")
            envelope = torch.ones(end - offset)
            fade = min(overlap, len(envelope))
            if offset:
                envelope[:fade] = torch.linspace(0, 1, fade)
            if end < length:
                envelope[-fade:] = torch.linspace(1, 0, fade)
            result[:, :, offset:end] += stems * envelope
            weight[offset:end] += envelope
    if (weight <= 0).any():
        raise ValueError("Uncovered separation window")
    result /= weight
    vocals = result[model.sources.index("vocals")].numpy().T
    # A residual backing preserves the original time/phase and overall mix.
    # Stem leakage is possible and is checked during dry-vocal listening.
    backing = audio.numpy().T - vocals
    require_audio(vocals, bundle.sample_rate, source_stats["seconds"])
    audio_stats(backing, bundle.sample_rate)
    sf.write(directory / "source-vocals.wav", vocals, bundle.sample_rate, subtype="FLOAT")
    sf.write(directory / "backing.wav", backing, bundle.sample_rate, subtype="FLOAT")
    return source_stats


def remix(original, backing, converted, rate):
    import numpy as np
    original_stats = require_audio(original, rate)
    converted_stats = require_audio(converted, rate, original_stats["seconds"])
    audio_stats(backing, rate)
    if len(backing) != len(original):
        raise ValueError("Backing timeline mismatch")
    # Seed's hop rounding can omit <100 ms; never stretch or shift lyric time.
    delta = len(original) - len(converted)
    if delta > 0:
        converted = np.pad(converted, ((0, delta), (0, 0)))
    elif delta < 0:
        converted = converted[:len(original)]
    if converted.shape[1] == 1:
        converted = np.repeat(converted, 2, axis=1)
    gain = min(2.0, max(0.5, original_stats["rms"] / converted_stats["rms"]))
    mixed = backing + converted * gain
    peak = float(np.abs(mixed).max())
    gain_all = min(1.0, 0.92 / max(peak, 1e-9))
    mixed *= gain_all
    return mixed, {"vocalGain": gain, "masterGain": gain_all, "alignmentPadFrames": max(delta, 0),
                   "alignmentTrimFrames": max(-delta, 0), "originalVocalsAdded": False,
                   "qa": require_audio(mixed, rate, original_stats["seconds"])}


def run(request_file):
    request = json.loads(Path(request_file).read_text(encoding="utf-8"))
    cfg = request["voiceConversion"]
    directory = Path(request_file).resolve().parent
    source = Path(request["sourceFile"]).resolve(strict=True)
    source.relative_to(directory.parent)
    threads = cfg.get("cpuThreads", DEFAULT_CPU_THREADS)
    device = cfg.get("device", "cpu")
    os.environ.update(CUDA_VISIBLE_DEVICES="0" if device == "cuda" else "", OMP_NUM_THREADS=str(threads), MKL_NUM_THREADS=str(threads), OPENBLAS_NUM_THREADS=str(threads),
                      HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HOME=str(Path(cfg["assetRoot"]) / "cache" / "huggingface"))
    set_worker_priority(device)
    import numpy as np
    import soundfile as sf
    import torch
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(threads)
    started = time.monotonic()
    record = {"version": 1, "backend": "seed-vc-v1-200m-svc", "state": "running", "seed": request["seed"],
              "voiceConditioned": False, "singingVoiceVerified": False, "device": device, "threads": threads,
              "diffusionSteps": cfg["steps"], "f0Condition": True, "autoF0Adjust": False,
              "semiToneShift": cfg["semiToneShift"], "lengthAdjust": 1.0, "referenceKind": cfg["referenceKind"],
              "sourceSha256": sha256(source), "referenceSha256": cfg["referenceSha256"]}
    save_json(directory / "result.json", record)
    try:
        stage(directory, "checking-assets")
        record["assetHashes"] = verify_assets(cfg)
        stage(directory, "separating")
        record["sourceQa"] = separate(source, directory, cfg)
        reference, rate = sf.read(cfg["referenceFile"], dtype="float32", always_2d=True)
        reference, record["referencePreparation"] = prepare_reference(reference, rate, cfg["referenceSeconds"])
        sf.write(directory / "reference.wav", reference, rate, subtype="PCM_24")
        stage(directory, "converting")
        from music_voice_seed import setup
        inference, config = setup(cfg["assetRoot"], cfg["whisperCheckpoint"], directory, request["seed"], device=device)
        inference.main(SimpleNamespace(source=str(directory / "source-vocals.wav"), target=str(directory / "reference.wav"),
                       output=str(directory), diffusion_steps=cfg["steps"], length_adjust=1.0, inference_cfg_rate=0.7,
                       f0_condition=True, auto_f0_adjust=False, semi_tone_shift=cfg["semiToneShift"], fp16=False,
                       checkpoint=str(Path(cfg["assetRoot"]) / "models" / "Plachta--Seed-VC" / "DiT_seed_v2_uvit_whisper_base_f0_44k_bigvgan_pruned_ft_ema_v2.pth"), config=str(config)))
        converted_files = list(directory.glob("vc_*.wav"))
        if len(converted_files) != 1:
            raise ValueError("Expected exactly one converted vocal file")
        converted, converted_rate = sf.read(converted_files[0], dtype="float32", always_2d=True)
        original, rate = sf.read(directory / "source-vocals.wav", dtype="float32", always_2d=True)
        backing, backing_rate = sf.read(directory / "backing.wav", dtype="float32", always_2d=True)
        if converted_rate != rate or backing_rate != rate:
            raise ValueError("Unexpected SVC sample rate")
        record["convertedQa"] = require_audio(converted, rate, len(original) / rate)
        if record["convertedQa"]["nearFullScaleFraction"] > 0.01:
            raise ValueError("Excessive converted vocal clipping")
        sf.write(directory / "converted-vocals.wav", converted, rate, subtype="FLOAT")
        stage(directory, "mixing")
        mixed, record["mix"] = remix(original, backing, converted, rate)
        sf.write(directory / "song.wav", mixed, rate, subtype="PCM_16")
        playback_vocals = converted[:len(original)]
        if len(playback_vocals) < len(original):
            playback_vocals = np.pad(playback_vocals, ((0, len(original) - len(playback_vocals)), (0, 0)))
        playback_vocals *= record["mix"]["vocalGain"] * record["mix"]["masterGain"]
        sf.write(directory / "playback-vocals.wav", playback_vocals, rate, subtype="PCM_16")
        record.update(state="complete", voiceConditioned=True, singingVoiceVerified=False,
                      sourceVocalSha256=sha256(directory / "source-vocals.wav"), backingSha256=sha256(directory / "backing.wav"),
                      convertedVocalSha256=sha256(directory / "converted-vocals.wav"), outputSha256=sha256(directory / "song.wav"),
                      elapsedSeconds=round(time.monotonic() - started, 3))
        save_json(directory / "result.json", record)
        stage(directory, "complete")
    except Exception:
        record.update(state="failed", elapsedSeconds=round(time.monotonic() - started, 3))
        save_json(directory / "result.json", record)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    run(parser.parse_args().request)
