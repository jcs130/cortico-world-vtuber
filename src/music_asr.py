"""Local song ASR and alignment; model loading is offline and confined to the worker."""
from __future__ import annotations

import gc
import math
import os
import re
import zlib


def characters(text):
    return "".join(char for char in text if char.isalnum())


def decoder_confidence(tokenizer, ids, probabilities, text):
    if len(ids) != len(probabilities) or not ids:
        raise ValueError("Missing decoder scores")
    tokens = [(token, probability) for token, probability in zip(ids, probabilities) if token not in tokenizer.all_special_ids]
    if not tokens or any(not math.isfinite(probability) or not 0 < probability <= 1 for _, probability in tokens):
        raise ValueError("Invalid decoder scores")
    raw = tokenizer.decode([token for token, _ in tokens], skip_special_tokens=True)
    prefix = re.match(r"language [A-Za-z -]+<asr_text>", raw)
    offset = prefix.end() if prefix else 0
    if raw[offset:] != text:
        raise ValueError("Parsed ASR must preserve actual decoding")
    confidence = [None] * len(raw)
    previous, pending = 0, None
    for index, (_, probability) in enumerate(tokens):
        decoded = tokenizer.decode([token for token, _ in tokens[:index + 1]], skip_special_tokens=True)
        stable = len(os.path.commonprefix([decoded, raw]))
        if stable < previous:
            raise ValueError("Non-monotonic decoding")
        pending = probability if pending is None else min(pending, probability)
        if stable > previous:
            confidence[previous:stable] = [pending] * (stable - previous)
            pending = probability if stable < len(decoded) else None
            previous = stable
    if previous != len(raw) or any(value is None for value in confidence):
        raise ValueError("Unscored decoded text")
    return [value for char, value in zip(text, confidence[offset:]) if char.isalnum()], sum(math.log(value) for _, value in tokens) / len(tokens)


def aligned_transcript(text, confidence, log_probability, timestamps):
    if characters("".join(word["text"] for word in timestamps)) != characters(text) or len(confidence) != len(characters(text)):
        raise ValueError("Alignment must preserve blind transcription")
    words, cursor, position = [], 0, 0
    # Character timestamps are quantized. Keep each recognized phrase's measured
    # span rather than inventing positive durations for zero-width characters.
    for match in re.finditer(r"[^，。！？、,.!?；;\n]+[，。！？、,.!?；;\n]*", text):
        phrase = match.group()
        count = len(characters(phrase))
        if not count:
            continue
        start_cursor, collected = cursor, 0
        while collected < count and cursor < len(timestamps):
            collected += len(characters(timestamps[cursor]["text"]))
            cursor += 1
        if collected != count:
            raise ValueError("Alignment phrase boundary mismatch")
        words.append({"word": phrase, "start": timestamps[start_cursor]["start_time"], "end": timestamps[cursor - 1]["end_time"],
                      "probability": min(confidence[position:position + count])})
        position += count
    return {"recognizer": "qwen3", "confidenceSource": "decoderTokens", "text": text,
            "segments": [{"text": text, "avg_logprob": log_probability,
                          "compression_ratio": len(text.encode("utf-8")) / len(zlib.compress(text.encode("utf-8"))), "words": words}]}


def repair_zero_spans(transcript, reference, duration, realign):
    words = transcript["segments"][0]["words"]
    anchors = [word for segment in reference["segments"] for word in segment["words"]]
    if len(words) != len(anchors) or any(word["word"] != anchor["word"] for word, anchor in zip(words, anchors)):
        raise ValueError("Alignment must preserve independent phrases")
    windows = []
    for index, word in enumerate(words):
        if word["end"] > word["start"]:
            continue
        left = max(anchors[index - 1]["end"], words[index - 1]["end"]) if index else 0
        right = min(anchors[index + 1]["start"], words[index + 1]["start"]) if index + 1 < len(anchors) else duration
        if not 0 <= left < right <= duration:
            raise ValueError("Invalid measured alignment window")
        measured = realign(left, right, word["word"])
        candidate = aligned_transcript(word["word"], [word["probability"]] * len(characters(word["word"])),
                                       transcript["segments"][0]["avg_logprob"], measured)["segments"][0]["words"]
        if len(candidate) != 1 or not 0 <= candidate[0]["start"] < candidate[0]["end"] <= right - left:
            raise ValueError("Unresolved zero-width phrase")
        word.update(start=left + candidate[0]["start"], end=left + candidate[0]["end"])
        windows.append({"wordIndex": index, "start": left, "end": right})
    if windows:
        transcript["alignmentWindows"] = windows
    return transcript


def run(audio, model_dir, aligner_dir, language, device, cpu_threads, source=None):
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from transformers import AutoModelForMultimodalLM, AutoModelForTokenClassification, AutoProcessor

    torch.set_num_threads(cpu_threads)
    torch.set_num_interop_threads(cpu_threads)
    samples, rate = sf.read(audio, dtype="float32", always_2d=True)
    samples = samples.mean(axis=1)
    if rate != 16000:
        samples = resample_poly(samples, 16000, rate)
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    if source is None:
        processor = AutoProcessor.from_pretrained(model_dir, local_files_only=True)
        model = AutoModelForMultimodalLM.from_pretrained(model_dir, dtype=dtype, device_map=device, attn_implementation="sdpa", local_files_only=True)
        inputs = processor.apply_transcription_request(audio=samples, language=language, processor_kwargs={"sampling_rate": 16000}).to(model.device, model.dtype)
        with torch.inference_mode():
            output = model.generate(**inputs, max_new_tokens=1024, do_sample=False, return_dict_in_generate=True, output_scores=True)
        if output.sequences.shape[1] - inputs["input_ids"].shape[1] >= 1024:
            raise ValueError("Incomplete decoding")
        ids = output.sequences[0, inputs["input_ids"].shape[1]:].tolist()
        probabilities = model.compute_transition_scores(output.sequences, output.scores, normalize_logits=True).exp()[0].tolist()
        text = processor.decode([ids], return_format="transcription_only")[0]
        confidence, log_probability = decoder_confidence(processor.tokenizer, ids, probabilities, text)
        del model, processor, inputs, output
        gc.collect()
        if device == "cuda":
            torch.cuda.empty_cache()
    else:
        if source.get("recognizer") != "qwen3" or source.get("confidenceSource") != "decoderTokens":
            raise ValueError("Missing independent transcription")
        text = source["text"]
        confidence = [word["probability"] for segment in source["segments"] for word in segment["words"] for _ in characters(word["word"])]
        log_probability = source["segments"][0]["avg_logprob"]
    processor = AutoProcessor.from_pretrained(aligner_dir, local_files_only=True)
    model = AutoModelForTokenClassification.from_pretrained(aligner_dir, dtype=dtype, device_map=device, attn_implementation="sdpa", local_files_only=True)
    def align_segment(segment, phrase):
        inputs, word_lists = processor.prepare_forced_aligner_inputs(audio=segment, transcript=phrase, language=language)
        inputs = inputs.to(model.device, model.dtype)
        with torch.inference_mode():
            output = model(**inputs)
        return processor.decode_forced_alignment(logits=output.logits, input_ids=inputs["input_ids"], word_lists=word_lists,
                                                 timestamp_token_id=model.config.timestamp_token_id)[0]
    result = aligned_transcript(text, confidence, log_probability, align_segment(samples, text))
    if source is not None:
        result = repair_zero_spans(result, source, len(samples) / 16000,
            lambda left, right, phrase: align_segment(samples[round(left * 16000):round(right * 16000)], phrase))
    return result
