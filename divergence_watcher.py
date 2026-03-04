# divergence_watcher.py — Live BTC 5-min divergence strategy
#
# Monitors BTC price and Polymarket 5-min Up/Down markets.
# When BTC momentum diverges from Polymarket's implied probability,
# places Kelly-sized trades to capture the edge.
#
# Optimizations (from backtested strategy):
#   1. Volatility-adjusted signal (z-score)
#   2. Momentum acceleration filter
#   3. Multi-observation confirmation (T-60s + T-30s)
#   4. Kelly Criterion position sizing (25% fractional)
#   5. Dynamic edge threshold
#   6. Streak filter (cooldown after N losses)
#
# Usage: Launched automatically by main.py when DIVERGENCE_ENABLED=true

from __future__ import annotations

import asyncio
import logging
import math
import time

import httpx

from config import (
    DIVERGENCE_BASE_SIZE,
    DIVERGENCE_MAX_SIZE,
    DIVERGENCE_MIN_EDGE,
    DIVERGENCE_OBS_SECONDS,
    DIVERGENCE_STREAK_LIMIT,
    MAX_MARKET_PCT,
    MAX_TRADE_PCT,
    MAX_WALLET_PCT,
    MIN_COPY_USDC,
    POLYMARKET_CLOB,
)
from db import distribute_profit, get_fund_balance, log_trade, update_fund_balance
from position_manager import OrderIntent, get_effective_balance

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
BINANCE = "https://api.binance.us"
WINDOW_S = 5 * 60  # 300 seconds

# BTC price buffer: {unix_minute_ts: close_price}
_btc_prices: dict[int, float] = {}


# ── MAIN LOOP ────────────────────────────────────────────────────────────────


async def divergence_loop(mode: str) -> None:
    """
    Main loop for BTC 5-min divergence strategy.

    Each cycle:
      1. Discover current market (Gamma API)
      2. Observe BTC at T-60s and T-30s before close
      3. Detect edge (vol-adjusted z-score + filters)
      4. Kelly-size and execute if edge > threshold
      5. Paper mode: resolve inline. Live: on-chain resolution.
    """
    log.info("Divergence strategy started (mode=%s)", mode)
    losses = 0

    # Pre-fill BTC buffer
    await _refresh_btc(60)
    if len(_btc_prices) < 5:
        log.warning("BTC buffer sparse (%d pts) — retrying in 30s", len(_btc_prices))
        await asyncio.sleep(30)
        await _refresh_btc(60)

    while True:
        try:
            # ── Align to next cycle ──
            now = time.time()
            window_end = ((int(now) // WINDOW_S) + 1) * WINDOW_S
            window_start = window_end - WINDOW_S
            obs1_at = window_end - 60
            obs2_at = window_end - DIVERGENCE_OBS_SECONDS

            # Too late for this window? Wait for next.
            if now > obs1_at + 5:
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - now))
                continue

            # ── Streak cooldown ──
            if losses >= DIVERGENCE_STREAK_LIMIT:
                log.info("Streak cooldown (%d losses) — skipping cycle", losses)
                losses = 0
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - now))
                continue

            # ── Discover market ──
            slug = f"btc-updown-5m-{window_start}"
            market = await _fetch_market(slug)
            if not market:
                log.debug("No market for %s — waiting", slug)
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - now))
                continue

            up_token, down_token = _extract_tokens(market)
            if not up_token:
                log.warning("No token IDs for %s", slug)
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - now))
                continue

            market_id = market.get("conditionId") or market.get("id", "")

            # ── T-60s: first observation ──
            wait = obs1_at - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

            await _refresh_btc(35)
            btc_start = _btc_at(window_start)
            btc_obs1 = _btc_latest()

            if btc_start is None or btc_obs1 is None:
                log.debug("Missing BTC data (start=%s obs1=%s)", btc_start, btc_obs1)
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - now))
                continue

            # ── T-30s: second observation ──
            wait = obs2_at - time.time()
            if wait > 0:
                await asyncio.sleep(wait)

            await _refresh_btc(5)
            btc_obs2 = _btc_latest()
            if btc_obs2 is None:
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - time.time()))
                continue

            # ── Polymarket "Up" price ──
            poly_up = await _get_poly_up(up_token)
            if poly_up is None or poly_up <= 0.01 or poly_up >= 0.99:
                log.debug("Bad poly price: %s", poly_up)
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - time.time()))
                continue

            # ── Edge detection ──
            vol = _calc_volatility()
            accel = _calc_acceleration(btc_start, btc_obs1, btc_obs2)
            multi = 1.0 if (btc_obs1 > btc_start) == (btc_obs2 > btc_start) else 0.0

            side, edge, conf = _detect_edge(
                btc_start, btc_obs2, poly_up, vol, accel, multi,
            )
            if side is None:
                log.debug(
                    "No edge | poly=%.3f btc=%+.4f%% vol=%.6f",
                    poly_up, (btc_obs2 - btc_start) / btc_start * 100, vol,
                )
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - time.time()))
                continue

            # ── Size trade ──
            balance = get_effective_balance("main")
            if balance < 1.0:
                log.debug("Balance too low: $%.2f", balance)
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - time.time()))
                continue

            entry = poly_up if side == "UP" else (1 - poly_up)
            size = _kelly_size(edge, entry, conf, balance)
            if size < MIN_COPY_USDC:
                await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - time.time()))
                continue

            token_id = up_token if side == "UP" else down_token

            log.info(
                "EDGE %s | %.1f%% edge, %.0f%% conf | $%.2f (%.1f%% of $%.2f) "
                "| poly=%.3f btc=%+.3f%%",
                side, edge * 100, conf * 100, size,
                size / balance * 100, balance,
                poly_up, (btc_obs2 - btc_start) / btc_start * 100,
            )

            # ── Execute ──
            if mode == "paper":
                won, pnl = await _exec_paper(
                    market_id, token_id, side, size, entry,
                    btc_start, window_end,
                )
            else:
                won, pnl = await _exec_live(
                    market_id, token_id, side, size,
                )

            if won is True:
                losses = 0
                log.info(
                    "WIN +$%.2f | bal=$%.2f",
                    pnl, get_effective_balance("main"),
                )
            elif won is False:
                losses += 1
                log.info(
                    "LOSS -$%.2f | streak=%d | bal=$%.2f",
                    size, losses, get_effective_balance("main"),
                )

            # Sleep until next cycle's observation window
            await asyncio.sleep(max(1, window_end + WINDOW_S - 60 - time.time()))

        except Exception as e:
            log.error("Divergence error: %s", e, exc_info=True)
            await asyncio.sleep(30)


# ── BTC PRICE DATA ───────────────────────────────────────────────────────────


async def _refresh_btc(lookback_min: int = 35) -> None:
    """Fetch recent BTC 1-min candles from Binance.US into buffer."""
    try:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - (lookback_min * 60 * 1000)

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{BINANCE}/api/v3/klines",
                params={
                    "symbol": "BTCUSDT",
                    "interval": "1m",
                    "startTime": start_ms,
                    "endTime": end_ms,
                    "limit": lookback_min + 5,
                },
            )
            r.raise_for_status()
            candles = r.json()

        for c in candles:
            ts = int(c[0]) // 1000  # open_time ms -> seconds
            _btc_prices[ts] = float(c[4])  # close price

        # Trim entries older than 2 hours
        cutoff = int(time.time()) - 7200
        for k in [k for k in _btc_prices if k < cutoff]:
            del _btc_prices[k]

    except Exception as e:
        log.warning("BTC price fetch failed: %s", e)


def _btc_at(ts: int) -> float | None:
    """Get BTC price closest to the given unix timestamp."""
    if not _btc_prices:
        return None
    minute_ts = (ts // 60) * 60
    # Check exact and nearby minutes
    for offset in (0, -60, 60, -120, 120):
        price = _btc_prices.get(minute_ts + offset)
        if price is not None:
            return price
    # Nearest available
    nearest = min(_btc_prices, key=lambda k: abs(k - ts))
    return _btc_prices[nearest]


def _btc_latest() -> float | None:
    """Get the most recent BTC price."""
    if not _btc_prices:
        return None
    return _btc_prices[max(_btc_prices)]


# ── MARKET DISCOVERY ─────────────────────────────────────────────────────────


async def _fetch_market(slug: str) -> dict | None:
    """Fetch market metadata from Gamma API by slug."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{GAMMA}/markets",
                params={"slug": slug},
                headers={"User-Agent": "polybot/1.0"},
            )
            if r.status_code != 200:
                return None
            data = r.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict) and data.get("id"):
                return data
    except Exception as e:
        log.debug("Market fetch failed (%s): %s", slug, e)
    return None


def _extract_tokens(market: dict) -> tuple[str, str]:
    """Extract (up_token_id, down_token_id) from market data."""
    import json as _json

    # clobTokenIds may be a JSON string or a list
    raw = market.get("clobTokenIds") or market.get("tokens") or []
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except (ValueError, TypeError):
            return "", ""

    if not isinstance(raw, list) or not raw:
        return "", ""

    # Parse outcomes to map token positions
    outcomes_raw = market.get("outcomes") or []
    if isinstance(outcomes_raw, str):
        try:
            outcomes_raw = _json.loads(outcomes_raw)
        except (ValueError, TypeError):
            outcomes_raw = []

    # If we have outcome labels, match them to tokens
    if outcomes_raw and len(outcomes_raw) == len(raw):
        up_token = ""
        down_token = ""
        for i, outcome in enumerate(outcomes_raw):
            tid = raw[i] if isinstance(raw[i], str) else raw[i].get("token_id", "")
            label = outcome.lower() if isinstance(outcome, str) else ""
            if "up" in label or "yes" in label:
                up_token = tid
            elif "down" in label or "no" in label:
                down_token = tid
        if up_token or down_token:
            return up_token, down_token

    # Fallback: first=Up, second=Down (Polymarket convention)
    first = raw[0] if isinstance(raw[0], str) else raw[0].get("token_id", "")
    second = raw[1] if len(raw) > 1 and isinstance(raw[1], str) else (
        raw[1].get("token_id", "") if len(raw) > 1 else ""
    )
    return first, second


# ── POLYMARKET PRICE ─────────────────────────────────────────────────────────


async def _get_poly_up(token_id: str) -> float | None:
    """Get current 'Up' price from CLOB orderbook midpoint."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(
                f"{POLYMARKET_CLOB}/book",
                params={"token_id": token_id},
            )
            if r.status_code != 200:
                return None
            book = r.json()

        bids = book.get("bids", [])
        asks = book.get("asks", [])

        if bids and asks:
            best_bid = float(bids[0]["price"])
            best_ask = float(asks[0]["price"])
            if best_bid > 0 and best_ask > 0:
                return (best_bid + best_ask) / 2

        # Fallback to one-sided book
        if bids:
            return float(bids[0]["price"])
        if asks:
            return float(asks[0]["price"])
    except Exception as e:
        log.debug("CLOB book fetch failed: %s", e)
    return None


# ── STRATEGY LOGIC ───────────────────────────────────────────────────────────


def _detect_edge(
    btc_start: float,
    btc_obs: float,
    poly_up: float,
    volatility: float,
    acceleration: float,
    multi_obs_conf: float,
) -> tuple[str | None, float, float]:
    """
    Optimized edge detection with vol-adjusted z-score signal.

    Returns (side, edge, confidence) or (None, 0, 0) if no edge.
    """
    move_pct = (btc_obs - btc_start) / btc_start

    # Volatility-adjusted signal (z-score → sigmoid)
    if volatility > 0.0001:
        expected_5m_vol = volatility * math.sqrt(5)
        z_score = move_pct / expected_5m_vol if expected_5m_vol > 0 else 0
        our_up = 1.0 / (1.0 + math.exp(-z_score * 0.8))
    else:
        our_up = 0.5 + (move_pct * 15)

    our_up = max(0.05, min(0.95, our_up))

    # Momentum acceleration bonus
    accel_bonus = max(-0.05, min(0.05, acceleration * 5.0))
    if move_pct > 0:
        our_up = min(0.95, our_up + accel_bonus)
    else:
        our_up = max(0.05, our_up - accel_bonus)

    # Edge
    edge_up = our_up - poly_up
    edge_down = (1 - our_up) - (1 - poly_up)

    # Dynamic threshold (higher when observations disagree)
    threshold = DIVERGENCE_MIN_EDGE * 1.8 if multi_obs_conf < 0.5 else DIVERGENCE_MIN_EDGE

    # Confidence scoring
    confidence = multi_obs_conf
    if acceleration > 0:
        confidence = min(1.0, confidence + 0.2)
    if acceleration < -0.0005:
        confidence = max(0.0, confidence - 0.3)

    if edge_up > threshold:
        return "UP", edge_up, confidence
    if edge_down > threshold:
        return "DOWN", edge_down, confidence
    return None, 0.0, 0.0


def _kelly_size(
    edge: float, entry_price: float, confidence: float, balance: float,
) -> float:
    """25% fractional Kelly with confidence scaling + guardrails."""
    win_prob = max(0.01, min(0.99, entry_price + edge))
    b = (1.0 / entry_price) - 1.0 if entry_price > 0 else 0

    if b <= 0:
        size = min(DIVERGENCE_BASE_SIZE, balance * MAX_TRADE_PCT)
    else:
        q = 1 - win_prob
        kelly_f = max(0, (win_prob * b - q) / b)
        frac_kelly = kelly_f * 0.25 * (0.5 + 0.5 * confidence)
        size = DIVERGENCE_BASE_SIZE + frac_kelly * (DIVERGENCE_MAX_SIZE - DIVERGENCE_BASE_SIZE)
        size = max(DIVERGENCE_BASE_SIZE * 0.5, min(DIVERGENCE_MAX_SIZE, size))

    # Guardrails
    size = min(size, balance * MAX_TRADE_PCT)
    size = min(size, balance * MAX_MARKET_PCT)
    size = min(size, balance)
    return round(size, 2)


def _calc_volatility() -> float:
    """Std dev of 1-min BTC returns over last 30 minutes."""
    if len(_btc_prices) < 10:
        return 0.0

    sorted_prices = sorted(_btc_prices.items())[-30:]
    if len(sorted_prices) < 5:
        return 0.0

    prices = [p for _, p in sorted_prices]
    returns = [
        (prices[i] - prices[i - 1]) / prices[i - 1]
        for i in range(1, len(prices))
    ]
    if not returns:
        return 0.0

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance)


def _calc_acceleration(
    btc_start: float, btc_obs1: float, btc_obs2: float,
) -> float:
    """Compare first-half vs second-half momentum."""
    if not all([btc_start, btc_obs1, btc_obs2]):
        return 0.0
    first_half = (btc_obs1 - btc_start) / btc_start
    second_half = (btc_obs2 - btc_obs1) / btc_obs1
    if first_half * second_half > 0:
        return abs(second_half) - abs(first_half)
    return -abs(second_half)


# ── EXECUTION ────────────────────────────────────────────────────────────────


async def _exec_paper(
    market_id: str,
    token_id: str,
    side: str,
    size: float,
    entry: float,
    btc_start: float,
    window_end: int,
) -> tuple[bool | None, float]:
    """
    Paper mode: buy shares, wait for resolution, settle inline.

    Returns (won: bool, pnl: float).
    """
    balance = get_fund_balance("main")
    if balance < size:
        log.warning("Insufficient paper balance: $%.2f < $%.2f", balance, size)
        return None, 0

    shares = size / entry if entry > 0 else 0

    # Deduct trade amount
    update_fund_balance("main", balance - size, trade_count_delta=1)

    log_trade(
        market_id=market_id,
        trader_wallet="divergence_bot",
        token_id=token_id,
        side="BUY",
        shares=shares,
        usdc_amount=size,
        price=entry,
        mode="paper",
        success=True,
        outcome=side,
        fund_id="main",
    )

    log.info(
        "PAPER BUY %s | %.4f shares @ $%.4f | $%.2f",
        side, shares, entry, size,
    )

    # Wait for market close (+2s buffer for price settlement)
    wait = window_end - time.time() + 2
    if wait > 0:
        await asyncio.sleep(wait)

    # Check outcome
    await _refresh_btc(2)
    btc_end = _btc_latest()
    if btc_end is None:
        log.warning("Cannot determine close price — treating as loss")
        update_fund_balance("main", get_fund_balance("main"), pnl_delta=-size)
        return False, -size

    won = (side == "UP" and btc_end >= btc_start) or (
        side == "DOWN" and btc_end < btc_start
    )

    payout = shares if won else 0  # $1/share on win, $0 on loss
    pnl = payout - size

    # Credit payout and record PnL
    current = get_fund_balance("main")
    update_fund_balance("main", current + payout, pnl_delta=pnl)

    # Distribute share of profit to allocation funds
    if pnl > 0:
        distribute_profit(pnl)

    log_trade(
        market_id=market_id,
        trader_wallet="divergence_bot",
        token_id=token_id,
        side="RESOLVE",
        shares=shares,
        usdc_amount=payout,
        price=1.0 if won else 0.0,
        mode="paper",
        success=True,
        outcome=f"{side}_{'WIN' if won else 'LOSS'}",
        fund_id="main",
    )

    return won, pnl


async def _exec_live(
    market_id: str,
    token_id: str,
    side: str,
    size: float,
) -> tuple[bool | None, float]:
    """
    Live mode: place FOK order via executor.
    Resolution happens on-chain; resolver loop picks it up.

    Returns (None, 0) since outcome is unknown until resolution.
    """
    intent = OrderIntent(
        market_id=market_id,
        trader_wallet="divergence_bot",
        token_id=token_id,
        side="BUY",
        outcome=side,
        usdc_amount=size,
        fund_id="main",
    )

    try:
        from executor import execute

        success = execute(intent)
        if success:
            log.info("LIVE ORDER FILLED: BUY %s $%.2f", side, size)
        else:
            log.warning("LIVE ORDER REJECTED: BUY %s $%.2f", side, size)
        # Outcome unknown until on-chain resolution
        return None, 0
    except Exception as e:
        log.error("Live order failed: %s", e)
        return None, 0
