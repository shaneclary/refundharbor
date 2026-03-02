# ws_watcher.py — WebSocket price feed from Polymarket CLOB
#
# Subscribes to real-time market data for tokens we hold positions in.
# Maintains a live price cache that paper_trader and resolver can use
# for more accurate pricing (instead of polling the REST API each time).
#
# Usage:
#   This runs as a background task alongside the main bot.
#   Enable with USE_WEBSOCKET_PRICES=true in .env
#
# Architecture:
#   - Connects to wss://ws-subscriptions-clob.polymarket.com/ws/market
#   - Subscribes to token IDs from open positions
#   - Updates _price_cache with latest midpoint prices
#   - Auto-reconnects with exponential backoff

from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import websockets

from config import POLYMARKET_CLOB
from db import get_all_positions

log = logging.getLogger(__name__)

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Live price cache: token_id → midpoint price
_price_cache: dict[str, float] = {}

# Track subscribed token IDs
_subscribed: set[str] = set()


def get_live_price(token_id: str) -> Optional[float]:
    """Get cached live price for a token. Returns None if not available."""
    return _price_cache.get(token_id)


async def ws_price_feed() -> None:
    """
    WebSocket price feed loop with auto-reconnect.

    Subscribes to market data for all tokens in our open positions.
    Periodically checks for new positions and subscribes to their tokens.
    """
    log.info("WebSocket price feed starting...")
    backoff = 1

    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=30) as ws:
                log.info("WebSocket connected to %s", WS_URL)
                backoff = 1  # Reset backoff on successful connection

                # Subscribe to current positions
                await _subscribe_positions(ws)

                # Periodically re-subscribe to catch new positions
                resub_task = asyncio.create_task(_periodic_resubscribe(ws))

                try:
                    async for message in ws:
                        _handle_message(message)
                finally:
                    resub_task.cancel()

        except (websockets.ConnectionClosed, ConnectionError) as e:
            log.warning("WebSocket disconnected: %s — reconnecting in %ds", e, backoff)
        except Exception as e:
            log.error("WebSocket error: %s — reconnecting in %ds", e, backoff)

        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)  # Exponential backoff, max 60s


async def _subscribe_positions(ws) -> None:
    """Subscribe to market data for all tokens in open positions."""
    positions = get_all_positions()
    token_ids = {pos["token_id"] for pos in positions if pos.get("token_id")}

    new_tokens = token_ids - _subscribed
    if not new_tokens:
        return

    # Subscribe in batches (API may have limits)
    for token_id in new_tokens:
        msg = json.dumps({
            "type": "market",
            "assets_ids": [token_id],
        })
        await ws.send(msg)
        _subscribed.add(token_id)

    if new_tokens:
        log.info("WebSocket subscribed to %d token(s) (%d total)", len(new_tokens), len(_subscribed))


async def _periodic_resubscribe(ws) -> None:
    """Check for new positions every 30s and subscribe to their tokens."""
    while True:
        await asyncio.sleep(30)
        try:
            await _subscribe_positions(ws)
        except Exception as e:
            log.debug("Resubscribe failed: %s", e)


def _handle_message(raw: str) -> None:
    """Process incoming WebSocket market data message."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    msg_type = data.get("type", "")

    if msg_type == "book":
        # Full orderbook snapshot
        _update_from_book(data)

    elif msg_type == "price_change":
        # Incremental price update
        _update_from_price_change(data)


def _update_from_book(data: dict) -> None:
    """Extract midpoint from orderbook snapshot."""
    asset_id = data.get("asset_id", "")
    if not asset_id:
        return

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    best_bid = float(bids[0]["price"]) if bids else 0
    best_ask = float(asks[0]["price"]) if asks else 0

    if best_bid > 0 and best_ask > 0:
        midpoint = (best_bid + best_ask) / 2
        _price_cache[asset_id] = midpoint


def _update_from_price_change(data: dict) -> None:
    """Extract price from incremental update."""
    changes = data.get("changes", [])
    for change in changes:
        asset_id = change.get("asset_id", "")
        price = change.get("price")
        if asset_id and price is not None:
            _price_cache[asset_id] = float(price)
