"""Verified PBZ data fields; optional teleport dispatch adapter is isolated in its own module."""
from __future__ import annotations

import json
import struct
import threading

from runtime_paths import RESOURCE_HOME, hash_cache_path
from errors import IssueQueue, VersionError
from exe_hash import verified_sha256

from win32 import (K, ProcessEntry, api, close_handle, discover, exit_code, image_path, open_process,  # noqa: F401  re-exported
                   process_first, process_next, query_path, rpm, snapshot, wpm)
from memory import DataWriteError, ProcessMixin  # noqa: F401  re-exported
from ue import UEMixin
from writer import WriterMixin
from features.targets import TargetsMixin
from features.modifiers import ModifiersMixin
from features.resources import ResourcesMixin

HOME = RESOURCE_HOME


class Game(ProcessMixin, UEMixin, WriterMixin, TargetsMixin, ModifiersMixin, ResourcesMixin):
    def __init__(self):
        self.lock = threading.RLock()
        self.handle = None
        self.issues = IssueQueue()
        self.undo = {}
        self.auto_shachi = None
        self.auto_spirit_shadow = None
        self.auto_health = None
        self.auto_medicines = {}
        self.auto_ultimate = None
        self.teleport_unlock = None
        self.floor_repair_count = 0
        self.property_cache = {}
        self.names = {}
        self.profile = json.loads((HOME / 'profile.json').read_text(encoding='utf8'))
        self.pid, self.path = discover()
        _, self.hash_source = verified_sha256(self.path, self.profile['sha256'], hash_cache_path())
        self.handle = open_process(0x410, False, self.pid)
        if not self.handle:
            raise RuntimeError('无法读取游戏进程')
        try:
            if image_path(self.handle) != self.path:
                raise RuntimeError('进程身份变化')
            self.base = self.profile['base']
            header = self.read(self.base, 4096)
            nt = struct.unpack_from('<I', header, 60)[0]
            if header[:2] != b'MZ' or header[nt:nt + 4] != b'PE\0\0':
                raise VersionError('游戏映像校验失败')
            if struct.unpack_from('<H', header, nt + 4)[0] != 0x8664:
                raise VersionError('需要已核对的 64 位游戏')
            if struct.unpack_from('<I', header, nt + 80)[0] != self.profile['image_size']:
                raise VersionError('游戏映像大小变化')
            self.refresh_pool()
            if self.name(0) != 'None' or self.name(3) != 'ByteProperty':
                raise VersionError('引擎名称池校验失败')
            self.context()
        except BaseException:
            self.close()
            raise

    def start_teleport_unlock(self):
        if self.teleport_unlock is None:
            from teleport_unlock import TeleportUnlock
            self.teleport_unlock = TeleportUnlock(self)
        self.teleport_unlock.start()
        return self.teleport_unlock.status

    def pulse_teleport_unlock(self):
        if self.teleport_unlock:
            self.teleport_unlock.pulse()
            return self.teleport_unlock.status
        return ''

    def stop_teleport_unlock(self):
        if getattr(self, 'teleport_unlock', None):
            self.teleport_unlock.stop()

    def close(self):
        if getattr(self, 'teleport_unlock', None):
            self.teleport_unlock.close()
        self.auto_shachi = None
        self.auto_spirit_shadow = None
        self.auto_health = None
        self.auto_medicines.clear()
        self.auto_ultimate = None
        if self.handle:
            close_handle(self.handle)
            self.handle = None
