# watcher.py — monitor target wallets for new trades
#
# Polls Polymarket data-api for recent trades by tracked wallets.
# Emits TradeSignal events into a queue for processing.
#
# Real API: https://data-api.polymarket.com/trades?user=0x...&limit=20
#
# Response fields:
#   proxyWallet     — trader's proxy wallet address
#   side            — "BUY" | "SELL" (already uppercase)
#   asset           — token ID (large integer string)
#   conditionId     — market condition ID (0x...)
#   size            — number of shares
#   price           — price per share (0.00 - 1.00)
#   timestamp       — unix timestamp
#   title           — market question text
#   outcome         — outcome name ("Yes", "No", etc.)
#   transactionHash — unique tx hash (perfect dedup key)

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

import os
import time

from config import POLL_INTERVAL, POLYMARKET_DATA_API, get_active_wallets

# Max age for 5-minute markets (seconds) - trades older than this are skipped
FAST_MARKET_MAX_AGE = int(os.getenv("FAST_MARKET_MAX_AGE", "120"))  # 2 minutes
from db import is_trade_processed, mark_trade_processed

log = logging.getLogger(__name__)


@dataclass
class TradeSignal:
    """A trade signal from a watched wallet."""

    trader_wallet: str
    market_id: str
    token_id: str
    side: str  # "BUY" | "SELL"
    shares: Optional[float] = None
    price: Optional[float] = None
    usdc_value: Optional[float] = None
    timestamp: int = 0
    title: str = ""
    outcome: str = ""
    tx_hash: str = ""


async def start_watchers(queue: asyncio.Queue[TradeSignal]) -> None:
    """
    Main watcher loop.
    Polls all target wallets and emits trade signals.
    Re-reads wallet list each cycle so additions/removals take effect without restart.
    """
    log.info("👀 Watcher started (poll interval: %ds)", POLL_INTERVAL)

    while True:
        wallets = get_active_wallets()
        if not wallets:
            log.debug("No wallets configured — waiting for wallets to be added...")
            await asyncio.sleep(POLL_INTERVAL)
            continue

        try:
            tasks = [watch_wallet(wallet, queue) for wallet in wallets]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            log.error("Watcher error: %s", e)

        await asyncio.sleep(POLL_INTERVAL)


async def watch_wallet(wallet: str, queue: asyncio.Queue[TradeSignal]) -> None:
    """
    Fetch recent trades for a single wallet from Polymarket data API.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"{POLYMARKET_DATA_API}/trades"
            params = {
                "user": wallet.lower(),
                "limit": 20,
            }

            resp = await client.get(url, params=params)
            if resp.status_code != 200:
                log.warning("Failed to fetch trades for %s: HTTP %d", wallet[:10], resp.status_code)
                return

            trades = resp.json()
            if not isinstance(trades, list):
                trades = trades.get("data", []) if isinstance(trades, dict) else []

            for trade in trades:
                await _process_trade(wallet, trade, queue)

    except httpx.TimeoutException:
        log.warning("Timeout fetching trades for %s", wallet[:10])
    except Exception as e:
        log.error("Error watching wallet %s: %s", wallet[:10], e)


async def _process_trade(wallet: str, trade: dict, queue: asyncio.Queue[TradeSignal]) -> None:
    """
    Process a single trade from the Polymarket data API.
    Emit a TradeSignal if it's new.
    """
    try:
        # ── Parse fields from real Polymarket data-api response ──
        market_id = trade.get("conditionId", "")
        token_id = trade.get("asset", "")
        side = trade.get("side", "")  # Already uppercase from API
        tx_hash = trade.get("transactionHash", "")
        title = trade.get("title", "")
        outcome = trade.get("outcome", "")

        try:
            shares = float(trade.get("size", 0))
        except (ValueError, TypeError):
            shares = 0.0

        try:
            price = float(trade.get("price", 0))
        except (ValueError, TypeError):
            price = 0.0

        try:
            timestamp = int(trade.get("timestamp", 0))
        except (ValueError, TypeError):
            timestamp = 0

        # Calculate USDC value
        usdc_value = shares * price if shares and price else None

        # ── Validate essential fields ──
        if not market_id or not token_id or side not in ("BUY", "SELL"):
            log.debug("Skipping invalid trade data: %s", trade)
            return

        # ── Staleness check for fast-expiring markets ──
        # 5-minute Bitcoin markets expire quickly - skip if trade is too old
        trade_age_seconds = time.time() - timestamp if timestamp > 0 else 0
        is_fast_market = "Up or Down" in title or "5m" in title.lower()

        if is_fast_market and trade_age_seconds > FAST_MARKET_MAX_AGE:
            log.debug(
                "Skipping stale 5-min market trade (%.0fs old): %s",
                trade_age_seconds, title[:40]
            )
            # Still mark as processed to avoid re-checking
            if tx_hash:
                dedup_key = int(hashlib.sha256(tx_hash.encode()).hexdigest()[:8], 16)
            else:
                dedup_key = timestamp if timestamp > 0 else int(hashlib.sha256(str(sorted(trade.items())).encode()).hexdigest()[:8], 16)
            mark_trade_processed(wallet, market_id, token_id, side, shares, dedup_key)
            return

        # ── Deduplication ──
        # Use transactionHash as primary dedup key (unique per trade)
        # Fall back to (market_id, timestamp) if tx_hash is missing
        # NOTE: must use hashlib (deterministic) — built-in hash() is randomized per-process
        if tx_hash:
            dedup_key = int(hashlib.sha256(tx_hash.encode()).hexdigest()[:8], 16)
        elif timestamp > 0:
            dedup_key = timestamp
        else:
            dedup_key = int(hashlib.sha256(str(sorted(trade.items())).encode()).hexdigest()[:8], 16)

        if is_trade_processed(wallet, market_id, dedup_key):
            return

        mark_trade_processed(wallet, market_id, token_id, side, shares, dedup_key)

        # ── Emit signal ──
        signal = TradeSignal(
            trader_wallet=wallet,
            market_id=market_id,
            token_id=token_id,
            side=side,
            shares=shares,
            price=price,
            usdc_value=usdc_value,
            timestamp=timestamp,
            title=title,
            outcome=outcome,
            tx_hash=tx_hash,
        )

        log.info(
            "🔔 %s %s %.2f shares @ $%.3f | %s [%s] | %s",
            wallet[:10],
            side,
            shares,
            price,
            title[:50] if title else market_id[:12],
            outcome,
            tx_hash[:12] if tx_hash else "no-tx",
        )

        await queue.put(signal)

    except Exception as e:
        log.warning("Failed to parse trade for %s: %s", wallet[:10], e)
