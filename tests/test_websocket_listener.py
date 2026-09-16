import json

import pytest
import websockets
from websockets.exceptions import ConnectionClosed

from publisher.websocket_listener import WebSocketListener

received = []


async def _server_handler(websocket):
    path = websocket.request.path
    try:
        async for message in websocket:
            received.append((path, json.loads(message)))
    except ConnectionClosed:
        pass


class TestWebSocketListener:
    def setup_method(self):
        received.clear()

    def test_rejects_non_websocket_url(self):
        with pytest.raises(ValueError):
            WebSocketListener("http://localhost:9999")

    @pytest.mark.asyncio
    async def test_publishes_to_a_device_specific_url_under_the_root(self):
        port = 8865
        listener = WebSocketListener(f"ws://localhost:{port}/ingest", retry_delay_s=0.01)
        async with websockets.serve(_server_handler, "localhost", port):
            ok1 = await listener.publish({"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100})
            ok2 = await listener.publish({"device": "Org/Boiler1", "metrics": {"B": 2}, "timestamp": 200})
        await listener.stop()

        assert ok1 is True and ok2 is True
        assert ("/ingest/Org/Press1", {"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100}) in received
        assert ("/ingest/Org/Boiler1", {"device": "Org/Boiler1", "metrics": {"B": 2}, "timestamp": 200}) in received

    @pytest.mark.asyncio
    async def test_reuses_one_connection_per_device(self):
        port = 8866
        listener = WebSocketListener(f"ws://localhost:{port}", retry_delay_s=0.01)
        async with websockets.serve(_server_handler, "localhost", port):
            await listener.publish({"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100})
            await listener.publish({"device": "Org/Press1", "metrics": {"A": 2}, "timestamp": 200})
            assert len(listener._conns) == 1
        await listener.stop()
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_publish_fails_then_recovers_once_the_server_is_available(self):
        port = 8867
        listener = WebSocketListener(f"ws://localhost:{port}", retry_delay_s=0.01)

        # no server listening yet - connect attempt should fail without raising
        ok = await listener.publish({"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100})
        assert ok is False

        async with websockets.serve(_server_handler, "localhost", port):
            ok = await listener.publish({"device": "Org/Press1", "metrics": {"A": 2}, "timestamp": 200})
            assert ok is True
        await listener.stop()

        assert received == [("/Org/Press1", {"device": "Org/Press1", "metrics": {"A": 2}, "timestamp": 200})]

    @pytest.mark.asyncio
    async def test_drops_connection_and_recovers_after_server_restart(self):
        port = 8868
        listener = WebSocketListener(f"ws://localhost:{port}", retry_delay_s=0.01)

        async with websockets.serve(_server_handler, "localhost", port):
            ok1 = await listener.publish({"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100})
        # server is now closed - the cached connection is stale
        ok2 = await listener.publish({"device": "Org/Press1", "metrics": {"A": 2}, "timestamp": 200})

        async with websockets.serve(_server_handler, "localhost", port):
            ok3 = await listener.publish({"device": "Org/Press1", "metrics": {"A": 3}, "timestamp": 300})
        await listener.stop()

        assert [ok1, ok2, ok3] == [True, False, True]
        assert [msg for _, msg in received] == [
            {"device": "Org/Press1", "metrics": {"A": 1}, "timestamp": 100},
            {"device": "Org/Press1", "metrics": {"A": 3}, "timestamp": 300},
        ]
