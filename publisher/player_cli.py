from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys

from .asset_browser import AssetBrowser
from .listeners import EventListener, StdoutListener
from .player import FileSpec, FilePlayer
from .playlist import load_speed_overrides
from .state_store import StateStore

logger = logging.getLogger("fileplayer")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="General purpose timeseries CSV file player")
    parser.add_argument(
        "--root",
        required=True,
        help="Root directory of the asset hierarchy (organization/division/plant/line/device.csv)",
    )
    parser.add_argument(
        "--default-speed",
        type=float,
        default=1.0,
        help="Playback speed applied to every discovered device that has no playlist override (default 1.0)",
    )
    parser.add_argument(
        "--playlist",
        help="Optional CSV file with 'device,speed' rows overriding the speed of specific devices",
    )
    parser.add_argument(
        "--state-file",
        default="fileplayer_state.json",
        help="Path to the durable playback state file (default ./fileplayer_state.json)",
    )
    parser.add_argument(
        "--tick",
        type=float,
        default=0.2,
        help="Main loop tick interval in seconds (default 0.2)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    parser.add_argument(
        "--websocket-url",
        required=True,
        help="Root websocket url to publish to, e.g. ws://host:1880/ingest - each device's "
        "hierarchical path is appended to it to form that device's own endpoint",
    )
    parser.add_argument(
        "--websocket-retry-delay",
        type=float,
        default=5.0,
        help="Seconds to wait after a failed websocket connect/send before it's retried (default 5.0)",
    )
    parser.add_argument(
        "--no-stdout",
        action="store_true",
        help="Don't log events at DEBUG level (has no effect unless --log-level DEBUG)",
    )
    parser.add_argument(
        "--log-dir",
        help="If set, also write logs to a daily-rotating file in this directory (kept for 3 days)",
    )
    parser.add_argument(
        "--serve-api",
        action="store_true",
        help="Also serve a browsing API (/api/tree) and a live subscribe websocket (/ws/subscribe) for the "
        "standalone demo frontend in publisher/demo - fed directly, in-process, as the player publishes",
    )
    parser.add_argument("--api-host", default="0.0.0.0", help="Host to bind --serve-api to (default 0.0.0.0)")
    parser.add_argument("--api-port", type=int, default=8000, help="Port to bind --serve-api to (default 8000)")
    return parser


def discover_files(browser: AssetBrowser, default_speed: float, playlist_path: str = None) -> list[FileSpec]:
    overrides = load_speed_overrides(playlist_path) if playlist_path else {}
    files: list[FileSpec] = []
    for node in browser.walk_devices():
        speed = overrides.get(node.path, default_speed)
        files.append((node.path, node.file_path, speed))
    return files


async def _run(args: argparse.Namespace) -> None:
    browser = AssetBrowser(args.root)
    files = discover_files(browser, args.default_speed, args.playlist)
    if not files:
        logger.error("no device csv files found under %s", args.root)
        sys.exit(1)
    logger.info("discovered %d device file(s) under %s", len(files), args.root)

    state_store = StateStore(args.state_file)
    listeners: list[EventListener] = []
    if not args.no_stdout:
        listeners.append(StdoutListener())
    from .websocket_listener import WebSocketListener

    listeners.append(WebSocketListener(args.websocket_url, retry_delay_s=args.websocket_retry_delay))
    logger.info("publishing to %s", args.websocket_url)

    uv_server = None
    if args.serve_api:
        import uvicorn

        from .api_server import create_app
        from .hub_listener import HubListener
        from .subscription_hub import SubscriptionHub

        hub = SubscriptionHub()
        listeners.append(HubListener(hub))
        app = create_app(browser, hub)
        uv_server = uvicorn.Server(uvicorn.Config(app, host=args.api_host, port=args.api_port, log_level=args.log_level.lower()))
        logger.info("serving api on http://%s:%d", args.api_host, args.api_port)

    player = FilePlayer(files, listeners, state_store=state_store, tick_interval=args.tick)

    def _stop(*_args: object) -> None:
        logger.info("stopping file player")
        player.stop()
        if uv_server is not None:
            uv_server.should_exit = True

    for signame in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signame):
            try:
                signal.signal(getattr(signal, signame), _stop)
            except (ValueError, OSError):
                pass  # signal not available on this platform/thread

    print("Press Ctrl+C to terminate")
    if uv_server is not None:
        await asyncio.gather(player.run(), uv_server.serve())
    else:
        await player.run()


def configure_logging(log_level: str, log_dir: str = None) -> None:
    formatter = logging.Formatter("%(asctime)s:%(levelname)s:%(name)s:%(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level))
    root.handlers.clear()

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(log_dir, "fileplayer.log"), when="D", interval=1, backupCount=3
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def main() -> None:
    args = build_arg_parser().parse_args()
    configure_logging(args.log_level, args.log_dir)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
