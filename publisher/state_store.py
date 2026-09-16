from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, Optional


class StateStore:
    """Durable key/value store for per-file playback state (last row played).

    Persisted as a single JSON file, written atomically (temp file + rename)
    so a crash mid-write can never corrupt the previous good state. Writes
    are debounced by min_save_interval_s since the player may advance many
    files every tick; call flush() to force a write (e.g. on shutdown).
    """

    def __init__(self, path: str, min_save_interval_s: float = 1.0):
        self.path = path
        self._min_save_interval_s = min_save_interval_s
        self._last_saved = 0.0
        self._dirty = False
        self._state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r") as fin:
                    return json.load(fin)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._state.get(key)

    def set(self, key: str, value: Dict[str, Any]) -> None:
        self._state[key] = value
        self._dirty = True
        if time.monotonic() - self._last_saved >= self._min_save_interval_s:
            self.flush()

    def flush(self) -> None:
        if not self._dirty:
            return
        directory = os.path.dirname(os.path.abspath(self.path)) or "."
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".fileplayer_state_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fout:
                json.dump(self._state, fout)
            self._replace_with_retry(tmp_path, self.path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        self._dirty = False
        self._last_saved = time.monotonic()

    @staticmethod
    def _replace_with_retry(src: str, dst: str, attempts: int = 4, initial_delay_s: float = 0.02) -> None:
        # On Windows, os.replace can transiently fail with PermissionError
        # (WinError 5) if something else (antivirus, an editor's file
        # watcher, ...) has the destination briefly open the instant it
        # changes - mandatory locking that POSIX rename() doesn't have.
        # A short retry clears essentially all of these without masking a
        # real, persistent permission problem.
        delay = initial_delay_s
        for attempt in range(attempts):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2
