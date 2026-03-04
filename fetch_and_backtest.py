#!/usr/bin/env python3
"""
fetch_and_backtest.py — pulls real 5-min BTC Polymarket data + Binance prices
then runs the divergence backtest.

Run: python fetch_and_backtest.py            # live API mode
     python fetch_and_backtest.py --offline   # synthetic data (no network)
     python fetch_and_backtest.py --offline --original  # run without optimizations

No API keys needed. All public endpoints.
Takes ~2 minutes to run in live mode (fetches ~288 markets × price history).
"""

import json
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# ── Endpoints ────────────────────────────────────────────────────────────────
GAMMA    = "https://gamma-api.polymarket.com"
CLOB     = "https://clob.polymarket.com"
BINANCE  = "https://api.binance.com"

WINDOW_MIN    = 5           # 5-minute markets
ASSET         = "BTC"
MIN_EDGE      = 0.05        # 5% divergence required to trade
TRADE_SIZE    = 10.0        # USDC per trade (base size for Kelly)
MAX_TRADE     = 30.0        # max USDC per trade (Kelly cap)
TAKER_FEE     = 0.0156      # 1.56% peak fee
OBS_SECONDS   = 30          # observe at T-30s before close
CACHE_FILE    = Path("market_cache.json")   # saves API calls on re-runs

# ── Mode flags ───────────────────────────────────────────────────────────────
OFFLINE  = "--offline" in sys.argv
ORIGINAL = "--original" in sys.argv  # disable optimizations for A/B comparison


# ── Step 1: Generate all 5-min slugs for last 24h ───────────────────────────

def generate_slugs_last_24h(window_min: int = 5) -> list[str]:
    """
    5-min markets run on fixed intervals aligned to window boundaries.
    Slug timestamp = unix time of market START (must be divisible by window*60).
    """
    now     = int(time.time())
    window_s = window_min * 60
    slugs   = []

    # Go back 24h + 1 window buffer
    periods = (24 * 60 // window_min) + 2

    # Round current time down to nearest window boundary
    latest_start = (now // window_s) * window_s

    for i in range(periods):
        start_ts = latest_start - (i * window_s)
        slug = f"btc-updown-{window_min}m-{start_ts}"
        slugs.append(slug)

    return slugs


# ── Step 2: Fetch market data from Gamma API ─────────────────────────────────

def fetch_markets(slugs: list[str]) -> list[dict]:
    """Fetch market metadata for each slug. Cache results to disk."""

    if CACHE_FILE.exists():
        cached = json.loads(CACHE_FILE.read_text())
        cache_age = time.time() - cached.get("fetched_at", 0)
        if cache_age < 3600:  # use cache if < 1 hour old
            print(f"Using cached data ({len(cached['markets'])} markets, {cache_age/60:.0f}min old)")
            return cached["markets"]

    import httpx

    print(f"Fetching {len(slugs)} market slugs from Polymarket Gamma API...")
    markets = []

    with httpx.Client(timeout=10) as client:
        for i, slug in enumerate(slugs):
            try:
                r = client.get(f"{GAMMA}/markets", params={"slug": slug})
                if r.status_code == 200:
                    data = r.json()
                    if isinstance(data, list) and data:
                        markets.extend(data)
                    elif isinstance(data, dict) and data.get("id"):
                        markets.append(data)

                if i % 20 == 0:
                    print(f"  {i}/{len(slugs)} slugs fetched, {len(markets)} markets found...")
                    time.sleep(0.1)  # gentle rate limiting

            except Exception as e:
                continue

    # Also try bulk search as fallback
    print("Running bulk search for btc-updown-5m markets...")
    try:
        r = httpx.get(
            f"{GAMMA}/markets",
            params={"slug_contains": "btc-updown-5m", "limit": 500, "closed": "true"},
            timeout=15,
        )
        if r.status_code == 200:
            bulk = r.json()
            if isinstance(bulk, list):
                existing_ids = {m.get("id") for m in markets}
                new = [m for m in bulk if m.get("id") not in existing_ids]
                markets.extend(new)
                print(f"  Bulk search added {len(new)} more markets")
    except Exception as e:
        print(f"  Bulk search failed: {e}")

    # Filter to last 24h only
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    filtered = []
    for m in markets:
        end_str = m.get("endDate") or m.get("end_date_iso", "")
        if not end_str:
            continue
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if end_dt >= cutoff:
                filtered.append(m)
        except Exception:
            continue

    print(f"Found {len(filtered)} resolved 5-min BTC markets in last 24h")

    # Cache to disk
    CACHE_FILE.write_text(json.dumps({"fetched_at": time.time(), "markets": filtered}))
    return filtered


# ── Step 3: Fetch price history for each market ──────────────────────────────

def fetch_price_history(market_id: str, token_id: str) -> list[dict]:
    """Get 1-min price history for a market from CLOB."""
    import httpx

    try:
        r = httpx.get(
            f"{CLOB}/prices-history",
            params={"market": market_id, "interval": "1m", "fidelity": 1},
            timeout=8,
        )
        if r.status_code == 200:
            return r.json().get("history", [])
    except Exception:
        pass
    return []


# ── Step 4: Fetch Binance BTC 1-min candles ──────────────────────────────────

def fetch_binance_candles(hours: int = 26) -> pd.DataFrame:
    """Pull 1-minute BTC/USDT candles from Binance (no auth needed)."""
    import httpx

    print(f"Fetching Binance BTC 1-min candles (last {hours}h)...")

    all_candles = []
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (hours * 3600 * 1000)
    chunk_ms = 1000 * 60 * 1000  # 1000 minutes per request

    with httpx.Client(timeout=15) as client:
        current_start = start_ms
        while current_start < end_ms:
            r = client.get(
                f"{BINANCE}/api/v3/klines",
                params={
                    "symbol":    "BTCUSDT",
                    "interval":  "1m",
                    "startTime": current_start,
                    "endTime":   min(current_start + chunk_ms, end_ms),
                    "limit":     1000,
                },
            )
            r.raise_for_status()
            candles = r.json()
            if not candles:
                break
            all_candles.extend(candles)
            current_start = candles[-1][0] + 60000  # next minute
            time.sleep(0.1)

    df = pd.DataFrame(all_candles, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "tb_base", "tb_quote", "ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "close"]:
        df[col] = df[col].astype(float)
    df = df.set_index("open_time").sort_index()

    print(f"  Got {len(df)} candles: {df.index[0]} → {df.index[-1]}")
    return df


def get_btc_price(df: pd.DataFrame, dt: datetime) -> float | None:
    dt_floor = dt.replace(second=0, microsecond=0)
    try:
        idx = df.index.get_indexer([dt_floor], method="nearest")[0]
        if idx >= 0:
            return float(df.iloc[idx]["close"])
    except Exception:
        pass
    return None


# ── Step 4b: Generate synthetic BTC candles (offline mode) ───────────────────

def generate_synthetic_btc_candles(hours: int = 26) -> pd.DataFrame:
    """Generate realistic synthetic BTC 1-min candles using geometric Brownian motion."""
    print(f"Generating synthetic BTC 1-min candles (last {hours}h)...")

    random.seed(42)  # reproducible results

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = now - timedelta(hours=hours)
    n_candles = hours * 60

    # BTC ~$87,000, annualized vol ~55%
    price = 87000.0
    dt_ann = 1 / (365.25 * 24 * 60)  # 1 minute in years
    sigma = 0.55
    mu = 0.0  # neutral drift

    rows = []
    for i in range(n_candles):
        ts = start + timedelta(minutes=i)
        # GBM step
        z = random.gauss(0, 1)
        ret = (mu - 0.5 * sigma**2) * dt_ann + sigma * math.sqrt(dt_ann) * z
        price *= math.exp(ret)

        rows.append({
            "open_time": ts,
            "open": round(price * (1 - abs(random.gauss(0, 0.0001))), 2),
            "close": round(price, 2),
        })

    df = pd.DataFrame(rows)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.set_index("open_time").sort_index()

    print(f"  Generated {len(df)} candles: {df.index[0]} → {df.index[-1]}")
    print(f"  Price range: ${df['close'].min():.2f} → ${df['close'].max():.2f}")
    return df


def generate_synthetic_markets(btc_df: pd.DataFrame, window_min: int = 5) -> list[dict]:
    """Generate synthetic Polymarket-style markets aligned to BTC candle data."""
    print("Generating synthetic Polymarket markets...")

    random.seed(123)

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    window_s = window_min * 60
    markets = []

    # Generate markets for the last 24h
    periods = 24 * 60 // window_min
    latest_start_ts = int(now.timestamp()) // window_s * window_s

    for i in range(periods):
        start_ts = latest_start_ts - (i * window_s)
        end_ts = start_ts + window_s
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)

        # Only include if we have BTC data for this period
        start_dt = end_dt - timedelta(minutes=window_min)
        btc_start = get_btc_price(btc_df, start_dt)
        btc_end = get_btc_price(btc_df, end_dt)
        if btc_start is None or btc_end is None:
            continue

        market_id = f"synth-{start_ts}"
        markets.append({
            "id": market_id,
            "slug": f"btc-updown-{window_min}m-{start_ts}",
            "endDate": end_dt.isoformat(),
            "tokens": [f"token-{market_id}"],
            "_synthetic": True,
        })

    print(f"  Generated {len(markets)} synthetic markets")
    return markets


def generate_synthetic_poly_price(btc_df: pd.DataFrame, obs_dt: datetime, start_dt: datetime) -> float:
    """
    Generate a synthetic Polymarket YES price that lags/diverges from BTC reality.
    Simulates market inefficiency where Polymarket price doesn't fully reflect BTC moves.
    """
    random.seed(int(obs_dt.timestamp()))

    btc_start = get_btc_price(btc_df, start_dt)
    btc_obs = get_btc_price(btc_df, obs_dt)

    if btc_start is None or btc_obs is None:
        return 0.50

    move_pct = (btc_obs - btc_start) / btc_start

    # True implied prob based on momentum
    true_prob = 0.5 + (move_pct * 15)
    true_prob = max(0.05, min(0.95, true_prob))

    # Polymarket lags: add noise and dampening (simulates market inefficiency)
    lag_factor = random.uniform(0.3, 0.8)  # market only captures 30-80% of signal
    noise = random.gauss(0, 0.08)  # random noise in market price
    poly_price = 0.5 + (true_prob - 0.5) * lag_factor + noise
    poly_price = max(0.05, min(0.95, poly_price))

    return round(poly_price, 4)


# ── Step 5: Divergence strategy ──────────────────────────────────────────────

def get_poly_price_at(history: list[dict], target_ts: float) -> float | None:
    """Find Polymarket YES price closest to target timestamp."""
    if not history:
        return None
    closest = min(history, key=lambda h: abs(h.get("t", 0) - target_ts))
    price = closest.get("p")
    return float(price) if price is not None else None


def get_recent_volatility(btc_df: pd.DataFrame, dt: datetime, lookback_min: int = 30) -> float:
    """
    OPT 1: Calculate recent realized volatility (std of 1-min returns)
    over a lookback window. Used to normalize the momentum signal.
    """
    dt_floor = dt.replace(second=0, microsecond=0)
    try:
        idx = btc_df.index.get_indexer([dt_floor], method="nearest")[0]
        if idx < lookback_min:
            return 0.0
        window = btc_df.iloc[max(0, idx - lookback_min):idx + 1]["close"]
        returns = window.pct_change().dropna()
        if len(returns) < 5:
            return 0.0
        return float(returns.std())
    except Exception:
        return 0.0


def get_momentum_acceleration(btc_df: pd.DataFrame, start_dt: datetime, obs_dt: datetime) -> float:
    """
    OPT 2: Measure whether momentum is accelerating or decelerating.
    Compare move in first half vs second half of the window.
    Returns > 0 if accelerating (good), < 0 if decelerating (bad).
    """
    mid_dt = start_dt + (obs_dt - start_dt) / 2

    btc_start = get_btc_price(btc_df, start_dt)
    btc_mid = get_btc_price(btc_df, mid_dt)
    btc_obs = get_btc_price(btc_df, obs_dt)

    if not all([btc_start, btc_mid, btc_obs]):
        return 0.0

    first_half = (btc_mid - btc_start) / btc_start
    second_half = (btc_obs - btc_mid) / btc_mid

    # Same sign and second half bigger = accelerating
    if first_half * second_half > 0:
        return abs(second_half) - abs(first_half)
    else:
        # Direction reversed in second half = decelerating
        return -abs(second_half)


def get_multi_obs_agreement(btc_df: pd.DataFrame, start_dt: datetime, obs_dt: datetime) -> float:
    """
    OPT 3: Check agreement between T-60s and T-30s observations.
    Returns confidence multiplier: 1.0 if both agree, 0.0 if they disagree.
    """
    early_obs_dt = start_dt + timedelta(minutes=WINDOW_MIN) - timedelta(seconds=60)

    btc_start = get_btc_price(btc_df, start_dt)
    btc_early = get_btc_price(btc_df, early_obs_dt)
    btc_late = get_btc_price(btc_df, obs_dt)

    if not all([btc_start, btc_early, btc_late]):
        return 0.5

    early_up = btc_early > btc_start
    late_up = btc_late > btc_start

    if early_up == late_up:
        # Both agree on direction — high confidence
        return 1.0
    else:
        # Disagreement — direction flipped between observations
        return 0.0


def detect_edge(
    btc_start: float,
    btc_obs: float,
    poly_up_price: float,
    volatility: float = 0.0,
    acceleration: float = 0.0,
    multi_obs_conf: float = 1.0,
) -> tuple[str | None, float, float]:
    """
    Momentum divergence: actual BTC direction vs Polymarket implied probability.
    Only trade when divergence > MIN_EDGE.

    OPTIMIZED version adds:
    - Volatility-adjusted signal (z-score normalization)
    - Momentum acceleration bonus/penalty
    - Multi-observation confidence gating
    - Returns (side, edge, confidence) instead of just (side, edge)
    """
    move_pct = (btc_obs - btc_start) / btc_start

    if ORIGINAL:
        # ── Original strategy (no optimizations) ──
        our_up_prob = 0.5 + (move_pct * 15)
        our_up_prob = max(0.05, min(0.95, our_up_prob))

        edge_up   = our_up_prob - poly_up_price
        edge_down = (1 - our_up_prob) - (1 - poly_up_price)

        if edge_up > MIN_EDGE:
            return "UP", edge_up, 1.0
        if edge_down > MIN_EDGE:
            return "DOWN", edge_down, 1.0
        return None, 0.0, 0.0

    # ── Optimized strategy ──

    # OPT 1: Volatility-adjusted signal (z-score)
    # Instead of raw move * 15, normalize by recent vol to get a z-score
    if volatility > 0.0001:
        # z-score: how many standard deviations is this 5-min move?
        # Expected 5-min vol = 1-min vol * sqrt(5)
        expected_5m_vol = volatility * math.sqrt(WINDOW_MIN)
        z_score = move_pct / expected_5m_vol if expected_5m_vol > 0 else 0.0
        # Convert z-score to probability using a steeper sigmoid
        # z=1 → ~65%, z=2 → ~80%, z=3 → ~90%
        our_up_prob = 1.0 / (1.0 + math.exp(-z_score * 0.8))
    else:
        # Fallback to linear if no vol data
        our_up_prob = 0.5 + (move_pct * 15)

    our_up_prob = max(0.05, min(0.95, our_up_prob))

    # OPT 2: Momentum acceleration adjustment
    # Accelerating trends are more likely to continue
    accel_bonus = acceleration * 5.0  # scale up the small acceleration values
    accel_bonus = max(-0.05, min(0.05, accel_bonus))  # cap at ±5%

    if move_pct > 0:
        our_up_prob = min(0.95, our_up_prob + accel_bonus)
    else:
        our_up_prob = max(0.05, our_up_prob - accel_bonus)

    # Compute raw edge
    edge_up   = our_up_prob - poly_up_price
    edge_down = (1 - our_up_prob) - (1 - poly_up_price)

    # OPT 3: Multi-observation confidence gating
    # If T-60s and T-30s disagree on direction, require higher edge
    effective_min_edge = MIN_EDGE
    if multi_obs_conf < 0.5:
        effective_min_edge = MIN_EDGE * 1.8  # need 9% edge instead of 5% when signals disagree

    # Confidence score for Kelly sizing
    confidence = multi_obs_conf
    if acceleration > 0:
        confidence = min(1.0, confidence + 0.2)  # bonus for accelerating
    if acceleration < -0.0005:
        confidence = max(0.0, confidence - 0.3)  # penalty for decelerating

    if edge_up > effective_min_edge:
        return "UP", edge_up, confidence
    if edge_down > effective_min_edge:
        return "DOWN", edge_down, confidence
    return None, 0.0, 0.0


def kelly_size(edge: float, entry_price: float, confidence: float) -> float:
    """
    OPT 4: Kelly Criterion position sizing.
    Bet more when edge is larger, less when edge is marginal.
    Uses fractional Kelly (25%) for safety.

    Kelly fraction = (p * b - q) / b
    where p = win probability, b = payout odds, q = 1 - p
    """
    if ORIGINAL:
        return TRADE_SIZE

    # Implied win probability from our edge
    win_prob = entry_price + edge  # our estimated true probability
    win_prob = max(0.01, min(0.99, win_prob))

    # Payout odds: if we buy at `entry_price`, we get 1.0 on win
    # profit = (1/entry_price - 1) on win, lose 1.0 on loss
    b = (1.0 / entry_price) - 1.0  # odds ratio
    if b <= 0:
        return TRADE_SIZE

    q = 1 - win_prob
    kelly_f = (win_prob * b - q) / b
    kelly_f = max(0, kelly_f)

    # Fractional Kelly (25%) — aggressive enough to capture edge, conservative enough to survive variance
    frac_kelly = kelly_f * 0.25

    # Scale confidence into sizing
    frac_kelly *= (0.5 + 0.5 * confidence)  # range: 50%-100% of Kelly size

    # Convert to dollar amount, capped
    size = TRADE_SIZE + frac_kelly * (MAX_TRADE - TRADE_SIZE)
    size = max(TRADE_SIZE * 0.5, min(MAX_TRADE, size))

    return round(size, 2)


# ── Step 6: Run backtest ──────────────────────────────────────────────────────

def run_backtest(markets: list[dict], btc_df: pd.DataFrame) -> list[dict]:
    trades = []
    skipped = 0
    consecutive_losses = 0

    print(f"\nRunning backtest on {len(markets)} markets...\n")

    for m in markets:
        end_str = m.get("endDate") or m.get("end_date_iso", "")
        if not end_str:
            continue

        end_dt   = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        start_dt = end_dt - timedelta(minutes=WINDOW_MIN)
        obs_dt   = end_dt - timedelta(seconds=OBS_SECONDS)

        # BTC prices at key moments
        btc_start = get_btc_price(btc_df, start_dt)
        btc_obs   = get_btc_price(btc_df, obs_dt)
        btc_end   = get_btc_price(btc_df, end_dt)

        if not all([btc_start, btc_obs, btc_end]):
            skipped += 1
            continue

        # Get Polymarket YES price at observation time
        if OFFLINE or m.get("_synthetic"):
            poly_up = generate_synthetic_poly_price(btc_df, obs_dt, start_dt)
        else:
            token_id = ""
            tokens = m.get("tokens") or m.get("clobTokenIds", [])
            if isinstance(tokens, list) and tokens:
                token_id = tokens[0] if isinstance(tokens[0], str) else tokens[0].get("token_id", "")

            history = fetch_price_history(m.get("id", ""), token_id)
            poly_up = get_poly_price_at(history, obs_dt.timestamp()) or 0.50

        # Actual resolution
        resolved_up = btc_end >= btc_start

        # ── Compute optimization signals ──
        volatility = get_recent_volatility(btc_df, obs_dt) if not ORIGINAL else 0.0
        acceleration = get_momentum_acceleration(btc_df, start_dt, obs_dt) if not ORIGINAL else 0.0
        multi_obs_conf = get_multi_obs_agreement(btc_df, start_dt, obs_dt) if not ORIGINAL else 1.0

        # OPT 5: Streak filter — reduce size after 3+ consecutive losses
        if not ORIGINAL and consecutive_losses >= 3:
            # Cool-down: skip 1 trade after 3 straight losses
            consecutive_losses = 0
            continue

        # Check for edge
        side, edge, confidence = detect_edge(btc_start, btc_obs, poly_up,
                                             volatility, acceleration, multi_obs_conf)
        if side is None:
            continue

        # OPT 4: Kelly-sized position
        entry = poly_up if side == "UP" else (1 - poly_up)
        trade_size = kelly_size(edge, entry, confidence)

        fee   = TAKER_FEE * 4 * entry * (1 - entry)
        entry_after_fee = entry * (1 - fee)
        shares = trade_size / entry

        won = (side == "UP" and resolved_up) or (side == "DOWN" and not resolved_up)
        pnl = round((shares * 1.0) - trade_size, 4) if won else -trade_size

        # Track streak
        if won:
            consecutive_losses = 0
        else:
            consecutive_losses += 1

        trades.append({
            "time":          start_dt.strftime("%m-%d %H:%M"),
            "side":          side,
            "entry":         round(entry, 4),
            "entry_after_fee": round(entry_after_fee, 4),
            "edge_pct":      round(edge * 100, 2),
            "poly_up_price": round(poly_up, 4),
            "btc_start":     round(btc_start, 2),
            "btc_obs":       round(btc_obs, 2),
            "btc_end":       round(btc_end, 2),
            "btc_move_pct":  round((btc_end - btc_start) / btc_start * 100, 4),
            "resolved_up":   resolved_up,
            "won":           won,
            "pnl":           pnl,
            "trade_size":    trade_size,
            "confidence":    round(confidence, 2),
            "volatility":    round(volatility * 10000, 2),  # in bps
            "acceleration":  round(acceleration * 10000, 2),
        })

    print(f"Skipped {skipped} markets (missing BTC price data)")
    return trades


# ── Step 7: Print results ─────────────────────────────────────────────────────

def print_results(trades: list[dict]) -> None:
    if not trades:
        print("\n⚠️  No trades generated.")
        print("   Possible reasons:")
        print("   - 5-min markets too new, limited history in API")
        print("   - Min edge too high — try lowering MIN_EDGE to 0.03")
        print("   - No resolved markets in cache — delete market_cache.json and retry")
        return

    wins   = [t for t in trades if t["won"]]
    losses = [t for t in trades if not t["won"]]
    total_pnl    = sum(t["pnl"] for t in trades)
    total_risked = sum(t.get("trade_size", TRADE_SIZE) for t in trades)
    win_rate     = len(wins) / len(trades) * 100
    avg_size     = total_risked / len(trades)

    print("═" * 75)
    label = "ORIGINAL" if ORIGINAL else "OPTIMIZED"
    print(f"  BACKTEST [{label}] — BTC {WINDOW_MIN}-MIN MARKETS | LAST 24H")
    if OFFLINE:
        print(f"  *** OFFLINE MODE — synthetic data (seed=42) ***")
    if not ORIGINAL:
        print(f"  Optimizations: Kelly sizing, vol-adj signal, accel filter, multi-obs, streak")
    print(f"  Min edge: {MIN_EDGE*100:.0f}% | Observe at T-{OBS_SECONDS}s | avg ${avg_size:.1f}/trade")
    print("═" * 75)

    print(f"\n📊 SUMMARY")
    print(f"  Trades          : {len(trades)}")
    print(f"  Wins / Losses   : {len(wins)} / {len(losses)}")
    print(f"  Win rate        : {win_rate:.1f}%  (target: >54%)")
    print(f"  USDC risked     : ${total_risked:.2f}")
    print(f"  Total P&L       : {'+'if total_pnl>=0 else ''}${total_pnl:.2f}")
    print(f"  ROI             : {total_pnl/total_risked*100:.1f}%")
    print(f"  Avg edge        : {sum(t['edge_pct'] for t in trades)/len(trades):.1f}%")
    if not ORIGINAL:
        print(f"  Avg trade size  : ${avg_size:.2f}  (Kelly range: ${min(t.get('trade_size', TRADE_SIZE) for t in trades):.0f}-${max(t.get('trade_size', TRADE_SIZE) for t in trades):.0f})")
        print(f"  Avg confidence  : {sum(t.get('confidence', 1.0) for t in trades)/len(trades):.2f}")

    print(f"\n📋 TRADE LOG (top 20 by |P&L|)")
    sorted_trades = sorted(trades, key=lambda t: abs(t["pnl"]), reverse=True)
    print(f"  {'Time':14} {'Side':5} {'Entry':7} {'Edge':7} {'BTC Δ':8} {'Size':6} {'Conf':5} {'Result'}")
    print(f"  {'-'*14} {'-'*5} {'-'*7} {'-'*7} {'-'*8} {'-'*6} {'-'*5} {'-'*12}")
    for t in sorted_trades[:20]:
        ts = t.get("trade_size", TRADE_SIZE)
        result = f"✅ +${t['pnl']:.2f}" if t["won"] else f"❌ -${ts:.2f}"
        print(
            f"  {t['time']:14} {t['side']:5} "
            f"${t['entry']:.3f}  "
            f"{t['edge_pct']:+.1f}%  "
            f"{t['btc_move_pct']:+.4f}%  "
            f"${ts:5.1f} "
            f" {t.get('confidence', 1.0):.1f}  "
            f"{result}"
        )

    print(f"\n💰 $100 ACCOUNT SIMULATION")
    balance = 100.0
    peak    = 100.0
    worst   = 100.0
    for t in trades:
        balance += t["pnl"]
        peak    = max(peak, balance)
        worst   = min(worst, balance)
    drawdown = (peak - worst) / peak * 100
    print(f"  Start       : $100.00")
    print(f"  End         : ${balance:.2f}")
    print(f"  Peak        : ${peak:.2f}")
    print(f"  Worst       : ${worst:.2f}")
    print(f"  Max drawdown: {drawdown:.1f}%")

    print(f"\n🔍 EDGE BREAKDOWN")
    buckets = [
        ("3-5%",  0.03, 0.05),
        ("5-8%",  0.05, 0.08),
        ("8-12%", 0.08, 0.12),
        ("12%+",  0.12, 1.00),
    ]
    for label, lo, hi in buckets:
        bucket = [t for t in trades if lo <= t["edge_pct"]/100 < hi]
        if bucket:
            wr = sum(1 for t in bucket if t["won"]) / len(bucket) * 100
            avg_pnl = sum(t["pnl"] for t in bucket) / len(bucket)
            print(f"  {label}: {len(bucket):3d} trades | {wr:.0f}% win | avg P&L ${avg_pnl:+.2f}")

    if not ORIGINAL:
        print(f"\n🎯 CONFIDENCE BREAKDOWN")
        conf_buckets = [
            ("Low  (0-0.4)",   0.0, 0.4),
            ("Med  (0.4-0.7)", 0.4, 0.7),
            ("High (0.7-1.0)", 0.7, 1.01),
        ]
        for label, lo, hi in conf_buckets:
            bucket = [t for t in trades if lo <= t.get("confidence", 1.0) < hi]
            if bucket:
                wr = sum(1 for t in bucket if t["won"]) / len(bucket) * 100
                avg_pnl = sum(t["pnl"] for t in bucket) / len(bucket)
                avg_sz = sum(t.get("trade_size", TRADE_SIZE) for t in bucket) / len(bucket)
                print(f"  {label}: {len(bucket):3d} trades | {wr:.0f}% win | avg size ${avg_sz:.1f} | avg P&L ${avg_pnl:+.2f}")

    # Save full results
    out = Path("backtest_results.json")
    out.write_text(json.dumps(trades, indent=2))
    print(f"\n  Full results saved → {out}")
    print("═" * 75)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = "OFFLINE (synthetic data)" if OFFLINE else "LIVE (API)"
    label = "ORIGINAL" if ORIGINAL else "OPTIMIZED"
    print(f"\n🔍 BTC {WINDOW_MIN}-min Polymarket Backtest [{label}] | Last 24h | Mode: {mode}\n")

    if OFFLINE:
        # Generate synthetic data — no network calls needed
        btc_df = generate_synthetic_btc_candles(hours=26)
        markets = generate_synthetic_markets(btc_df, WINDOW_MIN)
    else:
        # 1. Generate slugs
        slugs = generate_slugs_last_24h(WINDOW_MIN)
        print(f"Generated {len(slugs)} slugs to check\n")

        # 2. Fetch markets
        markets = fetch_markets(slugs)

        if not markets:
            print("\n⚠️  No markets found. The 5-min markets are very new (~3 weeks).")
            print("   Try: delete market_cache.json and rerun")
            print("   Or check polymarket.com/crypto/5M to confirm markets exist")
            print("\n💡 Tip: run with --offline flag for synthetic data backtest")
            exit(1)

        # 3. Fetch BTC candles
        btc_df = fetch_binance_candles(hours=26)

    # 4. Run backtest
    trades = run_backtest(markets, btc_df)

    # 5. Print results
    print_results(trades)
