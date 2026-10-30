"""Step 6: cached executable hash. Cache may only cause extra full hashes, never acceptance."""
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import exe_hash
from errors import VersionError


def fake_exe(path, payload=b'x', stamp=0x12345678):
    header = bytearray(4096)
    header[:2] = b'MZ'
    struct.pack_into('<I', header, 60, 128)
    header[128:132] = b'PE\0\0'
    struct.pack_into('<I', header, 136, stamp)
    data = bytes(header) + payload * 1024
    Path(path).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


class CachedHash(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.exe = self.dir / 'pbz.exe'
        self.cache = self.dir / 'cache' / 'hash-cache.json'
        self.expected = fake_exe(self.exe)
        patcher = mock.patch.dict(os.environ, {exe_hash.NO_CACHE_ENV: ''})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def verify(self, **kwargs):
        with mock.patch('exe_hash.full_sha256', wraps=exe_hash.full_sha256) as full:
            result = exe_hash.verified_sha256(self.exe, self.expected, self.cache, **kwargs)
        return result, full.call_count

    def test_first_full_then_cache_hit(self):
        self.assertEqual(self.verify(), ((self.expected, 'full'), 1))
        self.assertTrue(self.cache.is_file())
        self.assertEqual(self.verify(), ((self.expected, 'cache'), 0))

    def test_content_and_size_change_rehashes(self):
        self.verify()
        self.expected = fake_exe(self.exe, b'yy')
        self.assertEqual(self.verify(), ((self.expected, 'full'), 1))

    def test_same_size_changed_content_with_restored_mtime_is_not_accepted(self):
        self.verify()
        st = self.exe.stat()
        fake_exe(self.exe, b'z', stamp=0x87654321)
        os.utime(self.exe, ns=(st.st_atime_ns, st.st_mtime_ns))
        with self.assertRaises(VersionError):
            self.verify()

    def test_replaced_file_rehashes(self):
        self.verify()
        other = self.dir / 'new.exe'
        fake_exe(other)
        os.replace(other, self.exe)
        self.assertEqual(self.verify()[1], 1)

    def test_corrupt_or_unknown_cache_is_a_miss(self):
        for text in ('{not json', '[]', json.dumps({'schema': 99, 'entries': []}),
                     json.dumps({'schema': 1, 'entries': [{'path': 1, 'sha256': 'x'}]}), '\xff'):
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(text, encoding='latin-1')
            self.assertEqual(self.verify(), ((self.expected, 'full'), 1), text)

    def test_forged_digest_with_wrong_identity_misses(self):
        identity = exe_hash.file_identity(self.exe)
        identity['size'] += 1
        self.cache.parent.mkdir(parents=True)
        self.cache.write_text(json.dumps({'schema': 1, 'entries': [{**identity, 'sha256': self.expected}]}))
        self.assertEqual(self.verify()[1], 1)

    def test_profile_change_is_not_accepted_from_cache(self):
        self.verify()
        with self.assertRaises(VersionError):
            exe_hash.verified_sha256(self.exe, 'f' * 64, self.cache)

    def test_mismatch_raises_and_writes_no_cache(self):
        with self.assertRaises(VersionError) as ctx:
            exe_hash.verified_sha256(self.exe, '0' * 64, self.cache)
        self.assertEqual(str(ctx.exception), '游戏版本不匹配，已停止写入；此版本只支持已核对版本')
        self.assertFalse(self.cache.exists())

    def test_unwritable_cache_still_verifies(self):
        with mock.patch('exe_hash.os.replace', side_effect=PermissionError('denied')):
            self.assertEqual(self.verify(), ((self.expected, 'full'), 1))
        self.assertFalse(self.cache.exists())
        self.assertEqual(self.verify()[1], 1)

    def test_env_switch_forces_full_hash(self):
        self.verify()
        with mock.patch.dict(os.environ, {exe_hash.NO_CACHE_ENV: '1'}):
            self.assertEqual(self.verify(), ((self.expected, 'full'), 1))

    def test_use_cache_false_forces_full_hash(self):
        self.verify()
        self.assertEqual(self.verify(use_cache=False), ((self.expected, 'full'), 1))

    def test_cache_keeps_at_most_four_entries(self):
        for i in range(6):
            exe = self.dir / f'g{i}.exe'
            digest = fake_exe(exe, bytes([65 + i]))
            exe_hash.verified_sha256(exe, digest, self.cache)
        self.assertEqual(len(json.loads(self.cache.read_text())['entries']), exe_hash.MAX_ENTRIES)

    def test_pe_timestamp(self):
        self.assertEqual(exe_hash.pe_timestamp(self.exe), 0x12345678)
        (self.dir / 'bad.exe').write_bytes(b'nope')
        self.assertEqual(exe_hash.pe_timestamp(self.dir / 'bad.exe'), 0)


class CorePath(unittest.TestCase):
    def test_hash_cache_path_frozen_and_source(self):
        import runtime_paths
        self.assertEqual(runtime_paths.hash_cache_path(), runtime_paths.RESOURCE_HOME / 'hash-cache.json')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(runtime_paths.sys, 'frozen', True, create=True), \
                mock.patch.dict(os.environ, {'LOCALAPPDATA': tmp}):
            self.assertEqual(runtime_paths.hash_cache_path(), Path(tmp) / 'PBZTrainer' / 'hash-cache.json')
            self.assertEqual(runtime_paths.log_directory(), Path(tmp) / 'PBZTrainer' / 'logs')


if __name__ == '__main__':
    unittest.main()
