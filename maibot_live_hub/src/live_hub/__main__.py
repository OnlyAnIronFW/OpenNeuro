"""CLI entrypoint for the standalone live hub."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import argparse
import asyncio

from .config import DEFAULT_CONFIG_PATH, load_live_hub_settings
from .service import ConsoleHubLogger, LiveHubService


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Standalone Bilibili live hub for MaiBot multi-instance experiments.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Hub config path. Default: {DEFAULT_CONFIG_PATH}",
    )
    parser.add_argument(
        "--room-id",
        type=int,
        default=0,
        help="Optional room_id override.",
    )
    parser.add_argument(
        "--listen-host",
        type=str,
        default="",
        help="Optional listen host override.",
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        default=0,
        help="Optional listen port override.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print captured Bilibili events to the console.",
    )
    return parser.parse_args(argv)


async def _run_until_cancelled(service: LiveHubService) -> None:
    await service.start()
    try:
        while True:
            await asyncio.sleep(3600.0)
    finally:
        await service.stop()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_live_hub_settings(args.config).with_runtime_overrides(
        room_id=args.room_id,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
    )
    logger = ConsoleHubLogger(verbose=bool(args.verbose))
    service = LiveHubService(settings, logger=logger)
    logger.info(
        "Using source adapter config: "
        f"{settings.source_adapter_config} room_id={settings.bilibili.room_id} "
        f"listen=http://{settings.hub.listen_host}:{settings.hub.listen_port}"
    )
    try:
        asyncio.run(_run_until_cancelled(service))
    except KeyboardInterrupt:
        logger.info("Hub stopped by Ctrl+C")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
