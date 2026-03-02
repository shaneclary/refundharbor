# paper_trader.py — paper trading execution with simulated fills
#
# Simulates order execution without real API calls.
# Tracks virtual balance and positions.

from __future__ import annotations

import asyncio
import logging
import random

import httpx

from config import (
    FUND_CONFIGS,
    PAPER_FILL_DELAY,
    PAPER_SLIPPAGE_BPS,
    PAPER_STARTING_BALANCE,
    POLYMARKET_CLOB,
)
from db import (
    delete_position,
    distribute_profit,
    get_fund_balance,
    get_position,
    init_paper_account,
    log_trade,
    update_fund_balance,
    upsert_position,
)
from position_manager import OrderIntent
from watcher import TradeSignal

log = logging.getLogger(__name__)


async def execute_paper(intent: OrderIntent, signal: TradeSignal | None = None) -> bool:
    """
    Execute a paper trade (simulated).

    Simulates:
      - Market price fetch
      - Slippage
      - Fill delay
      - Balance updates
    """
    fund_id = intent.fund_id

    # Initialize paper account if needed
    balance = get_fund_balance(fund_id)
    if balance == 0:
        init_paper_account(PAPER_STARTING_BALANCE)
        balance = get_fund_balance(fund_id)
        if balance > 0:
            log.info("[%s] Paper account initialized: $%.2f USDC", fund_id, balance)

    # Simulate fill delay
    await asyncio.sleep(PAPER_FILL_DELAY)

    # Re-read balance after delay (another trade may have executed during sleep)
    balance = get_fund_balance(fund_id)

    try:
        if intent.side == "BUY":
            return await _execute_paper_buy(intent, signal, balance)
        elif intent.side == "SELL":
            return await _execute_paper_sell(intent, signal, balance)
        else:
            log.error("❌ Unknown intent side: %s", intent.side)
            return False
    except Exception as e:
        log.error("[%s] Paper trade failed: %s", fund_id, e)
        log_trade(
            market_id=intent.market_id,
            trader_wallet=intent.trader_wallet,
            token_id=intent.token_id,
            side=intent.side,
            shares=0,
            usdc_amount=intent.usdc_amount if intent.side == "BUY" else 0,
            mode="paper",
            success=False,
            outcome=intent.outcome,
            fund_id=fund_id,
        )
        return False


async def _execute_paper_buy(intent: OrderIntent, signal: TradeSignal | None, balance: float) -> bool:
    """Execute a simulated buy order."""
    fund_id = intent.fund_id

    # Check balance
    if balance < intent.usdc_amount:
        log.warning(
            "[%s] Insufficient paper balance: need $%.2f, have $%.2f",
            fund_id, intent.usdc_amount, balance,
        )
        return False

    # Get current market price
    price = await _get_market_price(intent.token_id, intent.market_id, signal)
    if price is None or price <= 0:
        log.warning("[%s] Could not fetch price for market %s", fund_id, intent.market_id)
        return False

    # Apply slippage (buys get worse price)
    slippage_factor = 1 + (PAPER_SLIPPAGE_BPS / 10000)
    fill_price = price * slippage_factor

    # Calculate shares filled
    shares_filled = intent.usdc_amount / fill_price

    # Update position
    position = get_position(intent.market_id, intent.trader_wallet, fund_id)
    new_shares = (position["shares"] if position else 0) + shares_filled
    new_spent = (position["usdc_spent"] if position else 0) + intent.usdc_amount

    upsert_position(
        market_id=intent.market_id,
        trader_wallet=intent.trader_wallet,
        token_id=intent.token_id,
        side="BUY",
        shares=new_shares,
        usdc_spent=new_spent,
        outcome=intent.outcome,
        fund_id=fund_id,
    )

    # Update balance
    new_balance = balance - intent.usdc_amount
    update_fund_balance(fund_id, new_balance, trade_count_delta=1)

    # Log trade
    log_trade(
        market_id=intent.market_id,
        trader_wallet=intent.trader_wallet,
        token_id=intent.token_id,
        side="BUY",
        shares=shares_filled,
        usdc_amount=intent.usdc_amount,
        price=fill_price,
        mode="paper",
        success=True,
        outcome=intent.outcome,
        fund_id=fund_id,
    )

    log.info(
        "[%s] PAPER BUY: %.4f shares @ $%.4f | spent $%.2f | balance: $%.2f -> $%.2f | market=%s",
        fund_id, shares_filled, fill_price, intent.usdc_amount, balance, new_balance, intent.market_id[:12],
    )

    return True


async def _execute_paper_sell(intent: OrderIntent, signal: TradeSignal | None, balance: float) -> bool:
    """Execute a simulated sell order."""
    fund_id = intent.fund_id

    position = get_position(intent.market_id, intent.trader_wallet, fund_id)
    if not position:
        log.warning("[%s] No position to sell for market %s", fund_id, intent.market_id)
        return False

    if position["shares"] < intent.shares_to_sell:
        log.warning(
            "[%s] Insufficient shares: need %.4f, have %.4f",
            fund_id, intent.shares_to_sell, position["shares"],
        )
        return False

    # Get current market price
    price = await _get_market_price(intent.token_id, intent.market_id, signal)
    if price is None or price <= 0:
        log.warning("[%s] Could not fetch price for market %s", fund_id, intent.market_id)
        return False

    # Apply slippage (sells get worse price)
    slippage_factor = 1 - (PAPER_SLIPPAGE_BPS / 10000)
    fill_price = price * slippage_factor

    # Calculate USDC received
    usdc_received = intent.shares_to_sell * fill_price

    # Calculate P&L
    avg_cost = position["usdc_spent"] / position["shares"] if position["shares"] > 0 else 0
    pnl = (fill_price - avg_cost) * intent.shares_to_sell

    # Update position
    remaining_shares = position["shares"] - intent.shares_to_sell
    if remaining_shares <= 0.0001:
        delete_position(intent.market_id, intent.trader_wallet, fund_id)
        log.info("   [%s] Position closed (full exit)", fund_id)
    else:
        sell_pct = intent.shares_to_sell / position["shares"]
        new_spent = position["usdc_spent"] * (1 - sell_pct)
        upsert_position(
            market_id=intent.market_id,
            trader_wallet=intent.trader_wallet,
            token_id=intent.token_id,
            side=position["side"],
            shares=remaining_shares,
            usdc_spent=new_spent,
            outcome=intent.outcome,
            fund_id=fund_id,
        )

    # Update balance
    new_balance = balance + usdc_received
    update_fund_balance(fund_id, new_balance, trade_count_delta=1, pnl_delta=pnl)

    # Log trade
    log_trade(
        market_id=intent.market_id,
        trader_wallet=intent.trader_wallet,
        token_id=intent.token_id,
        side="SELL",
        shares=intent.shares_to_sell,
        usdc_amount=usdc_received,
        price=fill_price,
        mode="paper",
        success=True,
        outcome=intent.outcome,
        fund_id=fund_id,
    )

    log.info(
        "[%s] PAPER SELL: %.4f shares @ $%.4f | received $%.2f | P&L: $%.2f | balance: $%.2f -> $%.2f | market=%s",
        fund_id, intent.shares_to_sell, fill_price, usdc_received, pnl, balance, new_balance, intent.market_id[:12],
    )

    # Distribute share of profit to allocation funds
    if fund_id == "main" and pnl > 0:
        distribute_profit(pnl)

    return True


async def _get_market_price(token_id: str, market_id: str, signal: TradeSignal | None) -> float | None:
    """
    Get current market price for a token.

    In paper mode, we have a few options:
      1. Use the price from the signal (if available)
      2. Fetch from Polymarket API (real market data)
      3. Simulate a random price

    For realism, we'll prefer signal price, then API, then fallback to simulation.
    """

    # Option 1: Use signal price if available
    if signal and signal.price and signal.price > 0:
        # Add small random noise to simulate market movement
        noise = random.uniform(-0.005, 0.005)  # ±0.5%
        return signal.price * (1 + noise)

    # Option 2: Fetch from Polymarket API (real-time price)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Try to get order book midpoint
            url = f"{POLYMARKET_CLOB}/book"
            params = {"token_id": token_id}
            resp = await client.get(url, params=params)

            if resp.status_code == 200:
                book = resp.json()
                bids = book.get("bids", [])
                asks = book.get("asks", [])

                if bids and asks:
                    best_bid = float(bids[0]["price"])
                    best_ask = float(asks[0]["price"])
                    if best_bid > 0 and best_ask > 0:
                        return (best_bid + best_ask) / 2

    except Exception as e:
        log.debug("Could not fetch market price from API: %s", e)

    # Option 3: Simulate a reasonable price (fallback)
    # Most prediction markets trade in 0.01 - 0.99 range
    simulated_price = random.uniform(0.30, 0.70)
    log.debug("Using simulated price: $%.4f (API unavailable)", simulated_price)
    return simulated_price
