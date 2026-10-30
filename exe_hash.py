"""Cached SHA256 verification of the game executable.

The cache can only ever cause an extra full hash, never let a mismatching file through:
- only full hashes that matched the profile are stored;
- a hit still compares the cached digest with the profile's expected value;
- any identity field mismatch, unreadable or malformed cache means "miss" and a full hash.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import struct

from errors import VersionError

SCHEMA = 1
MAX_ENTRIES = 4
NO_CACHE_ENV = 'PBZ_TRAINER_NO_HASH_CACHE'
MISMATCH = '游戏版本不匹配，已停止写入；此版本只支持已核对版本'
IDENTITY_KEYS = ('path', 'size', 'mtime_ns', 'ctime_ns', 'file_id', 'volume', 'pe_timestamp')


def cache_disabled():
    return os.environ.get(NO_CACHE_ENV, '').strip() not in ('', '0')


def pe_timestamp(path):
    """COFF TimeDateStamp from the first 4KB; 0 when it cannot be read."""
    try:
        with Path(path).open('rb') as f:
            header = f.read(4096)
        nt = struct.unpack_from('<I', header, 60)[0]
        if header[:2] != b'MZ' or header[nt:nt + 4] != b'PE\0\0':
            return 0
        return struct.unpack_from('<I', header, nt + 8)[0]
    except (OSError, struct.error):
        return 0


def file_identity(path):
    path = Path(path).resolve()
    st = path.stat()
    ctime = getattr(st, 'st_birthtime_ns', None)
    if ctime is None:
        ctime = st.st_ctime_ns
    return {'path': os.path.normcase(str(path)), 'size': st.st_size, 'mtime_ns': st.st_mtime_ns,
            'ctime_ns': int(ctime or 0), 'file_id': int(st.st_ino or 0), 'volume': int(st.st_dev or 0),
            'pe_timestamp': pe_timestamp(path)}


def full_sha256(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def _load_entries(cache_path):
    try:
        data = json.loads(Path(cache_path).read_text(encoding='utf8'))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if not isinstance(data, dict) or data.get('schema') != SCHEMA or not isinstance(data.get('entries'), list):
        return []
    valid = []
    for entry in data['entries']:
        if (isinstance(entry, dict) and isinstance(entry.get('path'), str) and isinstance(entry.get('sha256'), str)
                and all(type(entry.get(k)) is int for k in IDENTITY_KEYS if k != 'path')):
            valid.append(entry)
    return valid


def _save_entries(cache_path, entries):
    cache_path = Path(cache_path)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_name(cache_path.name + '.tmp')
        with tmp.open('w', encoding='utf8') as f:
            json.dump({'schema': SCHEMA, 'entries': entries[-MAX_ENTRIES:]}, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, cache_path)
        return True
    except OSError:
        return False


def verified_sha256(path, expected, cache_path=None, use_cache=True):
    """Return (digest, source) where source is 'cache' or 'full'; raise VersionError on mismatch."""
    use_cache = use_cache and cache_path is not None and not cache_disabled()
    identity = None
    if use_cache:
        try:
            identity = file_identity(path)
        except OSError:
            identity = None
    if identity is not None:
        for entry in _load_entries(cache_path):
            if all(entry.get(k) == identity[k] for k in IDENTITY_KEYS) and entry['sha256'] == expected:
                return entry['sha256'], 'cache'
    digest = full_sha256(path)
    if digest != expected:
        raise VersionError(MISMATCH)
    if identity is not None:
        try:
            after = file_identity(path)
        except OSError:
            after = None
        if after == identity:
            entries = [e for e in _load_entries(cache_path) if e.get('path') != identity['path']]
            entries.append({**identity, 'sha256': digest})
            _save_entries(cache_path, entries)
    return digest, 'full'
