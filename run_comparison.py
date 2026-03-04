#!/usr/bin/env python3
"""
run_comparison.py — Head-to-head: Original vs Optimized BTC 5-min strategy
                    with proper guardrails at your current balance.

Usage: python run_comparison.py              # live API
       python run_comparison.py --offline    # synthetic data
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
GAMMA   = "https://gamma-api.polymarket.com"
CLOB    = "https://clob.polymarket.com"
BINANCE = "https://api.binance.us"

WINDOW_MIN    = 5
ASSET         = "BTC"
MIN_EDGE      = 0.05
OBS_SECONDS   = 30
TAKER_FEE     = 0.0156
CACHE_FILE    = Path("market_cache.json")

OFFLINE = "--offline" in sys.argv

# ── Your current balance + guardrails from config.py ─────────────────────────
STARTING_BALANCE = 78.41
MAX_TRADE_PCT    = 0.15   # 15% of balance per trade
MAX_MARKET_PCT   = 0.15   # 15% max exposure per market
MAX_WALLET_PCT   = 0.50   # 50% max total exposure

# Original strategy: flat $10/trade (capped by guardrails)
ORIG_TRADE_SIZE = 10.0

# Optimized strategy: Kelly-sized, $10 base, $30 max (capped by guardrails)
OPT_BASE_SIZE = 10.0
OPT_MAX_SIZE  = 30.0


# ── Data fetching (reused from fetch_and_backtest.py) ────────────────────────

def generate_slugs_last_24h(window_min=5):
    now = int(time.time())
    window_s = window_min * 60
    slugs = []
    periods = (24 * 60 // window_min) + 2
    latest_start = (now // window_s) * window_s
    for i in range(periods):
        start_ts = latest_start - (i * window_s)
        slugs.append(f"btc-updown-{window_min}m-{start_ts}")
    return slugs


def fetch_markets(slugs):
    if CACHE_FILE.exists():
        cached = json.loads(CACHE_FILE.read_text())
        cache_age = time.time() - cached.get("fetched_at", 0)
        if cache_age < 3600:
            print(f"Using cached data ({len(cached['markets'])} markets, {cache_age/60:.0f}min old)")
            return cached["markets"]

    import httpx
    print(f"Fetching {len(slugs)} market slugs from Polymarket Gamma API...")
    markets = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    with httpx.Client(timeout=10, headers=headers) as client:
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
                    time.sleep(0.1)
            except Exception:
                continue

    # Bulk search fallback
    print("Running bulk search for btc-updown-5m markets...")
    try:
        import httpx as hx
        r = hx.get(
            f"{GAMMA}/markets",
            params={"slug_contains": "btc-updown-5m", "limit": 500, "closed": "true"},
            timeout=15, headers=headers,
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
    CACHE_FILE.write_text(json.dumps({"fetched_at": time.time(), "markets": filtered}))
    return filtered


def fetch_price_history(market_id, token_id):
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


def fetch_binance_candles(hours=26):
    import httpx
    print(f"Fetching Binance BTC 1-min candles (last {hours}h)...")
    all_candles = []
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (hours * 3600 * 1000)
    chunk_ms = 1000 * 60 * 1000

    with httpx.Client(timeout=15) as client:
        current_start = start_ms
        while current_start < end_ms:
            r = client.get(
                f"{BINANCE}/api/v3/klines",
                params={
                    "symbol": "BTCUSDT", "interval": "1m",
                    "startTime": current_start,
                    "endTime": min(current_start + chunk_ms, end_ms),
                    "limit": 1000,
                },
            )
            r.raise_for_status()
            candles = r.json()
            if not candles:
                break
            all_candles.extend(candles)
            current_start = candles[-1][0] + 60000
            time.sleep(0.1)

    df = pd.DataFrame(all_candles, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "tb_base", "tb_quote", "ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for col in ["open", "close"]:
        df[col] = df[col].astype(float)
    df = df.set_index("open_time").sort_index()
    print(f"  Got {len(df)} candles: {df.index[0]} -> {df.index[-1]}")
    return df


def generate_synthetic_btc_candles(hours=26):
    print(f"Generating synthetic BTC 1-min candles (last {hours}h)...")
    random.seed(42)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    start = now - timedelta(hours=hours)
    n = hours * 60
    price = 87000.0
    dt_ann = 1 / (365.25 * 24 * 60)
    sigma = 0.55

    rows = []
    for i in range(n):
        ts = start + timedelta(minutes=i)
        z = random.gauss(0, 1)
        ret = (-0.5 * sigma**2) * dt_ann + sigma * math.sqrt(dt_ann) * z
        price *= math.exp(ret)
        rows.append({"open_time": ts, "open": round(price * (1 - abs(random.gauss(0, 0.0001))), 2), "close": round(price, 2)})

    df = pd.DataFrame(rows)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df = df.set_index("open_time").sort_index()
    print(f"  Generated {len(df)} candles: ${df['close'].min():.2f} -> ${df['close'].max():.2f}")
    return df


def generate_synthetic_markets(btc_df, window_min=5):
    print("Generating synthetic Polymarket markets...")
    random.seed(123)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    window_s = window_min * 60
    markets = []
    periods = 24 * 60 // window_min
    latest_start_ts = int(now.timestamp()) // window_s * window_s

    for i in range(periods):
        start_ts = latest_start_ts - (i * window_s)
        end_ts = start_ts + window_s
        end_dt = datetime.fromtimestamp(end_ts, tz=timezone.utc)
        start_dt = end_dt - timedelta(minutes=window_min)
        if get_btc_price(btc_df, start_dt) is None or get_btc_price(btc_df, end_dt) is None:
            continue
        markets.append({
            "id": f"synth-{start_ts}", "slug": f"btc-updown-{window_min}m-{start_ts}",
            "endDate": end_dt.isoformat(), "tokens": [f"token-synth-{start_ts}"], "_synthetic": True,
        })
    print(f"  Generated {len(markets)} synthetic markets")
    return markets


def get_btc_price(df, dt):
    dt_floor = dt.replace(second=0, microsecond=0)
    try:
        idx = df.index.get_indexer([dt_floor], method="nearest")[0]
        if idx >= 0:
            return float(df.iloc[idx]["close"])
    except Exception:
        pass
    return None


def get_poly_price_at(history, target_ts):
    if not history:
        return None
    closest = min(history, key=lambda h: abs(h.get("t", 0) - target_ts))
    price = closest.get("p")
    return float(price) if price is not None else None


def generate_synthetic_poly_price(btc_df, obs_dt, start_dt):
    random.seed(int(obs_dt.timestamp()))
    btc_start = get_btc_price(btc_df, start_dt)
    btc_obs = get_btc_price(btc_df, obs_dt)
    if btc_start is None or btc_obs is None:
        return 0.50
    move_pct = (btc_obs - btc_start) / btc_start
    true_prob = max(0.05, min(0.95, 0.5 + (move_pct * 15)))
    lag = random.uniform(0.3, 0.8)
    noise = random.gauss(0, 0.08)
    return round(max(0.05, min(0.95, 0.5 + (true_prob - 0.5) * lag + noise)), 4)


# ── Optimization helpers ─────────────────────────────────────────────────────

def get_recent_volatility(btc_df, dt, lookback_min=30):
    dt_floor = dt.replace(second=0, microsecond=0)
    try:
        idx = btc_df.index.get_indexer([dt_floor], method="nearest")[0]
        if idx < lookback_min:
            return 0.0
        window = btc_df.iloc[max(0, idx - lookback_min):idx + 1]["close"]
        returns = window.pct_change().dropna()
        return float(returns.std()) if len(returns) >= 5 else 0.0
    except Exception:
        return 0.0


def get_momentum_acceleration(btc_df, start_dt, obs_dt):
    mid_dt = start_dt + (obs_dt - start_dt) / 2
    btc_start = get_btc_price(btc_df, start_dt)
    btc_mid = get_btc_price(btc_df, mid_dt)
    btc_obs = get_btc_price(btc_df, obs_dt)
    if not all([btc_start, btc_mid, btc_obs]):
        return 0.0
    first_half = (btc_mid - btc_start) / btc_start
    second_half = (btc_obs - btc_mid) / btc_mid
    if first_half * second_half > 0:
        return abs(second_half) - abs(first_half)
    return -abs(second_half)


def get_multi_obs_agreement(btc_df, start_dt, obs_dt):
    early_dt = start_dt + timedelta(minutes=WINDOW_MIN) - timedelta(seconds=60)
    btc_start = get_btc_price(btc_df, start_dt)
    btc_early = get_btc_price(btc_df, early_dt)
    btc_late = get_btc_price(btc_df, obs_dt)
    if not all([btc_start, btc_early, btc_late]):
        return 0.5
    return 1.0 if (btc_early > btc_start) == (btc_late > btc_start) else 0.0


# ── Edge detection (parameterized for both strategies) ───────────────────────

def detect_edge_original(btc_start, btc_obs, poly_up):
    move_pct = (btc_obs - btc_start) / btc_start
    our_up = max(0.05, min(0.95, 0.5 + (move_pct * 15)))
    edge_up = our_up - poly_up
    edge_down = (1 - our_up) - (1 - poly_up)
    if edge_up > MIN_EDGE:
        return "UP", edge_up, 1.0
    if edge_down > MIN_EDGE:
        return "DOWN", edge_down, 1.0
    return None, 0.0, 0.0


def detect_edge_optimized(btc_start, btc_obs, poly_up, volatility, acceleration, multi_obs_conf):
    move_pct = (btc_obs - btc_start) / btc_start

    if volatility > 0.0001:
        expected_5m_vol = volatility * math.sqrt(WINDOW_MIN)
        z_score = move_pct / expected_5m_vol if expected_5m_vol > 0 else 0.0
        our_up = 1.0 / (1.0 + math.exp(-z_score * 0.8))
    else:
        our_up = 0.5 + (move_pct * 15)

    our_up = max(0.05, min(0.95, our_up))

    accel_bonus = max(-0.05, min(0.05, acceleration * 5.0))
    if move_pct > 0:
        our_up = min(0.95, our_up + accel_bonus)
    else:
        our_up = max(0.05, our_up - accel_bonus)

    edge_up = our_up - poly_up
    edge_down = (1 - our_up) - (1 - poly_up)

    effective_min = MIN_EDGE * 1.8 if multi_obs_conf < 0.5 else MIN_EDGE

    confidence = multi_obs_conf
    if acceleration > 0:
        confidence = min(1.0, confidence + 0.2)
    if acceleration < -0.0005:
        confidence = max(0.0, confidence - 0.3)

    if edge_up > effective_min:
        return "UP", edge_up, confidence
    if edge_down > effective_min:
        return "DOWN", edge_down, confidence
    return None, 0.0, 0.0


def kelly_size(edge, entry_price, confidence, balance):
    """Kelly-sized position, capped by guardrails."""
    win_prob = max(0.01, min(0.99, entry_price + edge))
    b = (1.0 / entry_price) - 1.0 if entry_price > 0 else 0
    if b <= 0:
        return min(OPT_BASE_SIZE, balance * MAX_TRADE_PCT)

    q = 1 - win_prob
    kelly_f = max(0, (win_prob * b - q) / b)
    frac_kelly = kelly_f * 0.25 * (0.5 + 0.5 * confidence)

    size = OPT_BASE_SIZE + frac_kelly * (OPT_MAX_SIZE - OPT_BASE_SIZE)
    size = max(OPT_BASE_SIZE * 0.5, min(OPT_MAX_SIZE, size))

    # Guardrail: max trade % of current balance
    max_allowed = balance * MAX_TRADE_PCT
    size = min(size, max_allowed)
    return round(size, 2)


# ── Run one strategy ─────────────────────────────────────────────────────────

def run_strategy(markets, btc_df, optimized=False):
    """Run a single strategy over all markets. Returns (trades, equity_curve)."""
    trades = []
    balance = STARTING_BALANCE
    peak = balance
    max_dd = 0.0
    max_dd_pct = 0.0
    total_exposure = 0.0  # running exposure for wallet cap
    consecutive_losses = 0

    for m in sorted(markets, key=lambda x: x.get("endDate", "")):
        end_str = m.get("endDate") or m.get("end_date_iso", "")
        if not end_str:
            continue

        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        start_dt = end_dt - timedelta(minutes=WINDOW_MIN)
        obs_dt = end_dt - timedelta(seconds=OBS_SECONDS)

        btc_start = get_btc_price(btc_df, start_dt)
        btc_obs = get_btc_price(btc_df, obs_dt)
        btc_end = get_btc_price(btc_df, end_dt)
        if not all([btc_start, btc_obs, btc_end]):
            continue

        if OFFLINE or m.get("_synthetic"):
            poly_up = generate_synthetic_poly_price(btc_df, obs_dt, start_dt)
        else:
            token_id = ""
            tokens = m.get("tokens") or m.get("clobTokenIds", [])
            if isinstance(tokens, list) and tokens:
                token_id = tokens[0] if isinstance(tokens[0], str) else tokens[0].get("token_id", "")
            history = fetch_price_history(m.get("id", ""), token_id)
            poly_up = get_poly_price_at(history, obs_dt.timestamp()) or 0.50

        resolved_up = btc_end >= btc_start

        if optimized:
            vol = get_recent_volatility(btc_df, obs_dt)
            accel = get_momentum_acceleration(btc_df, start_dt, obs_dt)
            multi = get_multi_obs_agreement(btc_df, start_dt, obs_dt)

            # Streak filter: skip after 3 consecutive losses
            if consecutive_losses >= 3:
                consecutive_losses = 0
                continue

            side, edge, conf = detect_edge_optimized(btc_start, btc_obs, poly_up, vol, accel, multi)
        else:
            side, edge, conf = detect_edge_original(btc_start, btc_obs, poly_up)
            vol = 0.0
            accel = 0.0

        if side is None:
            continue

        # ── Size with guardrails ──
        if optimized:
            entry = poly_up if side == "UP" else (1 - poly_up)
            trade_size = kelly_size(edge, entry, conf, balance)
        else:
            trade_size = min(ORIG_TRADE_SIZE, balance * MAX_TRADE_PCT)

        # Guardrail: max market exposure (15% of balance)
        max_market = balance * MAX_MARKET_PCT
        trade_size = min(trade_size, max_market)

        # Guardrail: max total wallet exposure (50% of balance)
        max_wallet = balance * MAX_WALLET_PCT
        if total_exposure + trade_size > max_wallet:
            trade_size = max(0, max_wallet - total_exposure)

        # Skip dust
        if trade_size < 0.50 or balance < 1.0:
            continue

        # Can't exceed balance
        trade_size = min(trade_size, balance)

        entry = poly_up if side == "UP" else (1 - poly_up)
        fee = TAKER_FEE * 4 * entry * (1 - entry)
        shares = trade_size / entry if entry > 0 else 0

        won = (side == "UP" and resolved_up) or (side == "DOWN" and not resolved_up)
        pnl = round((shares * 1.0) - trade_size, 4) if won else -trade_size

        # These are 5-min markets: they resolve immediately, so exposure resets
        # (no lingering open positions — each trade is atomic)
        balance += pnl
        peak = max(peak, balance)
        dd = peak - balance
        dd_pct = (dd / peak * 100) if peak > 0 else 0
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)

        if won:
            consecutive_losses = 0
        else:
            consecutive_losses += 1

        trades.append({
            "time": start_dt.strftime("%m-%d %H:%M"),
            "side": side,
            "entry": round(entry, 4),
            "edge_pct": round(edge * 100, 2),
            "trade_size": round(trade_size, 2),
            "won": won,
            "pnl": pnl,
            "balance": round(balance, 2),
            "confidence": round(conf, 2),
            "btc_move": round((btc_end - btc_start) / btc_start * 100, 4),
        })

    return trades, max_dd, max_dd_pct


# ── Comparison output ─────────────────────────────────────────────────────────

def print_comparison(orig_trades, orig_dd, orig_dd_pct, opt_trades, opt_dd, opt_dd_pct):
    def stats(trades):
        if not trades:
            return {"count": 0, "wins": 0, "losses": 0, "win_rate": 0, "pnl": 0,
                    "final": STARTING_BALANCE, "risked": 0, "roi": 0, "avg_size": 0,
                    "largest_win": 0, "largest_loss": 0}
        wins = [t for t in trades if t["won"]]
        losses = [t for t in trades if not t["won"]]
        total_pnl = sum(t["pnl"] for t in trades)
        total_risked = sum(t["trade_size"] for t in trades)
        final = trades[-1]["balance"] if trades else STARTING_BALANCE
        pnls = [t["pnl"] for t in trades]
        return {
            "count": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0,
            "pnl": total_pnl,
            "final": final,
            "risked": total_risked,
            "roi": total_pnl / total_risked * 100 if total_risked else 0,
            "avg_size": total_risked / len(trades) if trades else 0,
            "largest_win": max(pnls) if pnls else 0,
            "largest_loss": min(pnls) if pnls else 0,
        }

    o = stats(orig_trades)
    p = stats(opt_trades)

    # Determine winner
    if p["pnl"] > o["pnl"]:
        winner = "OPTIMIZED"
        margin = p["pnl"] - o["pnl"]
    elif o["pnl"] > p["pnl"]:
        winner = "ORIGINAL"
        margin = o["pnl"] - p["pnl"]
    else:
        winner = "TIE"
        margin = 0

    w = 38  # column width

    print("=" * 80)
    print(f"  HEAD-TO-HEAD: ORIGINAL vs OPTIMIZED | Starting Balance: ${STARTING_BALANCE:.2f}")
    print(f"  BTC 5-Min Markets | Last 24h | Guardrails: {MAX_TRADE_PCT*100:.0f}% trade / {MAX_WALLET_PCT*100:.0f}% wallet / {MAX_MARKET_PCT*100:.0f}% market")
    if OFFLINE:
        print(f"  *** OFFLINE MODE -- synthetic data (seed=42) ***")
    print("=" * 80)

    def row(label, oval, pval, fmt="s", highlight=False):
        if fmt == "$":
            os_val = f"${oval:,.2f}"
            ps_val = f"${pval:,.2f}"
        elif fmt == "%":
            os_val = f"{oval:.1f}%"
            ps_val = f"{pval:.1f}%"
        elif fmt == "d":
            os_val = f"{oval}"
            ps_val = f"{pval}"
        else:
            os_val = f"{oval}"
            ps_val = f"{pval}"

        flag_o = " <--" if highlight and oval > pval else ""
        flag_p = " <--" if highlight and pval > oval else ""
        print(f"  {label:22s} {os_val:>16s}{flag_o:5s}   {ps_val:>16s}{flag_p}")

    print(f"\n  {'':22s} {'ORIGINAL':>16s}        {'OPTIMIZED':>16s}")
    print(f"  {'':22s} {'-'*16:>16s}        {'-'*16:>16s}")
    row("Trades",          o["count"],       p["count"],       "d")
    row("Wins / Losses",   f"{o['wins']}/{o['losses']}", f"{p['wins']}/{p['losses']}")
    row("Win Rate",        o["win_rate"],    p["win_rate"],    "%", highlight=True)
    row("Total P&L",       o["pnl"],         p["pnl"],         "$", highlight=True)
    row("Final Balance",   o["final"],       p["final"],       "$", highlight=True)
    row("ROI",             o["roi"],         p["roi"],         "%", highlight=True)
    row("USDC Risked",     o["risked"],      p["risked"],      "$")
    row("Avg Trade Size",  o["avg_size"],    p["avg_size"],    "$")
    row("Max Drawdown",    orig_dd,          opt_dd,           "$")
    row("Max Drawdown %",  orig_dd_pct,      opt_dd_pct,       "%")
    row("Largest Win",     o["largest_win"], p["largest_win"], "$")
    row("Largest Loss",    o["largest_loss"],p["largest_loss"],"$")

    print(f"\n  {'=' * 76}")
    if winner == "TIE":
        print(f"  RESULT: TIE -- both strategies performed equally")
    else:
        print(f"  WINNER: {winner} by ${margin:.2f}")
        if winner == "OPTIMIZED":
            print(f"  Optimizations: Kelly sizing, vol-adj signal, accel filter, multi-obs, streak")
        else:
            print(f"  Simple flat-$10 approach outperformed the optimized strategy")
    print(f"  {'=' * 76}")

    # Trade log for each
    for label, trades in [("ORIGINAL", orig_trades), ("OPTIMIZED", opt_trades)]:
        if not trades:
            continue
        print(f"\n  --- {label} TRADE LOG (top 15 by |P&L|) ---")
        sorted_t = sorted(trades, key=lambda t: abs(t["pnl"]), reverse=True)
        print(f"  {'Time':14s} {'Side':5s} {'Entry':7s} {'Edge':7s} {'Size':7s} {'Conf':5s} {'BTC':8s} {'Result'}")
        for t in sorted_t[:15]:
            result = f"WIN +${t['pnl']:.2f}" if t["won"] else f"LOSS -${t['trade_size']:.2f}"
            print(
                f"  {t['time']:14s} {t['side']:5s} "
                f"${t['entry']:.3f}  {t['edge_pct']:+5.1f}%  "
                f"${t['trade_size']:5.1f}  {t['confidence']:.1f}  "
                f"{t['btc_move']:+.3f}%  {result}"
            )

    # Equity curve
    print(f"\n  --- EQUITY CURVES ---")
    print(f"  {'Time':14s} {'Original':>12s} {'Optimized':>12s}")
    # Merge timelines
    all_times = sorted(set(
        [t["time"] for t in orig_trades] + [t["time"] for t in opt_trades]
    ))
    o_bal = {t["time"]: t["balance"] for t in orig_trades}
    p_bal = {t["time"]: t["balance"] for t in opt_trades}
    last_o = STARTING_BALANCE
    last_p = STARTING_BALANCE
    step = max(1, len(all_times) // 20)  # show ~20 points
    for i, t in enumerate(all_times):
        last_o = o_bal.get(t, last_o)
        last_p = p_bal.get(t, last_p)
        if i % step == 0 or i == len(all_times) - 1:
            print(f"  {t:14s} ${last_o:>10.2f}  ${last_p:>10.2f}")

    # Save results
    results = {
        "starting_balance": STARTING_BALANCE,
        "guardrails": {"max_trade_pct": MAX_TRADE_PCT, "max_wallet_pct": MAX_WALLET_PCT, "max_market_pct": MAX_MARKET_PCT},
        "original": {"trades": orig_trades, "stats": stats(orig_trades), "max_dd": orig_dd, "max_dd_pct": orig_dd_pct},
        "optimized": {"trades": opt_trades, "stats": stats(opt_trades), "max_dd": opt_dd, "max_dd_pct": opt_dd_pct},
        "winner": winner,
    }
    Path("comparison_results.json").write_text(json.dumps(results, indent=2))
    print(f"\n  Full results saved -> comparison_results.json")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = "OFFLINE (synthetic)" if OFFLINE else "LIVE (API)"
    print(f"\nBTC 5-min Strategy Comparison | ${STARTING_BALANCE:.2f} balance | Mode: {mode}\n")

    if OFFLINE:
        btc_df = generate_synthetic_btc_candles(hours=26)
        markets = generate_synthetic_markets(btc_df, WINDOW_MIN)
    else:
        slugs = generate_slugs_last_24h(WINDOW_MIN)
        print(f"Generated {len(slugs)} slugs to check\n")
        markets = fetch_markets(slugs)

        if not markets:
            print("\nNo markets found. Try --offline for synthetic data.")
            sys.exit(1)

        btc_df = fetch_binance_candles(hours=26)

    print(f"\nRunning ORIGINAL strategy...")
    orig_trades, orig_dd, orig_dd_pct = run_strategy(markets, btc_df, optimized=False)
    print(f"  -> {len(orig_trades)} trades")

    print(f"Running OPTIMIZED strategy...")
    opt_trades, opt_dd, opt_dd_pct = run_strategy(markets, btc_df, optimized=True)
    print(f"  -> {len(opt_trades)} trades\n")

    print_comparison(orig_trades, orig_dd, orig_dd_pct, opt_trades, opt_dd, opt_dd_pct)
