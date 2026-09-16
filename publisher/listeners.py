from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

logger = logging.getLogger(__name__)


class EventListener(ABC):
    """Abstract sink for published playback events.

    Concrete listeners for websocket, OPC UA, MQTT, MQTT Sparkplug B, etc.
    are expected to subclass this. publish() is async so a network-backed
    listener can await I/O without blocking the player's main loop.
    """

    async def start(self) -> None:
        """Called once before the player begins publishing. Optional to override."""

    async def stop(self) -> None:
        """Called once when the player shuts down. Optional to override."""

    @abstractmethod
    async def publish(self, event: Dict[str, Any]) -> None:
        ...


class StdoutListener(EventListener):
    """Logs each event as a JSON line at DEBUG level. For testing/diagnostics.

    Routed through logging rather than a raw print, so it's silent at the
    default INFO level (there can be one of these per device per tick,
    across many devices - far too noisy to leave on by default) and only
    shows up when --log-level DEBUG is set. Also means it's captured by
    whatever handlers are configured - console, a rotating file, etc.
    """

    async def publish(self, event: Dict[str, Any]) -> None:
        logger.debug(json.dumps(event, default=str))
