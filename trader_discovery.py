# trader_discovery.py — Polymarket leaderboard and trader analysis
#
# Fetches trader data from Polymarket's data API for discovery and analysis.
#
# Usage:
#   from trader_discovery import fetch_leaderboard, analyze_trader
#   traders = await fetch_leaderboard(period="7d", limit=100)
#   analysis = await analyze_trader("0x123...")

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import httpx

from config import POLYMARKET_DATA_API

log = logging.getLogger(__name__)

# Cache settings
LEADERBOARD_CACHE_SECONDS = 300  # 5 minutes
TRADER_CACHE_SECONDS = 3600  # 1 hour


async def fetch_leaderboard(
    period: str = "7d",
    limit: int = 50,
    offset: int = 0,
    category: str = "OVERALL",
    order_by: str = "PNL",
) -> list[dict]:
    """
    Fetch top traders from Polymarket leaderboard.

    Args:
        period: Time period - "24h", "7d", "30d", "all"
        limit: Number of traders to fetch (max 50 per request)
        offset: Pagination offset (max 1000)
        category: Market category - OVERALL, POLITICS, SPORTS, CRYPTO, CULTURE, etc.
        order_by: Sort by PNL or VOL

    Returns:
        List of trader dicts with wallet, username, pnl, volume, etc.
    """
    # Map period to API format (DAY, WEEK, MONTH, ALL)
    period_map = {
        "24h": "DAY",
        "1d": "DAY",
        "7d": "WEEK",
        "30d": "MONTH",
        "all": "ALL",
    }
    api_period = period_map.get(period, "WEEK")

    url = f"{POLYMARKET_DATA_API}/v1/leaderboard"
    params = {
        "timePeriod": api_period,
        "limit": min(limit, 50),
        "offset": min(offset, 1000),
        "category": category.upper(),
        "orderBy": order_by.upper(),
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        traders = []
        entries = data if isinstance(data, list) else data.get("leaderboard", data.get("data", []))
        for entry in entries:
            traders.append({
                "wallet_address": entry.get("proxyWallet", entry.get("address", entry.get("wallet", ""))),
                "username": entry.get("userName", entry.get("username", entry.get("name", ""))),
                "pnl": float(entry.get("pnl", entry.get("profit", 0))),
                "volume": float(entry.get("vol", entry.get("volume", 0))),
                "win_count": int(entry.get("wins", entry.get("winCount", 0))),
                "loss_count": int(entry.get("losses", entry.get("lossCount", 0))),
                "rank": entry.get("rank", 0),
                "markets_traded": entry.get("marketsTraded", entry.get("numMarkets", 0)),
                "profile_image": entry.get("profileImage", ""),
                "x_username": entry.get("xUsername", ""),
                "verified": entry.get("verifiedBadge", False),
            })

        # Cache results
        _cache_traders(traders)

        return traders

    except httpx.HTTPError as e:
        log.error(f"Failed to fetch leaderboard: {e}")
        return []
    except Exception as e:
        log.error(f"Error parsing leaderboard data: {e}")
        return []


async def fetch_trader_history(
    wallet: str,
    hours: int = 720,
) -> list[dict]:
    """
    Fetch trade history for a specific trader.

    Args:
        wallet: Wallet address
        hours: How many hours of history to fetch (default 30 days)

    Returns:
        List of trade dicts ordered by timestamp DESC
    """
    url = f"{POLYMARKET_DATA_API}/trades"
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=hours)

    params = {
        "user": wallet.lower(),
        "startTs": int(start_time.timestamp()),
        "endTs": int(end_time.timestamp()),
        "limit": 500,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

        trades = []
        trade_list = data if isinstance(data, list) else data.get("trades", [])

        for trade in trade_list:
            trades.append({
                "market_id": trade.get("market", trade.get("conditionId", "")),
                "side": trade.get("side", "").upper(),
                "outcome": trade.get("outcome", trade.get("asset", "")),
                "price": float(trade.get("price", 0)),
                "shares": float(trade.get("size", trade.get("amount", 0))),
                "usdc_amount": float(trade.get("usdcSize", trade.get("value", 0))),
                "timestamp": trade.get("timestamp", trade.get("createdAt", "")),
                "tx_hash": trade.get("transactionHash", trade.get("hash", "")),
            })

        return trades

    except httpx.HTTPError as e:
        log.error(f"Failed to fetch trader history for {wallet}: {e}")
        return []
    except Exception as e:
        log.error(f"Error parsing trader history: {e}")
        return []


async def analyze_trader(wallet: str) -> dict:
    """
    Analyze a trader's performance metrics.

    Args:
        wallet: Wallet address

    Returns:
        Analysis dict with win_rate, avg_hold_time, pnl, metrics, etc.
    """
    # First try to get from cache
    from db import get_trader_cache, get_trader_cache_age_seconds

    cached = get_trader_cache(wallet)
    cache_age = get_trader_cache_age_seconds(wallet)

    # Use cache if fresh enough
    if cached and cache_age is not None and cache_age < TRADER_CACHE_SECONDS:
        win_count = cached.get("win_count", 0)
        loss_count = cached.get("loss_count", 0)
        total = win_count + loss_count
        return {
            "wallet_address": wallet,
            "username": cached.get("username", ""),
            "pnl": cached.get("pnl", 0),
            "volume": cached.get("volume", 0),
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": (win_count / total * 100) if total > 0 else 0,
            "total_trades": total,
            "from_cache": True,
        }

    # Fetch fresh data
    trades = await fetch_trader_history(wallet, hours=720)

    if not trades:
        return {
            "wallet_address": wallet,
            "error": "No trade history found",
        }

    # Calculate metrics
    buys = [t for t in trades if t["side"] == "BUY"]
    sells = [t for t in trades if t["side"] == "SELL"]

    total_spent = sum(t["usdc_amount"] for t in buys)
    total_received = sum(t["usdc_amount"] for t in sells)

    # Estimate wins/losses based on profitable exits
    # This is a simplified heuristic - real analysis would need market resolution data
    wins = 0
    losses = 0
    realized_pnl = 0

    # Group trades by market
    by_market: dict[str, list[dict]] = {}
    for trade in trades:
        market = trade["market_id"]
        if market not in by_market:
            by_market[market] = []
        by_market[market].append(trade)

    for market_id, market_trades in by_market.items():
        market_buys = sum(t["usdc_amount"] for t in market_trades if t["side"] == "BUY")
        market_sells = sum(t["usdc_amount"] for t in market_trades if t["side"] == "SELL")

        if market_sells > 0:
            market_pnl = market_sells - market_buys
            realized_pnl += market_pnl
            if market_pnl > 0:
                wins += 1
            else:
                losses += 1

    total_resolved = wins + losses
    win_rate = (wins / total_resolved * 100) if total_resolved > 0 else 0

    # Calculate average hold time
    avg_hold_hours = 0
    if buys and sells:
        # Simple estimate based on time between first buy and last sell
        buy_times = [_parse_timestamp(t["timestamp"]) for t in buys if t["timestamp"]]
        sell_times = [_parse_timestamp(t["timestamp"]) for t in sells if t["timestamp"]]
        if buy_times and sell_times:
            first_buy = min(buy_times)
            last_sell = max(sell_times)
            if last_sell > first_buy:
                avg_hold_hours = (last_sell - first_buy).total_seconds() / 3600 / max(len(buys), 1)

    analysis = {
        "wallet_address": wallet,
        "username": "",
        "pnl": realized_pnl,
        "volume": total_spent + total_received,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(win_rate, 1),
        "total_trades": len(trades),
        "total_buys": len(buys),
        "total_sells": len(sells),
        "total_spent": round(total_spent, 2),
        "total_received": round(total_received, 2),
        "markets_traded": len(by_market),
        "avg_hold_hours": round(avg_hold_hours, 1),
        "from_cache": False,
    }

    # Cache the result
    from db import upsert_trader_cache
    upsert_trader_cache(
        wallet_address=wallet,
        username="",
        pnl=realized_pnl,
        volume=total_spent + total_received,
        win_count=wins,
        loss_count=losses,
    )

    return analysis


async def get_trader_profile(wallet: str) -> dict:
    """
    Get a trader's public profile from Polymarket.

    Args:
        wallet: Wallet address

    Returns:
        Profile dict with username, avatar, stats, etc.
    """
    url = f"{POLYMARKET_DATA_API}/profiles/{wallet.lower()}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url)
            if response.status_code == 404:
                return {"wallet_address": wallet, "error": "Profile not found"}
            response.raise_for_status()
            data = response.json()

        return {
            "wallet_address": wallet,
            "username": data.get("username", data.get("name", "")),
            "avatar": data.get("avatar", data.get("profileImage", "")),
            "bio": data.get("bio", ""),
            "twitter": data.get("twitter", ""),
            "pnl": float(data.get("pnl", data.get("profit", 0))),
            "volume": float(data.get("volume", 0)),
            "rank": data.get("rank", 0),
        }

    except httpx.HTTPError as e:
        log.error(f"Failed to fetch profile for {wallet}: {e}")
        return {"wallet_address": wallet, "error": str(e)}
    except Exception as e:
        log.error(f"Error parsing profile: {e}")
        return {"wallet_address": wallet, "error": str(e)}


def _parse_timestamp(ts: str | int | float) -> Optional[datetime]:
    """Parse various timestamp formats to datetime."""
    if not ts:
        return None

    try:
        if isinstance(ts, (int, float)):
            # Unix timestamp (seconds or milliseconds)
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts)
        elif isinstance(ts, str):
            # ISO format
            if "T" in ts:
                ts = ts.replace("Z", "+00:00")
                return datetime.fromisoformat(ts.split("+")[0])
            # Unix timestamp string
            return datetime.fromtimestamp(float(ts))
    except Exception:
        pass
    return None


def _cache_traders(traders: list[dict]) -> None:
    """Cache trader data to database."""
    from db import upsert_trader_cache

    for trader in traders:
        upsert_trader_cache(
            wallet_address=trader.get("wallet_address", ""),
            username=trader.get("username", ""),
            pnl=trader.get("pnl", 0),
            volume=trader.get("volume", 0),
            win_count=trader.get("win_count", 0),
            loss_count=trader.get("loss_count", 0),
        )


# ── SYNC WRAPPERS ────────────────────────────────────────────────────────────


def fetch_leaderboard_sync(
    period: str = "7d",
    limit: int = 50,
    offset: int = 0,
    category: str = "OVERALL",
    order_by: str = "PNL",
) -> list[dict]:
    """Synchronous wrapper for fetch_leaderboard."""
    return asyncio.run(fetch_leaderboard(period, limit, offset, category, order_by))


def fetch_trader_history_sync(wallet: str, hours: int = 720) -> list[dict]:
    """Synchronous wrapper for fetch_trader_history."""
    return asyncio.run(fetch_trader_history(wallet, hours))


def analyze_trader_sync(wallet: str) -> dict:
    """Synchronous wrapper for analyze_trader."""
    return asyncio.run(analyze_trader(wallet))


# ── BATCH OPERATIONS ─────────────────────────────────────────────────────────


async def refresh_leaderboard_cache(period: str = "7d") -> int:
    """
    Refresh the trader cache with leaderboard data.
    Returns count of traders cached.
    """
    traders = await fetch_leaderboard(period=period, limit=100)
    return len(traders)


async def find_top_performers(
    min_win_rate: float = 55.0,
    min_trades: int = 10,
    min_pnl: float = 0,
    period: str = "7d",
) -> list[dict]:
    """
    Find traders matching performance criteria.

    Args:
        min_win_rate: Minimum win rate percentage
        min_trades: Minimum number of trades
        min_pnl: Minimum PnL
        period: Time period for leaderboard

    Returns:
        Filtered list of traders sorted by PnL
    """
    traders = await fetch_leaderboard(period=period, limit=100)

    filtered = []
    for trader in traders:
        wins = trader.get("win_count", 0)
        losses = trader.get("loss_count", 0)
        total = wins + losses

        if total < min_trades:
            continue

        win_rate = (wins / total * 100) if total > 0 else 0
        if win_rate < min_win_rate:
            continue

        pnl = trader.get("pnl", 0)
        if pnl < min_pnl:
            continue

        trader["win_rate"] = round(win_rate, 1)
        filtered.append(trader)

    # Sort by PnL descending
    filtered.sort(key=lambda x: x.get("pnl", 0), reverse=True)
    return filtered
