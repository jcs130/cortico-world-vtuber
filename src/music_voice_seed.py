"""Offline loader for Seed-VC v1 200M SVC (upstream stays unmodified).

Checkpoint layout is documented in MUSIC_VOICE.md. Uses the official
Whisper encoder key mapping, strict learned-parameter checks and cached
weights only. This module is loaded inside a dedicated low-priority worker.
"""
import os
from pathlib import Path
import sys

def setup(asset_root, whisper_checkpoint, output_dir, seed, device="cpu"):
    ROOT = Path(asset_root).resolve()
    REPO = ROOT / "seed-vc"
    import torch
    import numpy as np
    import random
    import yaml
    import transformers
    from transformers.models.whisper.modeling_whisper import WhisperEncoder

    torch.manual_seed(seed)
    np.random.seed(seed)
    random.seed(seed)
    if device == "cpu" and (torch.cuda.is_available() or torch.version.cuda is not None):
        raise RuntimeError("This validation command requires the isolated CPU torch build")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("The configured SVC CUDA runtime is unavailable")
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)

    whisper_directory = ROOT / "models" / "openai--whisper-small"
    class ReusedWhisper(torch.nn.Module):
        def __init__(self):
            super().__init__()
            checkpoint = torch.load(whisper_checkpoint, map_location="cpu", weights_only=False)
            dims = checkpoint["dims"]
            if dims["n_audio_state"] != 768 or dims["n_audio_layer"] != 12 or dims["n_mels"] != 80:
                raise ValueError("Expected the cached multilingual Whisper small checkpoint")
            config = transformers.WhisperConfig(
                vocab_size=dims["n_vocab"], num_mel_bins=dims["n_mels"],
                d_model=dims["n_audio_state"], encoder_layers=dims["n_audio_layer"],
                encoder_attention_heads=dims["n_audio_head"], encoder_ffn_dim=4*dims["n_audio_state"],
                decoder_layers=0, decoder_attention_heads=dims["n_text_head"],
                decoder_ffn_dim=4*dims["n_text_state"], max_source_positions=dims["n_audio_ctx"],
                max_target_positions=dims["n_text_ctx"], apply_spec_augment=False,
            )
            self.encoder = WhisperEncoder(config)
            # Mapping follows transformers v4.46.3 convert_openai_to_hf.py.
            replacements = {
                "blocks": "layers", "mlp.0": "fc1", "mlp.2": "fc2",
                "mlp_ln": "final_layer_norm", ".attn.query": ".self_attn.q_proj",
                ".attn.key": ".self_attn.k_proj", ".attn.value": ".self_attn.v_proj",
                ".attn_ln": ".self_attn_layer_norm", ".attn.out": ".self_attn.out_proj",
                "positional_embedding": "embed_positions.weight", "ln_post": "layer_norm",
            }
            state = {}
            for name, tensor in checkpoint["model_state_dict"].items():
                if not name.startswith("encoder."):
                    continue
                name = name[len("encoder."):]
                for before, after in replacements.items():
                    name = name.replace(before, after)
                state[name] = tensor.float()
            # No partial or silently skipped encoder weights.
            self.encoder.load_state_dict(state, strict=True)
            self.encoder.eval().float()
            self.decoder = None  # Upstream explicitly deletes the unused decoder.

        @classmethod
        def from_pretrained(cls, model_name, **kwargs):
            if Path(model_name).resolve() != whisper_directory.resolve():
                raise ValueError("Unexpected speech encoder request")
            return cls()

        def _mask_input_features(self, input_features, attention_mask=None):
            # Whisper inference does not apply training-only SpecAugment.
            return input_features

    transformers.WhisperModel = ReusedWhisper
    sys.path.insert(0, str(REPO))
    os.chdir(REPO)
    import hf_utils
    local_models = {
        ("lj1995/VoiceConversionWebUI", "rmvpe.pt"): ROOT / "models" / "lj1995--VoiceConversionWebUI" / "rmvpe.pt",
        ("funasr/campplus", "campplus_cn_common.bin"): ROOT / "models" / "funasr--campplus" / "campplus_cn_common.bin",
    }
    def local_model(repo_id, model_filename="pytorch_model.bin", config_filename=None):
        key = (repo_id, model_filename)
        if config_filename is not None or key not in local_models:
            raise ValueError(f"Unexpected offline model request: {key}")
        path = local_models[key]
        if not path.is_file():
            raise FileNotFoundError(path)
        return str(path)
    hf_utils.load_custom_model_from_hf = local_model
    import inference
    inference.device = torch.device(device)
    def offline_save(path, waveform, sample_rate, **kwargs):
        # torchaudio2.11 routes save through optional torchcodec. Keep this
        # isolated WAV-only adapter offline and preserve finite float samples.
        import soundfile as sf
        destination = Path(path).resolve()
        destination.relative_to(Path(output_dir).resolve())
        data = waveform.detach().cpu().float().numpy()
        if not np.isfinite(data).all():
            raise FloatingPointError("Refusing to save non-finite converted audio")
        sf.write(destination, data.T, sample_rate, subtype="FLOAT")
    inference.torchaudio.save = offline_save
    original_load_checkpoint = inference.load_checkpoint
    def checked_checkpoint(model, optimizer, path, **kwargs):
        checkpoint = torch.load(path, map_location="cpu", weights_only=False)
        for module_name, module in model.items():
            supplied = checkpoint["net"].get(module_name, {})
            supplied = {name.removeprefix("module."): value for name, value in supplied.items()}
            # input_pos and sinusoidal frequency buffers are rebuilt by the
            # upstream architecture. Require every learned parameter exactly.
            invalid = [name for name, value in module.named_parameters()
                       if name not in supplied or supplied[name].shape != value.shape]
            if invalid:
                raise ValueError(f"Required SVC parameters are missing or mismatched: {module_name}: {invalid}")
        del checkpoint
        return original_load_checkpoint(model, optimizer, path, **kwargs)
    inference.load_checkpoint = checked_checkpoint
    os.environ["HF_HUB_CACHE"] = str(ROOT / "cache" / "huggingface" / "hub")
    config = yaml.safe_load((ROOT / "models" / "Plachta--Seed-VC" / "config_dit_mel_seed_uvit_whisper_base_f0_44k.yml").read_text())
    config["device"] = device
    config["model_params"]["vocoder"]["name"] = str(ROOT / "models" / "nvidia--bigvgan_v2_44khz_128band_512x")
    config["model_params"]["speech_tokenizer"]["name"] = str(whisper_directory)
    config_path = Path(output_dir) / f"config-svc-{device}.yml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return inference, config_path
