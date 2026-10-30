"""Process memory access for Game: liveness, bounded reads and verified raw writes."""
from __future__ import annotations

import ctypes as C
import ctypes.wintypes as W
import struct

from errors import ReadError, WriteError
from win32 import close_handle, exit_code, image_path, open_process, rpm, wpm


class DataWriteError(WriteError):
    def __init__(self, message, bytes_written):
        super().__init__(message)
        self.bytes_written = bytes_written


class ProcessMixin:
    def alive(self):
        code = W.DWORD()
        return bool(self.handle and exit_code(self.handle, C.byref(code)) and code.value == 259)

    def read(self, address, size):
        if not self.handle or not (0x10000 <= address < 0x7fffffffffff) or not 0 < size <= 1024 * 1024:
            raise RuntimeError('读取目标无效')
        buffer = C.create_string_buffer(size)
        done = C.c_size_t()
        if not rpm(self.handle, address, buffer, size, C.byref(done)) or done.value != size:
            raise ReadError('游戏内存读取失败，可能正在切场景或已退出')
        return buffer.raw[:size]

    def qword(self, address):
        return struct.unpack('<Q', self.read(address, 8))[0]

    def raw_write(self, address, payload):
        if len(payload) not in (4, 8, 16) or not self.alive():
            raise RuntimeError('写入范围或进程状态无效')
        h = open_process(0x438, False, self.pid)
        if not h:
            raise RuntimeError('无法取得数据写入权限')
        try:
            if image_path(h) != self.path:
                raise RuntimeError('写入进程身份变化')
            done = C.c_size_t()
            buffer = C.create_string_buffer(payload)
            ok = wpm(h, address, buffer, len(payload), C.byref(done))
            if not ok or done.value != len(payload):
                raise DataWriteError('数据写入失败，错误码 ' + str(C.get_last_error()), done.value)
        finally:
            close_handle(h)

