"""Windows ConPTY helper. No third-party packages — ctypes only."""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HPCON = ctypes.c_void_p
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
HANDLE_FLAG_INHERIT = 0x00000001
STILL_ACTIVE = 259


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _coord(cols: int, rows: int) -> ctypes.c_ulong:
    return ctypes.c_ulong((int(rows) << 16) | (int(cols) & 0xFFFF))


kernel32.CreatePipe.argtypes = [
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.POINTER(wintypes.HANDLE),
    ctypes.POINTER(SECURITY_ATTRIBUTES),
    wintypes.DWORD,
]
kernel32.CreatePseudoConsole.argtypes = [
    ctypes.c_ulong,
    wintypes.HANDLE,
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(HPCON),
]
kernel32.CreatePseudoConsole.restype = ctypes.HRESULT
kernel32.ResizePseudoConsole.argtypes = [HPCON, ctypes.c_ulong]
kernel32.ResizePseudoConsole.restype = ctypes.HRESULT
kernel32.ClosePseudoConsole.argtypes = [HPCON]
kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.BOOL,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.LPCWSTR,
    ctypes.POINTER(STARTUPINFOEXW),
    ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
kernel32.WriteFile.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.PeekNamedPipe.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]


class ConPTY:
    def __init__(self, cmdline: str, cwd: str, cols: int = 100, rows: int = 32):
        self.hpc = HPCON()
        self.h_write = wintypes.HANDLE()  # we write keystrokes
        self.h_read = wintypes.HANDLE()  # we read screen
        self.h_process = wintypes.HANDLE()
        self._attr = None
        pty_in = wintypes.HANDLE()
        pty_out = wintypes.HANDLE()
        if not kernel32.CreatePipe(ctypes.byref(pty_in), ctypes.byref(self.h_write), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.CreatePipe(ctypes.byref(self.h_read), ctypes.byref(pty_out), None, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        hr = kernel32.CreatePseudoConsole(_coord(cols, rows), pty_in, pty_out, 0, ctypes.byref(self.hpc))
        kernel32.CloseHandle(pty_in)
        kernel32.CloseHandle(pty_out)
        if hr != 0:
            self.close()
            raise OSError(f"CreatePseudoConsole failed: 0x{hr & 0xFFFFFFFF:08X}")
        self._start_process(cmdline, cwd)

    def _start_process(self, cmdline: str, cwd: str) -> None:
        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        self._attr = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(self._attr, 1, 0, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not kernel32.UpdateProcThreadAttribute(
            self._attr,
            0,
            PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
            ctypes.byref(self.hpc),
            ctypes.sizeof(self.hpc),
            None,
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        si = STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
        si.lpAttributeList = ctypes.cast(self._attr, ctypes.c_void_p)
        pi = PROCESS_INFORMATION()
        cmd = ctypes.create_unicode_buffer(cmdline)
        if not kernel32.CreateProcessW(
            None,
            cmd,
            None,
            None,
            False,
            EXTENDED_STARTUPINFO_PRESENT,
            None,
            cwd,
            ctypes.byref(si),
            ctypes.byref(pi),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        self.h_process = pi.hProcess
        kernel32.CloseHandle(pi.hThread)
        kernel32.DeleteProcThreadAttributeList(self._attr)
        self._attr = None

    def write(self, data: bytes) -> None:
        if not data:
            return
        written = wintypes.DWORD(0)
        buf = ctypes.create_string_buffer(data)
        kernel32.WriteFile(self.h_write, buf, len(data), ctypes.byref(written), None)

    def read(self, n: int = 4096) -> bytes:
        if not self.h_read:
            return b""
        avail = wintypes.DWORD(0)
        if not kernel32.PeekNamedPipe(self.h_read, None, 0, None, ctypes.byref(avail), None):
            return b""
        if avail.value == 0:
            return b""
        buf = ctypes.create_string_buffer(min(n, avail.value))
        got = wintypes.DWORD(0)
        ok = kernel32.ReadFile(self.h_read, buf, min(n, avail.value), ctypes.byref(got), None)
        if not ok or got.value == 0:
            return b""
        return buf.raw[: got.value]

    def resize(self, cols: int, rows: int) -> None:
        if self.hpc:
            kernel32.ResizePseudoConsole(self.hpc, _coord(max(2, cols), max(1, rows)))

    def alive(self) -> bool:
        if not self.h_process:
            return False
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(self.h_process, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE

    def close(self) -> None:
        if self.h_process:
            if self.alive():
                kernel32.TerminateProcess(self.h_process, 1)
            kernel32.CloseHandle(self.h_process)
            self.h_process = wintypes.HANDLE()
        if self.hpc:
            kernel32.ClosePseudoConsole(self.hpc)
            self.hpc = HPCON()
        for handle in (self.h_read, self.h_write):
            if handle:
                kernel32.CloseHandle(handle)
        self.h_read = wintypes.HANDLE()
        self.h_write = wintypes.HANDLE()
