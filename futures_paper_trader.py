# futures_paper_trader.py — paper trading execution for BTC perpetual futures
#
# Simulates BTC-PERP order execution without real API calls.
# Tracks virtual margin, positions, and P&L.
#
# Features:
#   - Mark price simulation (with noise)
#   - Slippage modeling
#   - Margin tracking
#   - P&L calculation on position close
#   - Position update with averaging for additions

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from db import (
    delete_futures_position,
    get_futures_account,
    get_futures_position,
    init_futures_account,
    log_futures_trade,
    update_futures_account,
    upsert_futures_position,
)
from futures_position_manager import FuturesOrderIntent, calculate_liquidation_price
from futures_watcher import FuturesTradeSignal, get_btc_mark_price

log = logging.getLogger(__name__)

# Simulation parameters
FUTURES_PAPER_SLIPPAGE_BPS = 5  # 0.05% slippage
FUTURES_PAPER_FILL_DELAY = 0.2  # 200ms delay
FUTURES_STARTING_BALANCE = 1000.0


async def execute_futures_paper(
    intent: FuturesOrderIntent, signal: FuturesTradeSignal
) -> bool:
    """
    Execute a paper futures trade (simulated).

    Simulates:
      - Mark price fetch with noise
      - Slippage
      - Fill delay
      - Margin/balance updates
      - P&L calculation
    """
    account_id = intent.account_id

    # Initialize account if needed
    account = get_futures_account(account_id)
    if account.get("balance_usdc", 0) == 0:
        init_futures_account(FUTURES_STARTING_BALANCE, account_id)
        account = get_futures_account(account_id)
        if account.get("balance_usdc", 0) > 0:
            log.info("[futures] Paper account initialized: $%.2f USDC", account["balance_usdc"])

    # Simulate fill delay
    await asyncio.sleep(FUTURES_PAPER_FILL_DELAY)

    # Re-read account after delay
    account = get_futures_account(account_id)

    try:
        if intent.side in ("LONG", "SHORT"):
            return await _execute_futures_open(intent, signal, account)
        elif intent.side in ("CLOSE_LONG", "CLOSE_SHORT"):
            return await _execute_futures_close(intent, signal, account)
        else:
            log.error("❌ Unknown futures intent side: %s", intent.side)
            return False
    except Exception as e:
        log.error("[futures] Paper trade failed: %s", e, exc_info=True)
        log_futures_trade(
            symbol=intent.symbol,
            trader_wallet=intent.trader_wallet,
            side=intent.side,
            size=intent.size_btc,
            price=intent.entry_price,
            leverage=intent.leverage,
            realized_pnl=0,
            mode="paper",
            success=False,
            account_id=account_id,
        )
        return False


async def _execute_futures_open(
    intent: FuturesOrderIntent, signal: FuturesTradeSignal, account: dict
) -> bool:
    """Execute a simulated futures open position."""
    account_id = intent.account_id
    balance = account.get("balance_usdc", 0)
    margin_used = account.get("margin_used", 0)
    available = balance - margin_used

    # Check margin
    if intent.margin_required > available:
        log.warning(
            "[futures] Insufficient margin: need $%.2f, have $%.2f available",
            intent.margin_required,
            available,
        )
        return False

    # Get fill price with slippage
    fill_price = await _get_fill_price(intent.entry_price, intent.side)

    # Check for existing position
    position = get_futures_position(intent.symbol, intent.trader_wallet, account_id)

    if position:
        # Average into existing position
        if position["side"] != intent.side:
            log.warning(
                "[futures] Cannot add to position with opposite side: have %s, signal %s",
                position["side"],
                intent.side,
            )
            return False

        # Calculate new averaged position
        old_notional = position["size"] * position["entry_price"]
        new_notional = intent.size_btc * fill_price
        total_size = position["size"] + intent.size_btc
        avg_entry = (old_notional + new_notional) / total_size if total_size > 0 else fill_price
        total_margin = position["margin_used"] + intent.margin_required

        # Calculate new liquidation price
        liq_price = calculate_liquidation_price(avg_entry, intent.leverage, intent.side)

        upsert_futures_position(
            symbol=intent.symbol,
            trader_wallet=intent.trader_wallet,
            side=intent.side,
            entry_price=avg_entry,
            size=total_size,
            leverage=intent.leverage,
            margin_used=total_margin,
            liquidation_price=liq_price,
            account_id=account_id,
        )

        log.info(
            "[futures] ADDED to %s: +%.4f BTC @ $%.2f | total: %.4f BTC @ $%.2f | margin: $%.2f",
            intent.side,
            intent.size_btc,
            fill_price,
            total_size,
            avg_entry,
            total_margin,
        )
    else:
        # New position
        liq_price = calculate_liquidation_price(fill_price, intent.leverage, intent.side)

        upsert_futures_position(
            symbol=intent.symbol,
            trader_wallet=intent.trader_wallet,
            side=intent.side,
            entry_price=fill_price,
            size=intent.size_btc,
            leverage=intent.leverage,
            margin_used=intent.margin_required,
            liquidation_price=liq_price,
            account_id=account_id,
        )

        log.info(
            "[futures] OPENED %s: %.4f BTC @ $%.2f (%.1fx) | margin: $%.2f | liq: $%.2f",
            intent.side,
            intent.size_btc,
            fill_price,
            intent.leverage,
            intent.margin_required,
            liq_price,
        )

    # Update account margin
    new_margin_used = margin_used + intent.margin_required
    update_futures_account(
        account_id=account_id,
        margin_used=new_margin_used,
        trade_count_delta=1,
    )

    # Log trade
    log_futures_trade(
        symbol=intent.symbol,
        trader_wallet=intent.trader_wallet,
        side=intent.side,
        size=intent.size_btc,
        price=fill_price,
        leverage=intent.leverage,
        realized_pnl=0,
        mode="paper",
        success=True,
        account_id=account_id,
    )

    return True


async def _execute_futures_close(
    intent: FuturesOrderIntent, signal: FuturesTradeSignal, account: dict
) -> bool:
    """Execute a simulated futures close position."""
    account_id = intent.account_id

    # Get existing position
    position = get_futures_position(intent.symbol, intent.trader_wallet, account_id)
    if not position:
        log.warning("[futures] No position to close for %s %s", intent.symbol, intent.trader_wallet[:10])
        return False

    # Get fill price with slippage
    exit_price = await _get_fill_price(intent.entry_price, intent.side)

    # Calculate P&L
    entry_price = position["entry_price"]
    size = position["size"]
    leverage = position["leverage"]

    if position["side"] == "LONG":
        # Long: profit when price goes up
        price_diff = exit_price - entry_price
    else:
        # Short: profit when price goes down
        price_diff = entry_price - exit_price

    # P&L = (price_diff / entry_price) * notional * leverage
    # Or simply: price_diff * size (in BTC terms, converted to USD)
    pnl = price_diff * size
    pnl_pct = (pnl / position["margin_used"] * 100) if position["margin_used"] > 0 else 0

    # Release margin and apply P&L
    balance = account.get("balance_usdc", 0)
    margin_used = account.get("margin_used", 0)
    margin_released = position["margin_used"]

    new_balance = balance + pnl
    new_margin = max(0, margin_used - margin_released)

    # Delete position
    delete_futures_position(intent.symbol, intent.trader_wallet, account_id)

    # Update account
    update_futures_account(
        account_id=account_id,
        balance_usdc=new_balance,
        margin_used=new_margin,
        pnl_delta=pnl,
        trade_count_delta=1,
    )

    pnl_emoji = "📈" if pnl >= 0 else "📉"
    log.info(
        "[futures] %s CLOSED %s: %.4f BTC @ $%.2f | entry: $%.2f | P&L: $%.2f (%+.1f%%) | balance: $%.2f",
        pnl_emoji,
        position["side"],
        size,
        exit_price,
        entry_price,
        pnl,
        pnl_pct,
        new_balance,
    )

    # Log trade
    log_futures_trade(
        symbol=intent.symbol,
        trader_wallet=intent.trader_wallet,
        side=intent.side,
        size=size,
        price=exit_price,
        leverage=leverage,
        realized_pnl=pnl,
        mode="paper",
        success=True,
        account_id=account_id,
    )

    return True


async def _get_fill_price(reference_price: float, side: str) -> float:
    """
    Get fill price with slippage.

    For opening positions:
        LONG: fill slightly higher (worse for buyer)
        SHORT: fill slightly lower (worse for seller)
    For closing positions:
        CLOSE_LONG: fill slightly lower (worse for seller)
        CLOSE_SHORT: fill slightly higher (worse for buyer)
    """
    # Try to get real mark price
    mark_price = await get_btc_mark_price()
    if mark_price and mark_price > 0:
        # Use mark price with small random noise
        noise = random.uniform(-0.001, 0.001)  # ±0.1%
        base_price = mark_price * (1 + noise)
    else:
        # Fall back to reference price
        base_price = reference_price

    # Apply slippage based on side
    slippage_pct = FUTURES_PAPER_SLIPPAGE_BPS / 10000

    if side in ("LONG", "CLOSE_SHORT"):
        # Buying: fill higher
        return base_price * (1 + slippage_pct)
    else:
        # Selling: fill lower
        return base_price * (1 - slippage_pct)


async def update_unrealized_pnl(account_id: int = 1) -> None:
    """
    Update unrealized P&L for all open positions.
    Called periodically to keep position values current.
    """
    from db import get_all_futures_positions

    positions = get_all_futures_positions(account_id)
    if not positions:
        return

    mark_price = await get_btc_mark_price()
    if not mark_price or mark_price <= 0:
        return

    for pos in positions:
        entry_price = pos["entry_price"]
        size = pos["size"]

        if pos["side"] == "LONG":
            unrealized_pnl = (mark_price - entry_price) * size
        else:
            unrealized_pnl = (entry_price - mark_price) * size

        upsert_futures_position(
            symbol=pos["symbol"],
            trader_wallet=pos["trader_wallet"],
            side=pos["side"],
            entry_price=entry_price,
            size=size,
            leverage=pos["leverage"],
            margin_used=pos["margin_used"],
            unrealized_pnl=unrealized_pnl,
            liquidation_price=pos["liquidation_price"],
            account_id=account_id,
        )
