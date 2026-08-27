"""Crash-safe helpers for canonical and disposable local file writes."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import time


REPLACE_RETRY_SECONDS = 2.0
REPLACE_RETRY_INTERVAL = 0.01


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_with_retry(source: Path, target: Path) -> None:
    """Retry only transient sharing/access violations around one atomic replace."""
    deadline = time.monotonic() + REPLACE_RETRY_SECONDS
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(REPLACE_RETRY_INTERVAL)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace *path* from a flushed sibling temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode: int | None = None
    try:
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        pass

    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        _replace_with_retry(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_create_bytes(path: Path, data: bytes) -> None:
    """Create *path* atomically without ever replacing an existing record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # A same-directory hard link is an atomic create-if-absent operation on
        # supported Windows and POSIX filesystems. Unlike os.replace it cannot
        # overwrite an immutable proposal/event record.
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
