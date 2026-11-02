"""Durable checkpoints and single-writer ownership for the local worker.

The JSON records remain compatible with existing installations. A write is not
acknowledged until both its bytes and directory entry have reached the OS. The
process lock prevents a second API worker from recovering live work as abandoned.
"""
import json
import os
from pathlib import Path
import tempfile


def save_checkpoint(directory, identifier, value):
    from uuid import UUID
    directory = Path(directory)
    identifier = str(UUID(identifier))
    target = directory / (identifier + ".json")
    if directory.is_symlink() or target.is_symlink():
        raise ValueError("执行记录路径无效")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class WorkerOwnership:
    """OS releases the exclusive claim after a crash, without stale PID leases."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.handle = None

    def acquire(self):
        import fcntl
        if self.handle is not None:
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.directory / ".worker.lock"
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        handle = os.fdopen(descriptor, "r+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            raise RuntimeError("ModelDesk 执行服务已经运行；不能启动第二个写入同一任务目录的工作进程") from None
        self.handle = handle
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        os.fsync(handle.fileno())

    def release(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
