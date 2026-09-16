from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .listeners import EventListener
from .state_store import StateStore
from .timeseries_file import TimeSeriesFile

logger = logging.getLogger(__name__)

# FileSpec: (device path in the asset hierarchy, path to the device's csv file, playback speed)
FileSpec = Tuple[str, str, float]


@dataclass
class _Entry:
    device_path: str  # hierarchical asset path published in each event's "device" field
    file_path: str  # path to the device's csv file on disk
    speed: float  # how fast this file's own timestamp axis advances relative to the wall clock
    state_key: str = field(init=False)  # absolute file_path, used as the StateStore lookup key
    ts_file: TimeSeriesFile = field(init=False)  # the loaded csv data for this device
    span_ms: int = field(init=False)  # last_ts - first_ts, at least 1; the length of one playback cycle
    anchor_wall: float = field(init=False)  # time.monotonic() value the virtual clock is measured from
    anchor_offset_ms: float = field(init=False)  # ms into the file's timestamp range at anchor_wall
    last_index: int = field(default=-1, init=False)  # row last published; -1 sentinel forces an initial publish

    def __post_init__(self) -> None:
        self.state_key = os.path.abspath(self.file_path)


class FilePlayer:
    """Plays many timeseries CSV files concurrently, each at its own speed.

    A single main loop ticks at a fixed interval. On every tick it figures
    out, for each file independently, which row corresponds to "now" given
    that file's playback speed, and publishes it if it differs from the row
    last published for that file. Speed scales how fast the file's own
    timestamp axis advances relative to the wall clock; it is not tied to
    the tick interval, so rows spaced irregularly in time are still played
    back at the right relative moments.

    A file loops back to its first row once it reaches the end, so playback
    is a continuous, repeating cycle.
    """

    def __init__(
        self,
        files: Sequence[FileSpec],
        listeners: Sequence[EventListener],
        state_store: Optional[StateStore] = None,
        tick_interval: float = 0.2,
        listener_shutdown_grace_s: float = 2.0,
    ):
        self._listeners = list(listeners)
        self._state_store = state_store
        self._tick_interval = tick_interval
        self._listener_shutdown_grace_s = listener_shutdown_grace_s
        self._stopped = False
        self._entries: List[_Entry] = [self._make_entry(*spec) for spec in files]
        self._listener_tasks: Dict[Tuple[EventListener, str], asyncio.Task] = {}

    def _make_entry(self, device_path: str, file_path: str, speed: float) -> _Entry:
        entry = _Entry(device_path=device_path, file_path=file_path, speed=max(speed, 0.0))
        entry.ts_file = TimeSeriesFile(file_path)
        entry.span_ms = max(entry.ts_file.span_ms, 1)

        saved = self._state_store.get(entry.state_key) if self._state_store else None
        if saved is not None and 0 <= saved.get("last_index", -1) < len(entry.ts_file):
            start_idx = saved["last_index"]
            logger.info("%s: resuming from durable state at row %d", device_path, start_idx)
        else:
            start_idx = entry.ts_file.find_index_nearest_to_now()
            logger.info("%s: starting fresh at row %d, nearest to current time of day", device_path, start_idx)

        entry.anchor_offset_ms = entry.ts_file.timestamps[start_idx] - entry.ts_file.first_ts
        entry.anchor_wall = time.monotonic()
        return entry

    def stop(self) -> None:
        self._stopped = True

    async def run(self) -> None:
        for listener in self._listeners:
            await listener.start()
        try:
            while not self._stopped:
                tick_start = time.monotonic()
                events = [e for e in (self._maybe_advance(entry) for entry in self._entries) if e is not None]
                if events:
                    self._dispatch(events)
                if self._state_store:
                    self._state_store.flush()
                elapsed = time.monotonic() - tick_start
                await asyncio.sleep(max(0.0, self._tick_interval - elapsed))
        finally:
            pending = [t for t in self._listener_tasks.values() if not t.done()]
            if pending:
                _, still_pending = await asyncio.wait(pending, timeout=self._listener_shutdown_grace_s)
                for task in still_pending:
                    task.cancel()
            if self._state_store:
                self._state_store.flush()
            for listener in self._listeners:
                await listener.stop()

    def _maybe_advance(self, entry: _Entry) -> Optional[Dict[str, Any]]:
        elapsed_ms = (time.monotonic() - entry.anchor_wall) * 1000.0 * entry.speed
        target_ts = entry.ts_file.first_ts + (entry.anchor_offset_ms + elapsed_ms) % entry.span_ms
        idx = entry.ts_file.find_index_for_ts(target_ts)
        if idx == entry.last_index:
            return None
        entry.last_index = idx
        if self._state_store:
            self._state_store.set(entry.state_key, {"last_index": idx, "last_row_ts": entry.ts_file.timestamps[idx]})
        return {
            "device": entry.device_path,
            "metrics": entry.ts_file.get_metrics(idx),
            "timestamp": int(time.time() * 1000),
        }

    def _dispatch(self, events: List[Dict[str, Any]]) -> None:
        # Each (listener, device) pair publishes in its own background task,
        # independent of every other pair and of the main tick loop - one
        # slow or unreachable device must never delay another device, even
        # through the same listener (e.g. many devices sharing one
        # WebSocketListener: a device that's down and retrying must not
        # stall publishing for devices that are fine). If a given pair's
        # previous publish is still in flight, this tick's event for it is
        # skipped rather than piling up concurrent publishes; the next tick
        # that finds it free carries the then-current value instead.
        for listener in self._listeners:
            for event in events:
                key = (listener, event["device"])
                task = self._listener_tasks.get(key)
                if task is not None and not task.done():
                    logger.debug("skipping dispatch to %r for %s - previous publish still in flight", listener, event["device"])
                    continue
                self._listener_tasks[key] = asyncio.create_task(self._publish_one(listener, event))

    async def _publish_one(self, listener: EventListener, event: Dict[str, Any]) -> None:
        try:
            await listener.publish(event)
        except Exception:
            logger.exception("listener %r failed to publish an event for %s", listener, event.get("device"))
