from __future__ import annotations

from typing import Any, Dict

from .listeners import EventListener
from .subscription_hub import SubscriptionHub


class HubListener(EventListener):
    """Publishes events directly, in-process, to a SubscriptionHub.

    Used when the player itself is also serving the browsing/subscribe API
    (see api_server.py and cli.py's --serve-api) - there's no network
    round trip, this just hands each event straight to the hub that the
    API's /ws/subscribe route reads from.
    """

    def __init__(self, hub: SubscriptionHub):
        self._hub = hub

    async def publish(self, event: Dict[str, Any]) -> None:
        await self._hub.publish(event)
