#!/usr/bin/env python3
# main.py — entry point
# Usage:
#   python main.py           → live trading (real orders)
#   python main.py --paper   → paper trading (simulated fills, no real orders)

import argparse
import asyncio
import logging
import os
import sys

# Ensure emoji output works on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("polybot")

from config import get_active_wallets
from config import FUND_CONFIGS
from config import PAPER_STARTING_BALANCE
from config import USE_WEBSOCKET_PRICES
from db import init_db, init_paper_account, get_paper_balance, seed_wallets
from approval import (
    enqueue_trade,
    expire_old_trades,
    get_approval_mode,
    get_approved_trades,
    init_approval_tables,
    mark_executed,
)
import mode as mode_module
from paper_trader import execute_paper
from portfolio import refresh_all_portfolios
from market_data import fetch_market_data
from position_manager import evaluate, OrderIntent
from reconciler import reconcile
from reserve import reserve_cycling_loop, full_moon_harvest_loop, settlement_loop
from resolver import resolution_loop
from watcher import TradeSignal, start_watchers

# Futures copy-trading imports
from futures_watcher import FuturesTradeSignal, start_futures_watchers
from futures_position_manager import evaluate_futures
from futures_paper_trader import execute_futures_paper, update_unrealized_pnl


def _get_mode() -> str:
    """
    Resolve trading mode. Priority:
      1. POLYMARKET_MODE env var  (paper | global | us)
      2. --paper flag             (legacy shorthand)
    """
    return os.getenv("POLYMARKET_MODE", "paper").lower()


# Pre-load executors so mode switching is instant (no import delay)
_executors: dict = {}


def _load_all_executors():
    """Import all available executors at startup."""
    _executors["paper"] = execute_paper
    try:
        from executor import execute as execute_global
        _executors["global"] = execute_global
    except Exception as e:
        log.info("Global executor not available: %s", e)
    try:
        from executor_us import execute as execute_us
        _executors["us"] = execute_us
    except Exception as e:
        log.info("US executor not available: %s", e)


def _validate_config(mode: str) -> None:
    wallets = get_active_wallets()
    if not wallets:
        if mode == "paper":
            log.warning("No wallets configured — watcher will idle until wallets are added via dashboard.")
        else:
            raise SystemExit("No wallets configured. Add wallets via dashboard or config.py first.")

    if mode == "paper":
        pass  # no credentials needed

    elif mode == "global":
        required = ["POLY_API_KEY", "POLY_API_SECRET", "POLY_API_PASSPHRASE", "POLY_PRIVATE_KEY"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise SystemExit(f"GLOBAL mode missing env vars: {missing}")

    elif mode == "us":
        required = ["POLY_US_API_KEY", "POLY_US_API_SECRET", "POLY_US_API_PASSPHRASE"]
        missing = [k for k in required if not os.getenv(k)]
        if missing:
            raise SystemExit(
                f"US mode missing env vars: {missing}\n"
                f"These come from polymarket.com → Settings → API Keys (US portal).\n"
                f"Available once you're approved off the waitlist."
            )

    else:
        raise SystemExit(f"Unknown POLYMARKET_MODE='{mode}'. Use: paper | global | us")

    log.info("Mode: %s | Tracking %d wallets", mode.upper(), len(wallets))


async def process_signals(queue: asyncio.Queue[TradeSignal]) -> None:
    """Consume trade signals and route to the correct executor based on current mode."""
    while True:
        signal = await queue.get()
        try:
            current_mode = mode_module.get_mode()
            approval_mode = get_approval_mode()

            # Fetch market liquidity data (cached, ~0ms if fresh)
            market = await fetch_market_data(signal.market_id)

            if current_mode == "paper":
                # Fan out to all funds — each evaluates independently
                for fund_id in FUND_CONFIGS:
                    intent = evaluate(signal, fund_id=fund_id, market=market)
                    if intent:
                        if approval_mode == "auto":
                            await execute_paper(intent, signal)
                        else:
                            enqueue_trade(intent, signal)
            else:
                # Live modes: only main fund trades
                intent = evaluate(signal, fund_id="main", market=market)
                if intent:
                    if approval_mode == "auto":
                        execute_fn = _executors.get(current_mode)
                        if execute_fn:
                            if asyncio.iscoroutinefunction(execute_fn):
                                await execute_fn(intent, signal)
                            else:
                                execute_fn(intent, signal)
                        else:
                            log.warning("No executor for mode '%s' — skipping trade", current_mode)
                    else:
                        enqueue_trade(intent, signal)
        except Exception as e:
            log.error("Error processing signal for market %s: %s", signal.market_id, e, exc_info=True)
        finally:
            queue.task_done()


async def process_approved_trades() -> None:
    """Background loop: execute approved trades and expire stale ones."""
    timeout_minutes = int(os.getenv("PENDING_TRADE_TIMEOUT_MINUTES", "10"))

    while True:
        await asyncio.sleep(3)

        try:
            # Expire stale pending trades
            expire_old_trades(timeout_minutes)

            # Execute approved trades
            approved = get_approved_trades()
            for trade_data in approved:
                current_mode = mode_module.get_mode()

                # Reconstruct OrderIntent from DB row
                intent = OrderIntent(
                    market_id=trade_data["market_id"],
                    trader_wallet=trade_data["trader_wallet"],
                    token_id=trade_data["token_id"],
                    side=trade_data["side"],
                    outcome=trade_data["outcome"],
                    usdc_amount=trade_data["usdc_amount"],
                    shares_to_sell=trade_data["shares_to_sell"],
                    fund_id=trade_data["fund_id"],
                )

                # Reconstruct minimal TradeSignal for executors
                signal = TradeSignal(
                    trader_wallet=trade_data["trader_wallet"],
                    market_id=trade_data["market_id"],
                    token_id=trade_data["token_id"],
                    side=trade_data["side"],
                    price=trade_data["signal_price"],
                    shares=trade_data["signal_shares"],
                    usdc_value=trade_data["signal_usdc_value"],
                    title=trade_data["signal_title"] or "",
                    timestamp=trade_data["signal_timestamp"] or 0,
                    outcome=trade_data["outcome"],
                )

                try:
                    if current_mode == "paper":
                        await execute_paper(intent, signal)
                    else:
                        execute_fn = _executors.get(current_mode)
                        if execute_fn:
                            if asyncio.iscoroutinefunction(execute_fn):
                                await execute_fn(intent, signal)
                            else:
                                execute_fn(intent, signal)

                    mark_executed(trade_data["id"])
                    log.info("Executed approved trade #%d: %s %s", trade_data["id"], intent.side, intent.market_id[:12])
                except Exception as e:
                    log.error("Failed to execute approved trade #%d: %s", trade_data["id"], e)

        except Exception as e:
            log.error("Error in approved trade processor: %s", e)


async def process_futures_signals(queue: asyncio.Queue[FuturesTradeSignal]) -> None:
    """Consume futures trade signals from Hyperliquid and execute paper trades."""
    while True:
        signal = await queue.get()
        try:
            intent = evaluate_futures(signal)
            if intent:
                await execute_futures_paper(intent, signal)
        except Exception as e:
            log.error("Error processing futures signal: %s", e, exc_info=True)
        finally:
            queue.task_done()


async def futures_pnl_update_loop() -> None:
    """Background loop: update unrealized P&L for all open futures positions."""
    log.info("Futures P&L update loop started (updates every 30s)")
    while True:
        await asyncio.sleep(30)
        try:
            await update_unrealized_pnl()
        except Exception as e:
            log.debug("Futures P&L update error: %s", e)


async def monthly_distribution_loop() -> None:
    """
    Background loop: on the 1st of each month, sweep allocation fund
    balances to their configured wallet addresses.
    """
    from datetime import datetime

    from config import ALLOCATION_FUNDS
    from db import (
        get_fund_balance,
        get_last_distribution,
        is_month_distributed,
        record_allocation,
        record_monthly_distribution,
        update_fund_balance,
    )

    fund_wallets = {f["name"].lower(): f["wallet"] for f in ALLOCATION_FUNDS}
    log.info("Monthly distribution monitor started (sweeps on 1st of month)")

    while True:
        now = datetime.now()

        if now.day == 1:
            current_month = now.strftime("%Y-%m")

            for fund_id, wallet in fund_wallets.items():
                if is_month_distributed(current_month, fund_id):
                    continue

                balance = get_fund_balance(fund_id)

                if balance < 0.01:
                    record_monthly_distribution(current_month, fund_id, 0, wallet or "", status="empty")
                    continue

                if not wallet:
                    log.warning("[%s] No wallet configured — $%.2f held (set ALLOC_%s_WALLET)", fund_id, balance, fund_id.upper())
                    record_monthly_distribution(current_month, fund_id, balance, "", status="held_no_wallet")
                    continue

                # Sweep fund balance to wallet
                update_fund_balance(fund_id, 0.0)
                record_monthly_distribution(current_month, fund_id, balance, wallet)
                record_allocation(
                    fund_name=fund_id.capitalize(),
                    amount=balance,
                    source_market="monthly_sweep",
                    source_pnl=0,
                )

                log.info(
                    "[%s] Monthly sweep: $%.2f → %s",
                    fund_id, balance, wallet[:12] + "...",
                )

        # Check every hour
        await asyncio.sleep(3600)


async def main(mode: str) -> None:
    _validate_config(mode)
    init_db()
    init_approval_tables()

    # Seed tracked_wallets table from config on first run
    from config import TARGET_WALLETS, WALLET_SEED_LABELS
    seed_wallets(TARGET_WALLETS, WALLET_SEED_LABELS)

    # Set initial mode in the shared module (allows runtime toggling)
    mode_module.set_mode(mode)

    # Always init paper account so toggling back to paper works
    init_paper_account(PAPER_STARTING_BALANCE)

    # Pre-load all executors for instant mode switching
    _load_all_executors()

    # Fetch trader portfolio sizes for proportional scaling
    refresh_all_portfolios()

    if mode != "paper":
        reconcile()

    MODE_LABELS = {
        "paper":  "📋 PAPER MODE  — no real orders, simulated fills",
        "global": "💰 LIVE GLOBAL — direct wallet, international Polymarket",
        "us":     "💰 LIVE US     — FCM-intermediated, CFTC-regulated",
    }
    log.info(MODE_LABELS[mode])
    log.info("🚀 Polybot running — watching %d wallets (mode toggle enabled)", len(get_active_wallets()))

    queue: asyncio.Queue[TradeSignal] = asyncio.Queue()
    futures_queue: asyncio.Queue[FuturesTradeSignal] = asyncio.Queue()

    tasks = [
        # Polymarket copy-trading
        start_watchers(queue),
        process_signals(queue),
        process_approved_trades(),  # executes operator-approved trades
        resolution_loop(),  # always run — resolves positions regardless of mode
        monthly_distribution_loop(),  # sweep allocation funds on 1st of month
        reserve_cycling_loop(),  # redistribute reserve to trading pool on schedule
        full_moon_harvest_loop(),  # harvest profits on the full moon (PST)
        settlement_loop(),  # settle allocated funds 3hrs before distribution
        # Futures copy-trading disabled (Polymarket only)
        # start_futures_watchers(futures_queue),
        # process_futures_signals(futures_queue),
        # futures_pnl_update_loop(),
    ]

    # BTC 5-min divergence strategy (autonomous, not copy-trading)
    from config import DIVERGENCE_ENABLED
    if DIVERGENCE_ENABLED:
        from divergence_watcher import divergence_loop
        tasks.append(divergence_loop(mode))
        log.info("BTC 5-min divergence strategy enabled")

    if USE_WEBSOCKET_PRICES:
        from ws_watcher import ws_price_feed
        tasks.append(ws_price_feed())
        log.info("WebSocket price feed enabled")

    # On-chain monitoring for real-time 5-minute market detection
    if os.getenv("POLYGON_WS_RPC"):
        from onchain_watcher import start_onchain_watcher
        tasks.append(start_onchain_watcher(queue))
        log.info("⛓️ On-chain watcher enabled (real-time 5-min market detection)")

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket copy-trade bot")
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Force paper mode (overrides POLYMARKET_MODE env var)",
    )
    args = parser.parse_args()

    mode = "paper" if args.paper else _get_mode()

    try:
        asyncio.run(main(mode=mode))
    except KeyboardInterrupt:
        log.info("Stopped.")
