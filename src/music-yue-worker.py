"""Run a provisioned, pinned ComfyUI as the dedicated offline music worker."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--config', type=Path, required=True)
    cfg = json.loads(cli.parse_args().config.read_text(encoding='utf-8-sig'))
    root = Path(cfg['comfyRoot']).resolve()
    checkpoint = (root / 'models' / 'checkpoints' / cfg['checkpoint']).resolve()
    if not checkpoint.is_relative_to(root / 'models' / 'checkpoints'):
        raise ValueError('Checkpoint path must be inside the configured runtime')
    with checkpoint.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != cfg['checkpointSha256']:
            raise ValueError('Provisioned checkpoint differs from the approved model')
    if cfg.get('host', '127.0.0.1') != '127.0.0.1' or type(cfg['port']) is not int or not 1 <= cfg['port'] <= 65535:
        raise ValueError('Expected a local worker port')
    from music_voice import set_worker_priority
    set_worker_priority('cuda')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      OMP_NUM_THREADS='4', MKL_NUM_THREADS='4', OPENBLAS_NUM_THREADS='4')
    os.chdir(root)
    sys.path.insert(0, str(root))
    for subdir in ('user', 'output'):
        (Path(cfg['stateDir']).resolve() / subdir).mkdir(parents=True, exist_ok=True)
    sys.argv = ['main.py', '--listen', '127.0.0.1', '--port', str(cfg['port']), '--offline',
                '--disable-all-custom-nodes', '--disable-auto-launch', '--reserve-vram', '3',
                '--disable-pinned-memory', '--cache-none', '--cpu-vae',
                '--user-directory', str(Path(cfg['stateDir']).resolve() / 'user'),
                '--output-directory', str(Path(cfg['stateDir']).resolve() / 'output')]
    runpy.run_path(str(root / 'main.py'), run_name='__main__')


if __name__ == '__main__':
    main()
