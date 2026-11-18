"""Optional, single-writer storage for the existing verified learning snapshot."""
from __future__ import annotations

import os
from pathlib import Path

from .domains.lifelong_math import LifelongMathAgent


class ToolMemory:
    """Hold an OS lock while a tool session loads and saves one memory directory."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._handle = (self.directory / '.session.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self._handle.tell() == 0:
                    self._handle.write(b'\0')
                    self._handle.flush()
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.close()
            raise ValueError('memory directory is already in use or cannot be locked') from exc

    def load(self) -> LifelongMathAgent:
        """Revalidate a complete snapshot; never reset a partial or invalid one."""
        if any((self.directory / name).exists() for name in ('rules.json', 'learning.json')):
            try:
                return LifelongMathAgent.load(self.directory)
            except TypeError as exc:
                raise ValueError('invalid memory snapshot: ' + str(exc)[:512]) from exc
        return LifelongMathAgent()

    def save(self, agent: LifelongMathAgent) -> None:
        """Persist rules and learning examples through the bounded snapshot API."""
        agent.save(self.directory)

    def close(self) -> None:
        """Release the lock; the OS also releases it if the process exits."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None
