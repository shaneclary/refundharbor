# resolver.py — market resolution monitor
#
# Checks open positions against the Polymarket CLOB API to detect
# resolved markets. When a market resolves, winning shares pay $1,
# losing shares pay $0. This module auto-settles positions and
# tracks realized P&L.
#
# These traders (vidarx, Square-Guy, etc.) hold through resolution
# rather than selling — so the watcher never sees a SELL signal.
# This loop fills that gap.

from __future__ import annotations

import asyncio
import logging

import httpx

from config import POLYMARKET_CLOB, RESOLUTION_CHECK_INTERVAL
from db import (
    delete_position,
    distribute_profit,
    get_all_positions,
    get_fund_balance,
    log_trade,
    update_fund_balance,
)

log = logging.getLogger(__name__)


def _fetch_market(market_id: str) -> dict | None:
    """Fetch market data from CLOB API. Returns None on failure."""
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(f"{POLYMARKET_CLOB}/markets/{market_id}")
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as e:
        log.debug("CLOB fetch failed for %s: %s", market_id[:12], e)
        return None


def _get_payouts(market_data: dict) -> dict[str, float]:
    """
    Build token_id → payout map from CLOB market data.

    Tokens array looks like:
      [{"token_id": "801...", "outcome": "Up", "winner": false, "price": 0},
       {"token_id": "996...", "outcome": "Down", "winner": true, "price": 1}]

    Winner pays $1/share, loser pays $0.
    """
    payouts = {}
    for token in market_data.get("tokens", []):
        tid = token.get("token_id", "")
        if tid:
            payouts[tid] = 1.0 if token.get("winner", False) else 0.0
    return payouts


def _get_outcomes(market_data: dict) -> dict[str, str]:
    """Build token_id → outcome name map (e.g. "Up", "Down", "Yes", "No")."""
    outcomes = {}
    for token in market_data.get("tokens", []):
        tid = token.get("token_id", "")
        if tid:
            outcomes[tid] = token.get("outcome", "")
    return outcomes


async def resolution_loop() -> None:
    """
    Background loop: check open positions for resolved markets.

    Runs every RESOLUTION_CHECK_INTERVAL seconds. For each resolved
    market, settles all our positions, credits/debits balance, and
    logs the resolution.
    """
    log.info("Resolution monitor started (checking every %ds)", RESOLUTION_CHECK_INTERVAL)

    while True:
        await asyncio.sleep(RESOLUTION_CHECK_INTERVAL)

        positions = get_all_positions()
        if not positions:
            continue

        # Deduplicate: one API call per market, not per position
        unique_markets = {pos["market_id"] for pos in positions}

        for market_id in unique_markets:
            market_data = _fetch_market(market_id)
            if not market_data:
                continue

            # Only process closed markets
            if not market_data.get("closed", False):
                continue

            # Check if resolution data is available (tokens have winner field)
            tokens = market_data.get("tokens", [])
            has_winner = any(t.get("winner", False) for t in tokens)
            if not has_winner:
                # Market is closed but not yet resolved (still settling)
                continue

            payouts = _get_payouts(market_data)
            outcomes = _get_outcomes(market_data)
            question = market_data.get("question", market_id[:20])

            # Settle every position we hold in this market
            for pos in positions:
                if pos["market_id"] != market_id:
                    continue

                fund_id = pos.get("fund_id", "main")
                payout_per_share = payouts.get(pos["token_id"], 0.0)
                payout = pos["shares"] * payout_per_share
                pnl = payout - pos["usdc_spent"]

                won = payout_per_share > 0
                outcome = "WIN" if won else "LOSS"

                # Credit payout directly to the fund that owns this position
                balance = get_fund_balance(fund_id)
                update_fund_balance(
                    fund_id,
                    balance + payout,
                    trade_count_delta=1,
                    pnl_delta=pnl,
                )

                # Log resolution as a trade
                pos_outcome = outcomes.get(pos["token_id"], pos.get("outcome", ""))
                log_trade(
                    market_id=pos["market_id"],
                    trader_wallet=pos["trader_wallet"],
                    token_id=pos["token_id"],
                    side="RESOLVE",
                    shares=pos["shares"],
                    usdc_amount=payout,
                    price=payout_per_share,
                    mode="paper",
                    success=True,
                    outcome=pos_outcome,
                    fund_id=fund_id,
                )

                # Remove the closed position
                delete_position(pos["market_id"], pos["trader_wallet"], fund_id)

                log.info(
                    "[%s] RESOLVED %s | %s | %s | %.4f shares -> $%.2f (%s$%.2f)",
                    fund_id,
                    question[:40],
                    pos["trader_wallet"][:10],
                    outcome,
                    pos["shares"],
                    payout,
                    "+" if pnl >= 0 else "",
                    pnl,
                )

                # Distribute share of profit to allocation funds
                if fund_id == "main" and pnl > 0:
                    distribute_profit(pnl)
