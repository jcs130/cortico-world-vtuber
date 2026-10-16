"""Server-owned, hash-pinned references. Requests select IDs, never file paths."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

SCHEMA = 'tts-voice-catalog-v1'
MAX_VOICES = 64


@dataclass(frozen=True)
class VoiceReference:
    id: str
    label: str
    description: str
    reference_file: str
    reference_text: str
    reference_sha256: str
    reference_seconds: float | None = None

    def public(self) -> dict:
        # Local paths and the private deployment configuration never leave the host.
        return {key: getattr(self, key) for key in (
            'id', 'label', 'description', 'reference_sha256', 'reference_seconds')}


class VoiceCatalog:
    def __init__(self, config: dict):
        self.default_id = config['voice']
        self.references: dict[str, VoiceReference] = {}
        self._add({'id': self.default_id, 'label': config.get('voice_label', self.default_id),
                   **{key: config[key] for key in ('reference_file', 'reference_text', 'reference_sha256')}},
                  Path.cwd())
        source = config.get('voice_catalog_file')
        if source:
            path = Path(source).resolve()
            body = json.loads(path.read_text(encoding='utf-8-sig'))
            if not isinstance(body, dict) or body.get('schema') != SCHEMA or not isinstance(body.get('voices'), list):
                raise ValueError('Invalid voice catalog schema')
            if len(body['voices']) >= MAX_VOICES:
                raise ValueError('Voice catalog exceeds the configured reference limit')
            for row in body['voices']:
                self._add(row, path.parent)

    def _add(self, row: dict, root: Path) -> None:
        if not isinstance(row, dict):
            raise ValueError('Voice catalog entry must be an object')
        vid = row.get('id')
        if not isinstance(vid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}', vid):
            raise ValueError('Invalid voice ID')
        if vid in self.references:
            raise ValueError('Duplicate voice ID')
        reference = row.get('reference_file')
        text = row.get('reference_text')
        digest = row.get('reference_sha256')
        if not isinstance(reference, str) or not reference or not isinstance(text, str) or not text.strip():
            raise ValueError('A voice needs a reference file and matching transcript')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('A voice needs a pinned SHA256')
        path = (root / reference).resolve()
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('Reference audio exceeds 32 MiB')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Configured reference audio hash mismatch')
        label, description = row.get('label', vid), row.get('description', '')
        if not isinstance(label, str) or not isinstance(description, str):
            raise ValueError('Voice labels and descriptions must be strings')
        seconds = row.get('reference_seconds')
        if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 300):
            raise ValueError('Invalid reference duration')
        self.references[vid] = VoiceReference(vid, label, description, str(path), text, digest, seconds)

    def resolve(self, voice=None) -> VoiceReference:
        if voice is None or voice in ('', 'default'):
            voice = self.default_id
        if not isinstance(voice, str) or voice not in self.references:
            raise ValueError('Unknown configured voice ID')
        return self.references[voice]

    def public(self) -> dict:
        return {'schema': SCHEMA, 'default_voice': self.default_id,
                'voices': list(self.references), 'catalog': [v.public() for v in self.references.values()]}
