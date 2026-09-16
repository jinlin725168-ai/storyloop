"""Minimal Windows Job Object wrapper for process-tree containment."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class WindowsJob:
    """Own a non-breakaway job that kills every member when closed."""

    def __init__(self) -> None:
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._bind()
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            self._raise_error()
        information = _ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        configured = self._kernel.SetInformationJobObject(
            self._handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information), ctypes.sizeof(information)
        )
        if not configured:
            self.close()
            self._raise_error()

    def _bind(self) -> None:
        handle = wintypes.HANDLE
        self._kernel.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        self._kernel.CreateJobObjectW.restype = handle
        self._kernel.SetInformationJobObject.argtypes = [handle, ctypes.c_int,
                                                         wintypes.LPVOID, wintypes.DWORD]
        self._kernel.SetInformationJobObject.restype = wintypes.BOOL
        self._kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel.OpenProcess.restype = handle
        self._kernel.AssignProcessToJobObject.argtypes = [handle, handle]
        self._kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self._kernel.TerminateJobObject.argtypes = [handle, wintypes.UINT]
        self._kernel.TerminateJobObject.restype = wintypes.BOOL
        self._kernel.WaitForSingleObject.argtypes = [handle, wintypes.DWORD]
        self._kernel.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel.CloseHandle.argtypes = [handle]
        self._kernel.CloseHandle.restype = wintypes.BOOL

    def assign(self, process_id: int) -> None:
        rights = _PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_QUERY_LIMITED_INFORMATION
        process = self._kernel.OpenProcess(rights, False, process_id)
        if not process:
            self._raise_error()
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, process):
                self._raise_error()
        finally:
            self._kernel.CloseHandle(process)

    def terminate(self) -> None:
        if self._handle and not self._kernel.TerminateJobObject(self._handle, 1):
            self._raise_error()

    def wait_empty(self, timeout_ms: int = 10_000) -> None:
        """Wait until every process assigned to this Job has terminated."""
        result = self._kernel.WaitForSingleObject(self._handle, timeout_ms)
        if result != _WAIT_OBJECT_0:
            raise OSError("Windows process Job did not become empty")

    def close(self) -> None:
        if not self._handle:
            return
        handle, self._handle = self._handle, None
        if not self._kernel.CloseHandle(handle):
            self._raise_error()

    @staticmethod
    def _raise_error() -> None:
        raise OSError(ctypes.get_last_error(), "Windows process containment failed")
