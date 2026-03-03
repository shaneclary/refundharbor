# market_data.py — market liquidity and depth awareness
#
# Fetches and caches market-level data from Polymarket's Gamma API.
# Used by position_manager to gate trades on liquidity and cap sizing
# to a safe fraction of market volume.
#
# Polymarket Gamma API: https://gamma-api.polymarket.com
#   GET /markets/{conditionId}  → volume_24h, liquidity, best_bid, best_ask
#
# Cache: SQLite market_cache table, refreshed every CACHE_TTL_SECONDS.

from __future__ import annotations

import logging
from typing import Optional

import httpx

from db import get_market_cache, get_market_cache_age_seconds, upsert_market_cache

log = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"

# How long to trust cached market data before re-fetching (seconds)
CACHE_TTL_SECONDS = 300  # 5 minutes

# ── Liquidity thresholds ──────────────────────────────────────────────────────
# These scale with balance. At higher balances, you need deeper markets.
#
# Format: (min_balance, min_volume_24h, min_liquidity, max_volume_pct)
#   min_volume_24h: skip markets with less daily volume than this
#   min_liquidity:  skip markets with less total liquidity than this
#   max_volume_pct: never place more than this % of 24h volume in one trade
#
# At small balances ($10 trades) any market works.
# At $1k+ trades you need to respect the order book.
LIQUIDITY_TIERS = [
    #  min_bal   min_vol_24h  min_liq   max_vol_pct
    (500000,    500000,      100000,    0.005),   # $500k+: need $500k/day vol, cap at 0.5% of vol
    (250000,    200000,       50000,    0.008),   # $250k+: need $200k/day
    (100000,    100000,       25000,    0.01),    # $100k+: need $100k/day, cap at 1%
    (50000,      50000,       10000,    0.02),    # $50k+:  need $50k/day
    (25000,      25000,        5000,    0.03),    # $25k+:  need $25k/day, cap at 3%
    (10000,      10000,        2000,    0.05),    # $10k+:  need $10k/day, cap at 5%
    (5000,        5000,        1000,    0.10),    # $5k+:   need $5k/day, cap at 10%
    # Below $5k: no liquidity gate (trade sizes are small enough)
]


def get_liquidity_requirements(balance: float) -> Optional[dict]:
    """
    Get the minimum liquidity requirements for the given balance.
    Returns None if no liquidity gate applies (small balance).
    """
    for min_bal, min_vol, min_liq, max_pct in LIQUIDITY_TIERS:
        if balance >= min_bal:
            return {
                "min_volume_24h": min_vol,
                "min_liquidity": min_liq,
                "max_volume_pct": max_pct,
            }
    return None  # Small balance — no gate


# ── Fetcher ───────────────────────────────────────────────────────────────────


async def fetch_market_data(market_id: str) -> Optional[dict]:
    """
    Fetch market data from Polymarket Gamma API and cache it.

    Returns cached data if fresh enough, otherwise fetches from API.
    Returns None if the fetch fails and no cache exists.
    """
    # Check cache freshness
    age = get_market_cache_age_seconds(market_id)
    if age is not None and age < CACHE_TTL_SECONDS:
        return get_market_cache(market_id)

    # Fetch fresh data
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{GAMMA_API}/markets/{market_id}")

            if resp.status_code != 200:
                log.debug("Gamma API returned %d for market %s", resp.status_code, market_id[:12])
                # Fall back to stale cache
                return get_market_cache(market_id)

            data = resp.json()

            volume_24h = _safe_float(data.get("volume24hr", 0))
            volume_total = _safe_float(data.get("volume", 0))
            liquidity = _safe_float(data.get("liquidityClob", 0)) or _safe_float(data.get("liquidity", 0))
            best_bid = _safe_float(data.get("bestBid", 0))
            best_ask = _safe_float(data.get("bestAsk", 0))
            spread = best_ask - best_bid if best_ask and best_bid else 0
            last_price = _safe_float(data.get("lastTradePrice", 0))
            active = data.get("active", True)

            upsert_market_cache(
                market_id=market_id,
                volume_24h=volume_24h,
                volume_total=volume_total,
                liquidity=liquidity,
                best_bid=best_bid,
                best_ask=best_ask,
                spread=spread,
                last_trade_price=last_price,
                active=bool(active),
            )

            log.debug(
                "Market %s: vol24h=$%.0f liq=$%.0f spread=%.4f",
                market_id[:12], volume_24h, liquidity, spread,
            )

            return get_market_cache(market_id)

    except httpx.TimeoutException:
        log.debug("Timeout fetching market data for %s", market_id[:12])
    except Exception as e:
        log.debug("Error fetching market data for %s: %s", market_id[:12], e)

    # Fall back to stale cache
    return get_market_cache(market_id)


def check_market_liquidity(market: Optional[dict], balance: float, trade_amount: float) -> tuple[bool, str, float]:
    """
    Check if a market has enough liquidity for a given trade.

    Args:
        market: Cached market data dict (or None if no data available)
        balance: Current account balance
        trade_amount: Proposed trade size in USDC

    Returns:
        (ok, reason, adjusted_amount)
        - ok: whether the trade should proceed
        - reason: human-readable explanation if rejected
        - adjusted_amount: the trade amount (possibly capped to volume %)
    """
    reqs = get_liquidity_requirements(balance)

    # No liquidity gate for small balances
    if reqs is None:
        return True, "ok", trade_amount

    # No market data available — allow but log warning
    if market is None:
        log.warning("No market data available — allowing trade (balance=$%.0f)", balance)
        return True, "no_data", trade_amount

    vol_24h = market.get("volume_24h", 0) or 0
    liq = market.get("liquidity", 0) or 0

    # Gate 1: minimum 24h volume
    if vol_24h < reqs["min_volume_24h"]:
        return False, (
            f"Market too thin: ${vol_24h:,.0f} 24h volume "
            f"(need ${reqs['min_volume_24h']:,.0f} at ${balance:,.0f} balance)"
        ), 0

    # Gate 2: minimum liquidity
    if liq < reqs["min_liquidity"]:
        return False, (
            f"Market illiquid: ${liq:,.0f} liquidity "
            f"(need ${reqs['min_liquidity']:,.0f} at ${balance:,.0f} balance)"
        ), 0

    # Gate 3: cap trade to max % of 24h volume
    max_from_volume = vol_24h * reqs["max_volume_pct"]
    if trade_amount > max_from_volume:
        log.info(
            "Capping trade from $%.2f to $%.2f (%.1f%% of $%.0f 24h vol)",
            trade_amount, max_from_volume, reqs["max_volume_pct"] * 100, vol_24h,
        )
        trade_amount = max_from_volume

    return True, "ok", trade_amount


def _safe_float(val) -> float:
    """Safely convert a value to float."""
    try:
        return float(val) if val else 0.0
    except (ValueError, TypeError):
        return 0.0
