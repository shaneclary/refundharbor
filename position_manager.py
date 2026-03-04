# position_manager.py — trading logic and order intent generation
#
# True proportional copy trading:
#   If a trader allocates X% of their portfolio to a trade,
#   we allocate X% of our current balance to the same trade.
#   All risk limits are dynamic (% of current balance).
#
# Supports both legacy fund-based and new account-based trading.

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from config import (
    COPY_AMOUNT_TIERS,
    COPY_AMOUNT_USDC,
    COPY_STRATEGY,
    FUND_CONFIGS,
    MAX_MARKET_PCT,
    MAX_TRADE_PCT,
    MAX_WALLET_PCT,
    MIN_COPY_USDC,
    STRATEGY_SWITCH_THRESHOLD,
    get_active_wallets,
    get_risk_limits_for_balance,
)
from db import (
    get_account_tradable_balance,
    get_fund_balance,
    get_position,
    get_trader_performance,
    get_trading_profile,
    get_wallet_activity,
    get_wallet_exposure,
)
import mode as mode_module
from portfolio import get_trader_portfolio
from watcher import TradeSignal

# Cache for CLOB balance to avoid spamming API
_clob_balance_cache: dict = {"balance": None, "updated": 0}
CLOB_BALANCE_CACHE_SECONDS = 30  # Refresh every 30 seconds


def _get_clob_balance() -> float:
    """
    Fetch real USDC balance from Polymarket.

    Uses on-chain RPC to check USDC balance directly - more reliable than
    py_clob_client which can have authentication issues.

    CRITICAL: This must return the REAL balance for guardrails to work.
    If balance cannot be fetched, returns 0 to BLOCK all trades.
    """
    import os
    import time

    now = time.time()

    # Return cached value if fresh
    if _clob_balance_cache["balance"] is not None and now - _clob_balance_cache["updated"] < CLOB_BALANCE_CACHE_SECONDS:
        return _clob_balance_cache["balance"]

    try:
        import httpx
        from eth_account import Account

        # Get private key and derive wallet address
        private_key = os.environ.get("POLY_PRIVATE_KEY") or os.environ.get("POLY_WALLET_PRIVATE_KEY", "")
        if not private_key:
            log.error("POLY_PRIVATE_KEY not set - BLOCKING all trades until configured")
            return 0.0

        # Ensure 0x prefix
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        wallet_address = Account.from_key(private_key).address.lower()

        # USDC contract on Polygon
        usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

        # balanceOf(address) function selector + padded address
        data = "0x70a08231" + wallet_address[2:].zfill(64)

        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": usdc_contract, "data": data}, "latest"],
            "id": 1,
        }

        # Try multiple RPC endpoints
        rpcs = [
            "https://polygon.drpc.org",
            "https://polygon-bor-rpc.publicnode.com",
        ]

        balance = 0.0
        for rpc in rpcs:
            try:
                resp = httpx.post(rpc, json=payload, timeout=5.0)
                result = resp.json()
                if "result" in result and result["result"] != "0x":
                    balance = int(result["result"], 16) / 1e6  # USDC has 6 decimals
                    break
            except Exception:
                continue

        # Also check Polymarket proxy wallet if available
        proxy_wallet = os.getenv("POLY_WALLET_ADDRESS", "").lower()
        if proxy_wallet and proxy_wallet.startswith("0x"):
            data = "0x70a08231" + proxy_wallet[2:].zfill(64)
            payload["params"] = [{"to": usdc_contract, "data": data}, "latest"]
            for rpc in rpcs:
                try:
                    resp = httpx.post(rpc, json=payload, timeout=5.0)
                    result = resp.json()
                    if "result" in result and result["result"] != "0x":
                        proxy_balance = int(result["result"], 16) / 1e6
                        balance += proxy_balance
                        break
                except Exception:
                    continue

        _clob_balance_cache["balance"] = balance
        _clob_balance_cache["updated"] = now

        if balance > 0:
            log.info("CLOB balance: $%.2f (used for guardrails)", balance)
        return balance

    except Exception as e:
        log.warning("Error fetching CLOB balance: %s", e)

    # Fallback to cached value or 0
    return _clob_balance_cache["balance"] or 0.0


def get_effective_balance(fund_id: str = "main") -> float:
    """
    Get the effective balance for trading.

    In PAPER mode: returns the fund balance (simulated)
    In GLOBAL/US mode: returns the REAL CLOB balance

    CRITICAL: In live mode, this MUST return real balance for guardrails.
    Returns 0 if balance cannot be fetched (blocks all trades as safety).
    """
    current_mode = mode_module.get_mode()

    if current_mode == "paper":
        return get_fund_balance(fund_id)
    else:
        # Live mode - MUST use real CLOB balance for guardrails
        balance = _get_clob_balance()
        if balance <= 0:
            log.error(
                "⚠️ CLOB balance is $0 or unavailable - ALL TRADES BLOCKED. "
                "Check POLY_PRIVATE_KEY is set correctly."
            )
        else:
            log.debug("Live mode balance: $%.2f", balance)
        return balance

log = logging.getLogger(__name__)

# ── Top performer boost ─────────────────────────────────────────────────────
# The #1 trader by win rate gets a higher wallet allocation cap.
TOP_PERFORMER_WALLET_PCT = 0.50  # 50% for #1 performer
MIN_RESOLVED_FOR_RANK = 5  # need at least 5 resolved trades to qualify

# ── Inactive wallet reallocation ────────────────────────────────────────────
# When a trader is inactive for 7+ minutes, redistribute 30% of their budget
# to active traders. This is a runtime toggle (can be changed via API).
INACTIVE_MINUTES = 7
INACTIVE_REALLOC_PCT = 0.30  # 30% of inactive wallet's budget goes to active traders

_top_performer_cache: dict = {"wallet": None, "updated": 0}
_realloc_enabled: bool = False  # Toggle: redistribute inactive wallet budgets


def is_realloc_enabled() -> bool:
    """Check if inactive wallet reallocation is enabled."""
    return _realloc_enabled


def set_realloc_enabled(enabled: bool) -> bool:
    """Enable or disable inactive wallet reallocation."""
    global _realloc_enabled
    _realloc_enabled = enabled
    log.info("Inactive wallet reallocation %s", "ENABLED" if enabled else "DISABLED")
    return _realloc_enabled


def _get_top_performer() -> str | None:
    """
    Find the trader with the highest win rate (min 5 resolved trades).
    Caches result for 60 seconds to avoid DB spam.
    """
    import time
    now = time.time()

    # Use cache if fresh (within 60 seconds)
    if _top_performer_cache["wallet"] and now - _top_performer_cache["updated"] < 60:
        return _top_performer_cache["wallet"]

    rows = get_trader_performance()
    best_wallet = None
    best_win_rate = 0.0

    for r in rows:
        wins = r["wins"] or 0
        losses = r["losses"] or 0
        resolved = wins + losses
        if resolved < MIN_RESOLVED_FOR_RANK:
            continue
        win_rate = wins / resolved
        if win_rate > best_win_rate:
            best_win_rate = win_rate
            best_wallet = r["trader_wallet"]

    _top_performer_cache["wallet"] = best_wallet
    _top_performer_cache["updated"] = now

    return best_wallet


@dataclass
class OrderIntent:
    """
    A decision to place an order.
    Passed to executor (paper, global, or US).
    """

    market_id: str
    trader_wallet: str  # the wallet we're copying
    token_id: str
    side: str  # "BUY" | "SELL"
    outcome: str = ""  # "Up", "Down", "Yes", "No", etc.
    usdc_amount: float = 0.0  # for buys
    shares_to_sell: float = 0.0  # for sells
    fund_id: str = "main"  # which fund this trade belongs to (legacy)
    account_id: Optional[int] = None  # which account this trade belongs to (new)


def evaluate(
    signal: TradeSignal,
    fund_id: str = "main",
    account_id: Optional[int] = None,
    market: Optional[dict] = None,
) -> Optional[OrderIntent]:
    """
    Evaluate a trade signal and generate an order intent.

    Can operate in two modes:
    1. Legacy fund mode: fund_id is used to look up balance from fund_accounts
    2. Account mode: account_id is used to look up balance from account_balances

    Args:
        market: Optional pre-fetched market cache dict from market_data.fetch_market_data().
                When provided, enables liquidity gating at higher balances.

    Returns None if we should ignore this signal.
    """
    if signal.side == "BUY":
        return _evaluate_buy(signal, fund_id, account_id, market)
    elif signal.side == "SELL":
        return _evaluate_sell(signal, fund_id, account_id)

    log.warning("Unknown signal side: %s", signal.side)
    return None


def evaluate_for_account(signal: TradeSignal, account_id: int, market: Optional[dict] = None) -> Optional[OrderIntent]:
    """
    Evaluate a trade signal for a specific trading account.
    Uses account's trading profile for risk limits.
    """
    return evaluate(signal, fund_id="main", account_id=account_id, market=market)


def _get_tiered_amount(balance: float) -> float:
    """Look up the fixed trade amount for the current balance tier."""
    for threshold, amount in COPY_AMOUNT_TIERS:
        if balance >= threshold:
            return amount
    # Fallback (shouldn't reach here if tiers include 0)
    return COPY_AMOUNT_USDC


def _evaluate_buy(signal: TradeSignal, fund_id: str = "main", account_id: Optional[int] = None, market: Optional[dict] = None) -> Optional[OrderIntent]:
    """
    Generate buy intent.

    Tiered fixed: scales trade size with balance (recommended for small capital)
    Proportional: trade_value / trader_portfolio = X% -> allocate X% of our balance
    Fixed: always COPY_AMOUNT_USDC
    All caps are dynamic % of current balance.

    If account_id is provided, uses account's profile and tradable balance.
    Otherwise falls back to fund-based configuration.
    If market data is provided, applies liquidity gating at higher balances.
    """
    # Get balance and config based on mode
    if account_id is not None:
        # Account mode: use account's tradable balance and profile
        balance = get_account_tradable_balance(account_id)
        profile = get_trading_profile(account_id)
        strategy = profile.get("copy_strategy", COPY_STRATEGY)
        f_max_trade_pct = profile.get("max_trade_pct", MAX_TRADE_PCT)
        f_max_wallet_pct = profile.get("max_wallet_pct", MAX_WALLET_PCT)
        f_max_market_pct = profile.get("max_market_pct", MAX_MARKET_PCT)
    else:
        # Legacy fund mode - use REAL balance in live mode, paper balance in paper mode
        fund_cfg = FUND_CONFIGS.get(fund_id, FUND_CONFIGS["main"])
        balance = get_effective_balance(fund_id)  # FIXED: Uses real CLOB balance in live mode
        strategy = fund_cfg.get("copy_strategy", COPY_STRATEGY)
        f_max_trade_pct = fund_cfg.get("max_trade_pct", MAX_TRADE_PCT)
        f_max_wallet_pct = fund_cfg.get("max_wallet_pct", MAX_WALLET_PCT)
        f_max_market_pct = fund_cfg.get("max_market_pct", MAX_MARKET_PCT)

    if balance <= 0:
        return None

    # ── Dynamic risk scaling: tighten limits as capital grows ──
    # Override profile/fund limits if the balance-based tier is tighter.
    # This prevents large accounts from concentrating too much in one trade/market.
    scaled = get_risk_limits_for_balance(balance)
    f_max_trade_pct = min(f_max_trade_pct, scaled["max_trade_pct"])
    f_max_wallet_pct = min(f_max_wallet_pct, scaled["max_wallet_pct"])
    f_max_market_pct = min(f_max_market_pct, scaled["max_market_pct"])

    # ── Strategy switch advisory ──
    if balance >= STRATEGY_SWITCH_THRESHOLD and strategy == "tiered_fixed":
        log.info(
            "[%s] Balance $%.0f exceeds $%.0f — consider switching to 'proportional' "
            "strategy for smoother scaling",
            fund_id, balance, STRATEGY_SWITCH_THRESHOLD,
        )

    # ── Determine copy amount ──
    if strategy == "tiered_fixed":
        buy_amount = _get_tiered_amount(balance)
    elif strategy == "proportional":
        buy_amount = _proportional_size(signal, balance)
    else:
        buy_amount = COPY_AMOUNT_USDC

    if buy_amount is None or buy_amount < MIN_COPY_USDC:
        return None

    # Cap 1: max single trade
    max_trade = balance * f_max_trade_pct
    if buy_amount > max_trade:
        buy_amount = max_trade

    # Cap 2: max per wallet (top performer gets 50%, others get fund cap)
    top_performer = _get_top_performer()
    is_top = signal.trader_wallet == top_performer
    wallet_pct = TOP_PERFORMER_WALLET_PCT if is_top else f_max_wallet_pct

    # ── Inactive wallet reallocation ──
    # If enabled, redistribute 30% of inactive wallets' budgets to active traders
    realloc_bonus = 0.0
    active_wallets = get_active_wallets()
    if _realloc_enabled and len(active_wallets) > 1:
        activity = get_wallet_activity(active_wallets, INACTIVE_MINUTES)
        if signal.trader_wallet in activity["active"] and activity["inactive"]:
            # This trader is active, and there are inactive traders
            # Redistribute 30% of each inactive trader's allocation
            inactive_budget_per_wallet = f_max_wallet_pct * INACTIVE_REALLOC_PCT
            total_realloc = inactive_budget_per_wallet * len(activity["inactive"])
            # Split among active traders
            num_active = len(activity["active"])
            realloc_bonus = total_realloc / num_active if num_active > 0 else 0
            wallet_pct += realloc_bonus

    max_wallet = balance * wallet_pct
    wallet_exposure = get_wallet_exposure(signal.trader_wallet, fund_id)
    wallet_remaining = max_wallet - wallet_exposure
    if wallet_remaining <= 0:
        log.debug("[%s] Wallet %s at cap ($%.2f / $%.2f)", fund_id, signal.trader_wallet[:10], wallet_exposure, max_wallet)
        return None
    if buy_amount > wallet_remaining:
        buy_amount = wallet_remaining

    # Cap 3: max per market
    max_market = balance * f_max_market_pct
    position = get_position(signal.market_id, signal.trader_wallet, fund_id)
    market_spent = position["usdc_spent"] if position else 0.0
    market_remaining = max_market - market_spent
    if market_remaining <= 0:
        return None
    if buy_amount > market_remaining:
        buy_amount = market_remaining

    # Cap 4: market liquidity gate (only at higher balances)
    # Rejects trades in thin markets and caps to a safe % of 24h volume.
    from market_data import check_market_liquidity
    ok, reason, buy_amount = check_market_liquidity(market, balance, buy_amount)
    if not ok:
        log.info("[%s] SKIP %s — %s", fund_id, signal.market_id[:12], reason)
        return None

    # Final minimum check after all caps
    if buy_amount < MIN_COPY_USDC:
        return None

    # ── Log ──
    trader_portfolio = get_trader_portfolio(signal.trader_wallet)
    trade_pct = ((signal.usdc_value or 0) / trader_portfolio * 100) if trader_portfolio > 0 else 0
    alloc_pct = buy_amount / balance * 100

    top_tag = " [#1]" if is_top else ""
    realloc_tag = f" [+{realloc_bonus*100:.0f}% realloc]" if realloc_bonus > 0 else ""
    log.info(
        "[%s] BUY %s | $%.2f (%.2f%% of bal) | their $%.0f (%.2f%% of $%.0fk) | wallet %s%s%s $%.0f/$%.0f",
        fund_id,
        signal.market_id[:12],
        buy_amount,
        alloc_pct,
        signal.usdc_value or 0,
        trade_pct,
        trader_portfolio / 1000,
        signal.trader_wallet[:8],
        top_tag,
        realloc_tag,
        wallet_exposure + buy_amount,
        max_wallet,
    )

    return OrderIntent(
        market_id=signal.market_id,
        trader_wallet=signal.trader_wallet,
        token_id=signal.token_id,
        side="BUY",
        outcome=signal.outcome,
        usdc_amount=buy_amount,
        fund_id=fund_id,
        account_id=account_id,
    )


def _proportional_size(signal: TradeSignal, balance: float) -> Optional[float]:
    """
    True proportional sizing.

    If a trader with a $200k portfolio puts $2k into a trade (1%),
    we put 1% of our balance into it.

    trade_pct = trade_usdc / trader_portfolio
    our_amount = trade_pct * our_balance
    """
    trade_value = signal.usdc_value
    if not trade_value or trade_value <= 0:
        return None

    trader_portfolio = get_trader_portfolio(signal.trader_wallet)
    if trader_portfolio <= 0:
        # Fallback: if we don't know their portfolio, use fixed amount
        log.debug("No portfolio data for %s, using fixed amount", signal.trader_wallet[:10])
        return COPY_AMOUNT_USDC

    trade_pct = trade_value / trader_portfolio
    our_amount = trade_pct * balance

    return our_amount


def _evaluate_sell(signal: TradeSignal, fund_id: str = "main", account_id: Optional[int] = None) -> Optional[OrderIntent]:
    """
    Generate sell intent — mirror the trader's sell proportionally.
    """
    position = get_position(signal.market_id, signal.trader_wallet, fund_id)
    if not position:
        return None

    our_shares = position["shares"]

    if signal.shares and signal.shares > 0:
        shares_to_sell = min(signal.shares, our_shares)
    else:
        shares_to_sell = our_shares

    if shares_to_sell < 0.0001:
        return None

    context = f"account={account_id}" if account_id else f"fund={fund_id}"
    log.info(
        "[%s] SELL %s | %.4f shares (%.0f%% of position) | wallet %s",
        context,
        signal.market_id[:12],
        shares_to_sell,
        (shares_to_sell / our_shares * 100) if our_shares > 0 else 0,
        signal.trader_wallet[:8],
    )

    return OrderIntent(
        market_id=signal.market_id,
        trader_wallet=signal.trader_wallet,
        token_id=signal.token_id,
        side="SELL",
        outcome=signal.outcome,
        shares_to_sell=shares_to_sell,
        fund_id=fund_id,
        account_id=account_id,
    )
