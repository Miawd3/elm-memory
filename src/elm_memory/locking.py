"""Cross-platform single-writer lock for ELM mutations."""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import time
import uuid


LOCK_FORMAT_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
        open_process.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (ctypes.c_void_p,)
        close_handle.restype = ctypes.c_int
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
        get_exit_code.restype = ctypes.c_int
        handle = open_process(process_query_limited_information, False, pid)
        if handle:
            exit_code = ctypes.c_uint32()
            try:
                if get_exit_code(handle, ctypes.byref(exit_code)):
                    return exit_code.value == 259  # STILL_ACTIVE
                return True
            finally:
                close_handle(handle)
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class WriterLockError(RuntimeError):
    def __init__(self, message: str, record: dict | None = None):
        super().__init__(message)
        self.record = record or {}


class WriterLock:
    """Atomic lock-file guard used by index and canonical writers."""

    def __init__(
        self,
        root: Path,
        operation: str,
        *,
        timeout: float = 10.0,
        poll_interval: float = 0.05,
        recover_stale: bool = False,
        stale_after: float = 300.0,
    ) -> None:
        self.root = Path(root)
        self.path = self.root / ".elm" / "writer.lock"
        self.recovery_log = self.root / ".elm" / "writer-lock-recovery.jsonl"
        self.operation = operation
        self.timeout = max(0.0, timeout)
        self.poll_interval = max(0.01, poll_interval)
        self.recover_stale = recover_stale
        self.stale_after = max(0.0, stale_after)
        self.host = socket.gethostname()
        self.token = str(uuid.uuid4())
        self.record = {
            "format_version": LOCK_FORMAT_VERSION,
            "pid": os.getpid(),
            "host": self.host,
            "started_at": _now_iso(),
            "operation": operation,
            "token": self.token,
        }
        self.acquired = False

    def _read_record(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
            return None

    def _age_seconds(self) -> float:
        try:
            return max(0.0, time.time() - self.path.stat().st_mtime)
        except FileNotFoundError:
            return 0.0

    def _is_stale(self, record: dict | None) -> bool:
        age = self._age_seconds()
        if not record:
            return age >= self.stale_after
        if record.get("host") == self.host:
            try:
                return not _process_exists(int(record.get("pid", -1)))
            except (TypeError, ValueError):
                return age >= self.stale_after
        return age >= self.stale_after

    def _log_recovery(self, record: dict | None) -> None:
        event = {
            "recovered_at": _now_iso(),
            "recovered_by_pid": os.getpid(),
            "recovered_by_host": self.host,
            "requested_operation": self.operation,
            "previous_lock": record,
        }
        self.recovery_log.parent.mkdir(parents=True, exist_ok=True)
        with self.recovery_log.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _recover_if_allowed(self, record: dict | None) -> bool:
        if not self.recover_stale or not self._is_stale(record):
            return False
        current = self._read_record()
        if record is not None and current != record:
            return False
        if record is None and current is not None:
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            return True
        self._log_recovery(record)
        return True

    def acquire(self) -> "WriterLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        payload = (json.dumps(self.record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        last_record: dict | None = None
        while True:
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                last_record = self._read_record()
                if self._recover_if_allowed(last_record):
                    continue
                if time.monotonic() >= deadline:
                    owner = "unknown owner"
                    if last_record:
                        owner = (
                            f"pid={last_record.get('pid')} host={last_record.get('host')} "
                            f"operation={last_record.get('operation')}"
                        )
                    raise WriterLockError(
                        f"ELM writer lock is unavailable ({owner}). "
                        "Wait for the writer or explicitly recover a proven stale lock.",
                        last_record,
                    )
                time.sleep(self.poll_interval)
                continue
            try:
                os.write(descriptor, payload)
                os.fsync(descriptor)
            except BaseException:
                os.close(descriptor)
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                raise
            else:
                os.close(descriptor)
            self.acquired = True
            return self

    def release(self) -> None:
        if not self.acquired:
            return
        current = self._read_record()
        if current and current.get("token") == self.token:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False

    def __enter__(self) -> "WriterLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()
