"""Thin kernel32 bindings: process discovery, handles and raw memory calls."""
from __future__ import annotations

import ctypes as C
import ctypes.wintypes as W
from pathlib import Path

K = C.WinDLL('kernel32', use_last_error=True)


def api(name, args, result):
    f = getattr(K, name)
    f.argtypes, f.restype = args, result
    return f


open_process = api('OpenProcess', [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
close_handle = api('CloseHandle', [W.HANDLE], W.BOOL)
rpm = api('ReadProcessMemory', [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL)
wpm = api('WriteProcessMemory', [W.HANDLE, C.c_void_p, C.c_void_p, C.c_size_t, C.POINTER(C.c_size_t)], W.BOOL)
snapshot = api('CreateToolhelp32Snapshot', [W.DWORD, W.DWORD], W.HANDLE)
query_path = api('QueryFullProcessImageNameW', [W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD)], W.BOOL)
exit_code = api('GetExitCodeProcess', [W.HANDLE, C.POINTER(W.DWORD)], W.BOOL)


class ProcessEntry(C.Structure):
    _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD), ('heap', C.c_size_t),
               ('module', W.DWORD), ('threads', W.DWORD), ('parent', W.DWORD), ('priority', W.LONG),
               ('flags', W.DWORD), ('exe', W.WCHAR * 260)]


process_first = api('Process32FirstW', [W.HANDLE, C.POINTER(ProcessEntry)], W.BOOL)
process_next = api('Process32NextW', [W.HANDLE, C.POINTER(ProcessEntry)], W.BOOL)


def image_path(handle):
    buffer = C.create_unicode_buffer(32768)
    size = W.DWORD(len(buffer))
    if not query_path(handle, 0, buffer, C.byref(size)):
        raise RuntimeError('无法读取进程路径')
    return Path(buffer.value)


def discover():
    found = []
    snap = snapshot(2, 0)
    if snap == C.c_void_p(-1).value:
        raise RuntimeError('无法枚举游戏进程')
    try:
        entry = ProcessEntry()
        entry.size = C.sizeof(entry)
        ok = process_first(snap, C.byref(entry))
        while ok:
            if entry.exe.casefold() == 'pbz.exe':
                h = open_process(0x1000, False, entry.pid)
                if h:
                    try:
                        p = image_path(h)
                        if p.as_posix().casefold().endswith('/pbz/platform/steam/pbz.exe'):
                            found.append((entry.pid, p))
                    finally:
                        close_handle(h)
            ok = process_next(snap, C.byref(entry))
    finally:
        close_handle(snap)
    if len(found) != 1:
        raise RuntimeError('请先运行游戏；需要且只能存在一个受支持的游戏进程')
    return found[0]
