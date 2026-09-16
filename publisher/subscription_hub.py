from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class SubscriptionHub:
    """Fans out published device events to the browser websockets that asked for them.

    HubListener calls publish() in-process, directly from FilePlayer's main
    loop, for every event as it's produced - there's no network hop between
    the player and the hub. Browser connections register(), then call
    set_subscriptions() with whichever device paths that browser currently
    wants; publish() only forwards an event to browsers subscribed to that
    specific device.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[WebSocket, Set[str]] = {}
        self._latest: Dict[str, Dict[str, Any]] = {}

    def register(self, ws: WebSocket) -> None:
        self._subscribers[ws] = set()

    def unregister(self, ws: WebSocket) -> None:
        self._subscribers.pop(ws, None)

    def set_subscriptions(self, ws: WebSocket, devices: Set[str]) -> None:
        self._subscribers[ws] = devices

    def latest(self, device: str) -> Optional[Dict[str, Any]]:
        return self._latest.get(device)

    async def publish(self, event: Dict[str, Any]) -> None:
        device = event.get("device")
        if not device:
            logger.warning("dropping event with no device field: %r", event)
            return
        self._latest[device] = event
        stale: List[WebSocket] = []
        for ws, devices in self._subscribers.items():
            if device not in devices:
                continue
            try:
                await ws.send_json(event)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._subscribers.pop(ws, None)
