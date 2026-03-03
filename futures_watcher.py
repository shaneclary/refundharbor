# futures_watcher.py — monitor Hyperliquid wallets for BTC-PERP trades
#
# Polls Hyperliquid info API for recent fills by tracked wallets.
# Emits FuturesTradeSignal events into a queue for processing.
#
# API: POST https://api.hyperliquid.xyz/info
#      Body: {"type": "userFills", "user": "0x..."}
#
# Response fields:
#   coin        — "BTC" for BTC-PERP
#   px          — fill price
#   sz          — fill size (BTC amount)
#   side        — "B" (buy/long) | "A" (ask/short)
#   time        — timestamp in ms
#   hash        — unique transaction hash for dedup
#   dir         — direction ("Open Long" | "Close Long" | "Open Short" | "Close Short")
#   crossed     — whether it crossed the spread
#   closedPnl   — realized PnL on close (if applicable)
#   oid         — order ID
#   startPosition — position size before fill
#   leverage    — leverage used

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from db import (
    get_futures_tracked_wallets,
    is_futures_trade_processed,
    mark_futures_trade_processed,
)

log = logging.getLogger(__name__)

# Hyperliquid API endpoint (no auth needed for public data)
HYPERLIQUID_INFO_API = "https://api.hyperliquid.xyz/info"

# Poll interval in seconds
FUTURES_POLL_INTERVAL = 10


@dataclass
class FuturesTradeSignal:
    """A futures trade signal from a watched Hyperliquid wallet."""

    trader_wallet: str
    symbol: str  # "BTC"
    side: str  # "LONG" | "SHORT" | "CLOSE_LONG" | "CLOSE_SHORT"
    size: float  # BTC amount
    price: float
    leverage: float
    timestamp: int
    tx_hash: str
    closed_pnl: float = 0.0  # Realized PnL on close


async def start_futures_watchers(queue: asyncio.Queue[FuturesTradeSignal]) -> None:
    """
    Main futures watcher loop.
    Polls all tracked Hyperliquid wallets and emits trade signals.
    Re-reads wallet list each cycle so additions/removals take effect without restart.
    """
    log.info("🔮 Futures watcher started (poll interval: %ds)", FUTURES_POLL_INTERVAL)

    while True:
        wallets = get_futures_tracked_wallets()
        if not wallets:
            log.debug("No futures wallets configured — waiting for wallets to be added...")
            await asyncio.sleep(FUTURES_POLL_INTERVAL)
            continue

        try:
            tasks = [watch_futures_wallet(w["address"], queue) for w in wallets]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            log.error("Futures watcher error: %s", e)

        await asyncio.sleep(FUTURES_POLL_INTERVAL)


async def watch_futures_wallet(wallet: str, queue: asyncio.Queue[FuturesTradeSignal]) -> None:
    """
    Fetch recent fills for a single wallet from Hyperliquid API.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            payload = {
                "type": "userFills",
                "user": wallet,
            }

            resp = await client.post(
                HYPERLIQUID_INFO_API,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code != 200:
                log.warning("Failed to fetch fills for %s: HTTP %d", wallet[:10], resp.status_code)
                return

            fills = resp.json()
            if not isinstance(fills, list):
                log.debug("Unexpected response format for %s: %s", wallet[:10], type(fills))
                return

            # Process only BTC fills (we're focused on BTC-PERP)
            for fill in fills:
                if fill.get("coin") == "BTC":
                    await _process_futures_fill(wallet, fill, queue)

    except httpx.TimeoutException:
        log.warning("Timeout fetching futures fills for %s", wallet[:10])
    except Exception as e:
        log.error("Error watching futures wallet %s: %s", wallet[:10], e)


async def _process_futures_fill(
    wallet: str, fill: dict, queue: asyncio.Queue[FuturesTradeSignal]
) -> None:
    """
    Process a single fill from Hyperliquid API.
    Emit a FuturesTradeSignal if it's new.
    """
    try:
        # Parse fields
        tx_hash = fill.get("hash", "")
        if not tx_hash:
            log.debug("Skipping fill without hash: %s", fill)
            return

        # Deduplication
        if is_futures_trade_processed(wallet, tx_hash):
            return

        # Parse fill data
        coin = fill.get("coin", "")  # Should be "BTC"
        raw_side = fill.get("side", "")  # "B" = buy, "A" = sell/ask
        direction = fill.get("dir", "")  # "Open Long", "Close Long", "Open Short", "Close Short"

        try:
            price = float(fill.get("px", 0))
        except (ValueError, TypeError):
            price = 0.0

        try:
            size = float(fill.get("sz", 0))
        except (ValueError, TypeError):
            size = 0.0

        try:
            timestamp = int(fill.get("time", 0))
        except (ValueError, TypeError):
            timestamp = 0

        try:
            leverage = float(fill.get("leverage", {}).get("value", 1)) if isinstance(fill.get("leverage"), dict) else float(fill.get("leverage", 1))
        except (ValueError, TypeError):
            leverage = 1.0

        try:
            closed_pnl = float(fill.get("closedPnl", 0))
        except (ValueError, TypeError):
            closed_pnl = 0.0

        # Validate
        if price <= 0 or size <= 0:
            log.debug("Skipping invalid fill: price=%.2f size=%.4f", price, size)
            return

        # Determine normalized side
        side = _normalize_side(direction, raw_side)
        if not side:
            log.debug("Could not determine side for fill: dir=%s raw_side=%s", direction, raw_side)
            return

        # Mark as processed
        mark_futures_trade_processed(wallet, tx_hash)

        # Emit signal
        signal = FuturesTradeSignal(
            trader_wallet=wallet,
            symbol=coin,
            side=side,
            size=size,
            price=price,
            leverage=leverage,
            timestamp=timestamp,
            tx_hash=tx_hash,
            closed_pnl=closed_pnl,
        )

        log.info(
            "🔮 FUTURES %s %s %.4f BTC @ $%.2f (%.1fx) | %s | %s",
            wallet[:10],
            side,
            size,
            price,
            leverage,
            f"PnL: ${closed_pnl:.2f}" if closed_pnl else "",
            tx_hash[:12],
        )

        await queue.put(signal)

    except Exception as e:
        log.warning("Failed to parse futures fill for %s: %s", wallet[:10], e)


def _normalize_side(direction: str, raw_side: str) -> Optional[str]:
    """
    Normalize Hyperliquid side/direction to our internal format.

    Hyperliquid dir values:
        "Open Long" / "Close Long" / "Open Short" / "Close Short"

    Returns: "LONG" | "SHORT" | "CLOSE_LONG" | "CLOSE_SHORT" | None
    """
    direction_lower = direction.lower() if direction else ""

    if "open long" in direction_lower:
        return "LONG"
    elif "close long" in direction_lower:
        return "CLOSE_LONG"
    elif "open short" in direction_lower:
        return "SHORT"
    elif "close short" in direction_lower:
        return "CLOSE_SHORT"

    # Fallback to raw side if direction not available
    if raw_side == "B":
        return "LONG"
    elif raw_side == "A":
        return "SHORT"

    return None


async def get_btc_mark_price() -> Optional[float]:
    """
    Fetch current BTC mark price from Hyperliquid.
    Used for P&L calculations.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            payload = {
                "type": "allMids",
            }

            resp = await client.post(
                HYPERLIQUID_INFO_API,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code == 200:
                mids = resp.json()
                if isinstance(mids, dict) and "BTC" in mids:
                    return float(mids["BTC"])

    except Exception as e:
        log.debug("Failed to fetch BTC mark price: %s", e)

    return None
