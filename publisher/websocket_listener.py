from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict

from websockets.asyncio import client
from websockets.exceptions import ConnectionClosed

from .listeners import EventListener

logger = logging.getLogger(__name__)


class WebSocketListener(EventListener):
    """Publishes events to a websocket server, one connection per device.

    Only a single root url is configured (there is nowhere else this
    listener will ever publish to). Each event's device path is appended to
    that root to get the device's own endpoint, e.g.
    root_url="ws://host:1880/ingest" + device="Plant/Line1/Press1" ->
    "ws://host:1880/ingest/Plant/Line1/Press1". A connection is opened
    lazily the first time a device is published and reused after that, so
    many devices can be streamed concurrently to the same server.

    On a failed connect, or a connection that drops mid-send, the
    connection is discarded and this call waits retry_delay_s before
    returning. There is no retry loop here - the next time publish() is
    called for that device (i.e. the next time the file player has a new
    value for it), a fresh connection is attempted.
    """

    def __init__(self, root_url: str, retry_delay_s: float = 5.0):
        if not (root_url.startswith("ws://") or root_url.startswith("wss://")):
            raise ValueError("Websocket root url must start with ws:// or wss://")
        self._root_url = root_url if root_url.endswith("/") else f"{root_url}/"
        self._retry_delay_s = retry_delay_s
        self._conns: Dict[str, Any] = {}  # device url -> open connection

    async def stop(self) -> None:
        for conn in self._conns.values():
            await conn.close()
        self._conns.clear()

    async def publish(self, event: Dict[str, Any]) -> bool:
        url = f"{self._root_url}{event['device']}"

        conn = self._conns.get(url)
        if conn is None:
            try:
                logger.debug("connecting to %s", url)
                conn = await client.connect(url)
                self._conns[url] = conn
                logger.info("connected to %s", url)
            except Exception:
                logger.exception("failed to connect to %s", url)
                await asyncio.sleep(self._retry_delay_s)
                return False

        try:
            logger.debug("publishing to %s: %s", url, event)
            await conn.send(json.dumps(event, default=str))
        except ConnectionClosed:
            logger.exception("connection to %s closed while publishing", url)
            self._conns.pop(url, None)
            await asyncio.sleep(self._retry_delay_s)
            return False
        except Exception:
            logger.exception("failed to publish to %s", url)
            return False
        return True
