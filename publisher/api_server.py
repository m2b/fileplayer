from __future__ import annotations

import json
import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .asset_browser import AssetBrowser
from .subscription_hub import SubscriptionHub

logger = logging.getLogger(__name__)


def create_app(browser: AssetBrowser, hub: SubscriptionHub) -> FastAPI:
    """Builds the player's own browsing + live-subscribe API.

    This is meant to run inside the player process itself (see cli.py's
    --serve-api): browser is the same AssetBrowser the player used to
    discover its files, and hub is fed directly by HubListener as the
    player publishes, so there's no separate process or network hop
    between "the data" and "the API that serves it". CORS is wide open
    since this is meant to be hit by the standalone demo frontend running
    on its own origin (or opened straight off disk).
    """
    app = FastAPI(title="File Player API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/tree")
    async def get_tree(path: str = ""):
        return [{"name": c.name, "path": c.path, "is_device": c.is_device} for c in browser.get_children(path)]

    @app.get("/api/devices/{device_path:path}/latest")
    async def get_latest(device_path: str):
        return hub.latest(device_path) or {}

    @app.websocket("/ws/subscribe")
    async def subscribe(websocket: WebSocket):
        await websocket.accept()
        hub.register(websocket)
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                devices = set(msg.get("devices", []))
                hub.set_subscriptions(websocket, devices)
                for device in devices:
                    cached = hub.latest(device)
                    if cached is not None:
                        await websocket.send_json(cached)
        except WebSocketDisconnect:
            pass
        finally:
            hub.unregister(websocket)

    return app
