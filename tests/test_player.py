import asyncio
import os
import time
from typing import Any, Dict, List

from publisher.listeners import EventListener
from publisher.player import FilePlayer
from publisher.state_store import StateStore


class RecordingListener(EventListener):
    def __init__(self):
        self.events: List[Dict[str, Any]] = []

    async def publish(self, event: Dict[str, Any]) -> None:
        self.events.append(event)


class SlowListener(EventListener):
    """Simulates a down/slow push target (e.g. WebSocketListener retrying)."""

    def __init__(self, delay: float):
        self.delay = delay
        self.calls: List[Dict[str, Any]] = []
        self.concurrent = 0
        self.max_concurrent = 0

    async def publish(self, event: Dict[str, Any]) -> None:
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        self.calls.append(event)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.concurrent -= 1


class PartiallySlowListener(EventListener):
    """One shared listener where only some devices (e.g. unreachable URLs) are slow.

    Models WebSocketListener handling many devices: each device gets its
    own independent connection/latency, so one stuck device must never
    delay publishing for the others through this same listener instance.
    """

    def __init__(self, slow_devices: set, delay: float):
        self.slow_devices = slow_devices
        self.delay = delay
        self.attempts: List[str] = []
        self.events: List[Dict[str, Any]] = []

    async def publish(self, event: Dict[str, Any]) -> None:
        self.attempts.append(event["device"])
        if event["device"] in self.slow_devices:
            await asyncio.sleep(self.delay)
        self.events.append(event)


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return str(path)


async def _run_briefly(player: FilePlayer, seconds: float) -> None:
    task = asyncio.create_task(player.run())
    await asyncio.sleep(seconds)
    player.stop()
    await task


def test_initial_publish_uses_nearest_to_now_for_single_row_file(tmp_path):
    path = _write(tmp_path, "device.csv", "timestamp,A\n100,42\n")
    listener = RecordingListener()
    player = FilePlayer([("Org/Device", path, 1.0)], [listener], tick_interval=0.01)

    asyncio.run(_run_briefly(player, 0.05))

    assert len(listener.events) == 1
    event = listener.events[0]
    assert event["device"] == "Org/Device"
    assert event["metrics"] == {"A": 42}
    assert isinstance(event["timestamp"], int)


def test_resumes_from_durable_state(tmp_path):
    path = _write(tmp_path, "device.csv", "timestamp,A\n100,1\n200,2\n300,3\n")
    state_store = StateStore(str(tmp_path / "state.json"))
    state_store.set(os.path.abspath(path), {"last_index": 1, "last_row_ts": 200})

    listener = RecordingListener()
    player = FilePlayer([("Org/Device", path, 1.0)], [listener], state_store=state_store, tick_interval=0.01)

    asyncio.run(_run_briefly(player, 0.03))

    assert listener.events[0]["metrics"] == {"A": 2}


def test_state_store_persists_last_index_across_player_instances(tmp_path):
    path = _write(tmp_path, "device.csv", "timestamp,A\n100,1\n200,2\n300,3\n")
    state_path = str(tmp_path / "state.json")

    state_store = StateStore(state_path)
    player = FilePlayer([("Org/Device", path, 1.0)], [RecordingListener()], state_store=state_store, tick_interval=0.01)
    asyncio.run(_run_briefly(player, 0.03))
    state_store.flush()

    reloaded_store = StateStore(state_path)
    saved = reloaded_store.get(os.path.abspath(path))
    assert saved is not None
    assert saved["last_index"] in (0, 1, 2)


def test_maybe_advance_publishes_only_when_the_target_row_changes(monkeypatch, tmp_path):
    path = _write(tmp_path, "device.csv", "timestamp,A\n0,1\n100,2\n200,3\n")
    state_store = StateStore(str(tmp_path / "state.json"))
    state_store.set(os.path.abspath(path), {"last_index": 0, "last_row_ts": 0})

    import publisher.player as player_module

    fake_now = [1000.0]
    monkeypatch.setattr(player_module.time, "monotonic", lambda: fake_now[0])

    listener = RecordingListener()
    player = FilePlayer([("Org/Device", path, 2.0)], [listener], state_store=state_store, tick_interval=0.01)
    entry = player._entries[0]

    # First call always publishes: the sentinel last_index (-1) differs from the resumed row 0.
    first = player._maybe_advance(entry)
    assert first is not None and first["metrics"] == {"A": 1}

    # No wall-clock movement yet -> same row -> no publish.
    assert player._maybe_advance(entry) is None

    # 60ms real time * 2.0 speed = 120ms virtual time -> row at ts=100 ("A": 2).
    fake_now[0] = 1000.06
    second = player._maybe_advance(entry)
    assert second is not None and second["metrics"] == {"A": 2}

    # 110ms real time * 2.0 speed = 220ms virtual time; span is 200ms so it wraps to 20ms -> back to row 0.
    fake_now[0] = 1000.11
    third = player._maybe_advance(entry)
    assert third is not None and third["metrics"] == {"A": 1}


def test_sparse_columns_are_dropped_from_published_metrics(tmp_path):
    path = _write(tmp_path, "device.csv", "timestamp,A,B\n100,1,\n")
    listener = RecordingListener()
    player = FilePlayer([("Org/Device", path, 1.0)], [listener], tick_interval=0.01)

    asyncio.run(_run_briefly(player, 0.03))

    assert listener.events[0]["metrics"] == {"A": 1}


def test_a_slow_listener_does_not_block_playback_or_other_listeners(tmp_path):
    # Rows 10ms apart so plenty of row changes happen in a fraction of a second.
    rows = "\n".join(f"{i * 10},{i}" for i in range(21))
    path = _write(tmp_path, "device.csv", "timestamp,A\n" + rows + "\n")
    fast = RecordingListener()
    slow = SlowListener(delay=5.0)  # far longer than this test's own duration
    player = FilePlayer(
        [("Org/Device", path, 1.0)], [slow, fast], tick_interval=0.01, listener_shutdown_grace_s=0.05
    )

    asyncio.run(_run_briefly(player, 0.3))

    # The fast listener kept getting fresh batches throughout, unblocked by the slow one.
    assert len(fast.events) > 3
    # The slow listener never had two of its own publishes running at once - later
    # ticks skipped dispatching to it while its first batch was still in flight,
    # rather than piling up concurrent calls.
    assert slow.max_concurrent == 1
    assert len(slow.calls) >= 1


def test_one_stuck_device_does_not_starve_another_device_on_the_same_listener(tmp_path):
    # Two devices, one fast-changing (10ms rows) and one that will be "stuck"
    # from the listener's point of view (e.g. an unreachable websocket url).
    # Both share a single listener instance, same as 30 real devices would
    # all share one WebSocketListener.
    fast_rows = "\n".join(f"{i * 10},{i}" for i in range(21))
    fast_path = _write(tmp_path, "fast.csv", "timestamp,A\n" + fast_rows + "\n")
    stuck_path = _write(tmp_path, "stuck.csv", "timestamp,A\n0,1\n")

    shared = PartiallySlowListener(slow_devices={"Org/Stuck"}, delay=5.0)
    player = FilePlayer(
        [("Org/Fast", fast_path, 1.0), ("Org/Stuck", stuck_path, 1.0)],
        [shared],
        tick_interval=0.01,
        listener_shutdown_grace_s=0.05,
    )

    asyncio.run(_run_briefly(player, 0.3))

    fast_events = [e for e in shared.events if e["device"] == "Org/Fast"]
    stuck_attempts = [d for d in shared.attempts if d == "Org/Stuck"]
    # The fast device kept being published the whole time, unblocked by the stuck one.
    assert len(fast_events) > 3
    # The stuck device was dispatched exactly once - still in flight (5s delay, 0.3s
    # test) - and was correctly skipped on later ticks rather than piling up.
    assert len(stuck_attempts) == 1


def test_shutdown_does_not_wait_out_a_stuck_listener(tmp_path):
    path = _write(tmp_path, "device.csv", "timestamp,A\n100,1\n")
    slow = SlowListener(delay=5.0)
    player = FilePlayer([("Org/Device", path, 1.0)], [slow], tick_interval=0.01, listener_shutdown_grace_s=0.05)

    start = time.monotonic()
    asyncio.run(_run_briefly(player, 0.02))
    elapsed = time.monotonic() - start

    # Shutdown is bounded by listener_shutdown_grace_s, not by the listener's own delay.
    assert elapsed < 1.0
