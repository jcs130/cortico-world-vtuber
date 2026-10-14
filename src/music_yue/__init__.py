"""Durable YuE2 composition -> score check -> audio rendering, using local ComfyUI.

Never re-POST an ambiguous submission. Original scores and exact graphs are archived.
No provider fallback, reference voice or hidden lyric rewriting is performed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from .score.abc_tools import parse_abc
from .score.instrumentalize import convert_score


class YuE2:
    def __init__(self, cfg, call, save, review_error):
        self.cfg, self.call, self.save, self.review_error = cfg, call, save, review_error

    def directory(self, identity):
        if not re.fullmatch(r"yue2_[a-f0-9]{32}", identity):
            raise self.review_error()
        return Path(self.cfg["stateDir"]) / "artifacts" / identity[5:] / "yue2"

    def api(self, path, data=None):
        return self.call(self.cfg["comfy"]["endpoint"].rstrip("/") + path,
                         data, self.cfg["comfy"]["requestTimeoutSec"])

    def checkpoint(self):
        return {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": self.cfg["yue2"]["checkpoint"]}}

    def plan(self, state):
        style = state["request"]["style"]
        if state["attempt"]:
            style += " Use standard notation, preserve the requested time signature, and write complete equal-length bars with a concise melody."
        return {
            "1": self.checkpoint(),
            "2": {"class_type": "YuE2GenerateABC", "inputs": {
                "clip": ["1", 1], "style": style, "lyrics": state["lyrics"],
                "seed": state["seed"] + state["attempt"] * 1000, "mode": "full",
                "max_abc_tokens": 4096, "temperature": 0.7, "top_p": 0.9, "top_k": 30,
                "repetition_penalty": 1.005, "penalty_window": 100}},
            "3": {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}},
        }

    def render(self, state):
        seed = state["seed"]
        return {
            "1": self.checkpoint(),
            "4": {"class_type": "YuE2GenerateMusic", "inputs": {
                "clip": ["1", 1], "style": state["request"]["style"], "lyrics": state["lyrics"],
                "abc": state["abc"], "seed": seed, "mode": state["mode"],
                "max_duration": state["request"]["durationSec"], "temperature": 1.0,
                "top_p": 0.95, "top_k": 100, "repetition_penalty": 1.2, "cfg_scale": 1.0}},
            "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
            "7": {"class_type": "EmptyYuE2LatentAudio", "inputs": {"seconds": ["4", 1], "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0],
                "negative": ["5", 0], "latent_image": ["7", 0], "seed": seed, "steps": 32,
                "cfg": 1.0, "sampler_name": "dpm_2", "scheduler": "sgm_uniform", "denoise": 1.0}},
            "9": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["8", 0], "vae": ["1", 2]}},
            "20": {"class_type": "AudioAdjustVolume", "inputs": {"audio": ["9", 0], "volume": -6}},
            "10": {"class_type": "SaveAudio", "inputs": {"audio": ["20", 0], "filename_prefix": "audio/yue2-" + state["jobId"]}},
        }

    def post(self, directory, state, phase, graph):
        # Persist intent before sending. A crash or lost response requires inspection,
        # never an automatic repeat of an expensive, possibly accepted request.
        state.update(phase=phase, submissionPending=True)
        state.pop("promptId", None)
        self.save(directory / (phase + "-" + str(state["attempt"]) + "-request.json"), graph)
        self.save(directory / "state.json", state)
        result = self.api("/prompt", {"prompt": graph, "client_id": "music-" + state["jobId"]})
        prompt_id = result.get("prompt_id") if isinstance(result, dict) else None
        if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
            raise self.review_error()
        state.update(promptId=prompt_id, submissionPending=False)
        self.save(directory / "state.json", state)

    def submit(self, request, job_id):
        identity = "yue2_" + job_id
        directory = self.directory(identity)
        if (directory / "state.json").exists():
            raise self.review_error()
        state = {"version": 1, "jobId": job_id, "request": request, "attempt": 0,
                 "seed": int(job_id[:12], 16), "model": self.cfg["yue2"]["checkpoint"],
                 "lyrics": "[Intro]\n[Instrumental]\n[Outro]" if request.get("instrumental") else request["lyrics"]}
        self.post(directory, state, "plan", self.plan(state))
        return identity

    def poll(self, identity, cancelled=lambda: False):
        directory = self.directory(identity)
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        if cancelled():
            return {"state": "cancelled"}
        if state.get("submissionPending") or not state.get("promptId"):
            raise self.review_error()
        if state["model"] != self.cfg["yue2"]["checkpoint"]:
            raise self.review_error()
        prompt_id = state["promptId"]
        history = self.api("/history/" + prompt_id).get(prompt_id)
        if not history or not history.get("status", {}).get("completed"):
            if history and history.get("status", {}).get("status_str") == "error":
                return {"state": "failed"}
            return {"state": "running"}
        if history["status"].get("status_str") != "success":
            return {"state": "failed"}
        self.save(directory / (state["phase"] + "-" + str(state["attempt"]) + "-history.json"), history)
        if state["phase"] == "plan":
            try:
                abc = history["outputs"]["3"]["text"][0]
                if not isinstance(abc, str) or len(abc) > 100000:
                    raise ValueError("Invalid score output")
                (directory / ("original-" + str(state["attempt"]) + ".abc")).write_text(abc, encoding="utf-8")
                if state["request"].get("instrumental"):
                    abc, check = convert_score(abc)
                    state["lyrics"] = "\n".join("[" + line[2:].title() + "]" for line in abc.splitlines() if line.startswith("% "))
                else:
                    check = {"instrumental": False}
                score = parse_abc(abc)
                if state["request"].get("instrumental") and (score.voices["Vocal"].notes or not score.voices["Ins"].notes):
                    raise ValueError("Instrumental score has vocal notes or no instruments")
                state.update(abc=abc, scoreCheck=check, mode="full" if score.voices["Vocal"].chords else "melody")
                (directory / "render.abc").write_text(abc, encoding="utf-8")
                self.save(directory / "score-check.json", check)
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                self.save(directory / ("invalid-" + str(state["attempt"]) + ".json"), {"error": str(exc)})
                if state["attempt"] >= 1:
                    raise self.review_error() from None
                state["attempt"] += 1
                if cancelled():
                    return {"state": "cancelled"}
                self.post(directory, state, "plan", self.plan(state))
                return {"state": "running"}
            if cancelled():
                return {"state": "cancelled"}
            self.post(directory, state, "render", self.render(state))
            return {"state": "running"}
        outputs = history.get("outputs", {}).get("10", {})
        candidates = outputs.get("audio", outputs.get("audios", []))
        if len(candidates) != 1:
            raise self.review_error()
        return {"state": "done", "output": candidates[0], "generation": {
            "model": "yue2", "checkpoint": state["model"], "scoreCheck": state["scoreCheck"],
            "durationLimitSec": state["request"]["durationSec"], "endTokenStatus": "not-exposed-by-comfy-api",
            "voiceConversion": False, "steps": 32, "exportGainDb": -6}}
