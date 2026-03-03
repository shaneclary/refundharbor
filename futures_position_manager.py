# futures_position_manager.py — BTC-PERP trading logic with leverage-aware risk caps
#
# Evaluates futures trade signals and generates order intents with:
#   - Leverage caps (max 10x even if trader uses higher)
#   - Margin-based risk limits (max 10% per trade, 30% per wallet)
#   - Position sizing based on available margin
#
# Supports copy-trading from Hyperliquid wallets for BTC perpetual futures.

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from db import (
    get_futures_account,
    get_futures_position,
    get_futures_wallet_margin,
)
from futures_watcher import FuturesTradeSignal

log = logging.getLogger(__name__)

# ── Risk Caps ────────────────────────────────────────────────────────────────

FUTURES_MAX_LEVERAGE = 10.0  # Cap even if trader uses higher
FUTURES_MAX_MARGIN_PCT = 0.10  # Max 10% of balance per trade
FUTURES_MAX_WALLET_MARGIN_PCT = 0.30  # Max 30% margin per tracked wallet
FUTURES_MIN_MARGIN_USDC = 5.0  # Minimum margin for a trade


@dataclass
class FuturesOrderIntent:
    """
    A decision to execute a futures order.
    Passed to futures_paper_trader (or live executor).
    """

    symbol: str  # "BTC"
    trader_wallet: str  # the wallet we're copying
    side: str  # "LONG" | "SHORT" | "CLOSE_LONG" | "CLOSE_SHORT"
    size_usd: float  # Position size in USD
    size_btc: float  # Position size in BTC
    leverage: float  # Leverage to use (capped)
    entry_price: float  # Expected entry price
    margin_required: float  # USDC margin required
    account_id: int = 1


def evaluate_futures(
    signal: FuturesTradeSignal, account_id: int = 1
) -> Optional[FuturesOrderIntent]:
    """
    Evaluate a futures trade signal and generate an order intent.

    Applies risk caps:
        - Max leverage: 10x (capped from signal)
        - Max margin per trade: 10% of balance
        - Max margin per wallet: 30% of balance

    Returns None if we should skip this signal.
    """
    # Handle different signal types
    if signal.side in ("LONG", "SHORT"):
        return _evaluate_futures_open(signal, account_id)
    elif signal.side in ("CLOSE_LONG", "CLOSE_SHORT"):
        return _evaluate_futures_close(signal, account_id)
    else:
        log.warning("Unknown futures signal side: %s", signal.side)
        return None


def _evaluate_futures_open(
    signal: FuturesTradeSignal, account_id: int
) -> Optional[FuturesOrderIntent]:
    """
    Evaluate an open position signal (LONG or SHORT).
    """
    # Get account balance
    account = get_futures_account(account_id)
    balance = account.get("balance_usdc", 0)
    current_margin_used = account.get("margin_used", 0)

    available_balance = balance - current_margin_used
    if available_balance <= FUTURES_MIN_MARGIN_USDC:
        log.debug("Insufficient available balance for futures: $%.2f", available_balance)
        return None

    # Cap leverage
    leverage = min(signal.leverage, FUTURES_MAX_LEVERAGE)
    if leverage <= 0:
        leverage = 1.0

    # Calculate position size based on signal
    signal_notional = signal.size * signal.price  # USD value of trader's position
    signal_margin = signal_notional / signal.leverage if signal.leverage > 0 else signal_notional

    # Scale our position: use same margin percentage as trader
    # (We're copying their conviction level)
    # But cap to our risk limits
    our_margin = signal_margin

    # Cap 1: Max margin per trade (10% of balance)
    max_trade_margin = balance * FUTURES_MAX_MARGIN_PCT
    if our_margin > max_trade_margin:
        our_margin = max_trade_margin

    # Cap 2: Max margin per wallet (30% of balance)
    wallet_margin = get_futures_wallet_margin(signal.trader_wallet, account_id)
    max_wallet_margin = balance * FUTURES_MAX_WALLET_MARGIN_PCT
    wallet_remaining = max_wallet_margin - wallet_margin

    if wallet_remaining <= 0:
        log.debug(
            "Wallet %s at margin cap ($%.2f / $%.2f)",
            signal.trader_wallet[:10],
            wallet_margin,
            max_wallet_margin,
        )
        return None

    if our_margin > wallet_remaining:
        our_margin = wallet_remaining

    # Cap 3: Don't exceed available balance
    if our_margin > available_balance:
        our_margin = available_balance

    # Final minimum check
    if our_margin < FUTURES_MIN_MARGIN_USDC:
        return None

    # Calculate position size from margin and leverage
    position_notional = our_margin * leverage
    size_btc = position_notional / signal.price if signal.price > 0 else 0

    if size_btc < 0.0001:
        return None

    # Log
    log.info(
        "FUTURES %s %s | $%.2f margin (%.1fx) = $%.2f notional | "
        "their: %.4f BTC @ $%.2f (%.1fx) | wallet %s $%.0f/$%.0f",
        signal.symbol,
        signal.side,
        our_margin,
        leverage,
        position_notional,
        signal.size,
        signal.price,
        signal.leverage,
        signal.trader_wallet[:8],
        wallet_margin + our_margin,
        max_wallet_margin,
    )

    return FuturesOrderIntent(
        symbol=signal.symbol,
        trader_wallet=signal.trader_wallet,
        side=signal.side,
        size_usd=position_notional,
        size_btc=size_btc,
        leverage=leverage,
        entry_price=signal.price,
        margin_required=our_margin,
        account_id=account_id,
    )


def _evaluate_futures_close(
    signal: FuturesTradeSignal, account_id: int
) -> Optional[FuturesOrderIntent]:
    """
    Evaluate a close position signal (CLOSE_LONG or CLOSE_SHORT).
    """
    # Check if we have a position to close
    position = get_futures_position(signal.symbol, signal.trader_wallet, account_id)
    if not position:
        log.debug(
            "No position to close for %s %s",
            signal.symbol,
            signal.trader_wallet[:10],
        )
        return None

    # Determine if the close signal matches our position
    expected_side = "LONG" if signal.side == "CLOSE_LONG" else "SHORT"
    if position["side"] != expected_side:
        log.debug(
            "Close signal mismatch: have %s, signal is %s",
            position["side"],
            signal.side,
        )
        return None

    # Close with same size as our position (proportional close could be added later)
    size_btc = position["size"]
    size_usd = size_btc * signal.price

    log.info(
        "FUTURES %s %s | closing %.4f BTC @ $%.2f | margin released: $%.2f",
        signal.symbol,
        signal.side,
        size_btc,
        signal.price,
        position["margin_used"],
    )

    return FuturesOrderIntent(
        symbol=signal.symbol,
        trader_wallet=signal.trader_wallet,
        side=signal.side,
        size_usd=size_usd,
        size_btc=size_btc,
        leverage=position["leverage"],
        entry_price=signal.price,  # This is the exit price
        margin_required=0,  # No additional margin for closes
        account_id=account_id,
    )


def calculate_liquidation_price(
    entry_price: float,
    leverage: float,
    side: str,
    maintenance_margin_pct: float = 0.005,  # 0.5% maintenance margin
) -> float:
    """
    Calculate approximate liquidation price.

    For LONG: liquidation when price drops such that losses = margin
    For SHORT: liquidation when price rises such that losses = margin

    Simplified formula (ignoring funding rates):
        LONG:  liq_price = entry * (1 - 1/leverage + maintenance)
        SHORT: liq_price = entry * (1 + 1/leverage - maintenance)
    """
    if leverage <= 0:
        leverage = 1.0

    if side == "LONG":
        return entry_price * (1 - (1 / leverage) + maintenance_margin_pct)
    else:  # SHORT
        return entry_price * (1 + (1 / leverage) - maintenance_margin_pct)
