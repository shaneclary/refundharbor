# executor.py — order execution for Polymarket Global (non-US)
#
# Uses py-clob-client for EIP-712 signed orders on Polygon.
#
# Required env vars:
#   POLY_PRIVATE_KEY     — hot wallet private key (0x...)
#   POLY_API_KEY         — from create_or_derive_api_creds() (auto-generated on first run)
#   POLY_API_SECRET      — same
#   POLY_API_PASSPHRASE  — same

from __future__ import annotations

import logging
import os

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import MarketOrderArgs, OrderType

from db import delete_position, get_position, log_trade, upsert_position
from position_manager import OrderIntent
from watcher import TradeSignal

log = logging.getLogger(__name__)

CLOB_HOST = os.getenv("POLY_CLOB_HOST", "https://clob.polymarket.com")
CHAIN_ID = 137  # Polygon mainnet

_client: ClobClient | None = None


def _get_client() -> ClobClient:
    """Lazy-init the CLOB client (needs env vars set)."""
    global _client
    if _client is not None:
        return _client

    # Check both possible env var names
    private_key = os.environ.get("POLY_PRIVATE_KEY") or os.environ.get("POLY_WALLET_PRIVATE_KEY")
    if not private_key:
        raise ValueError("POLY_PRIVATE_KEY not set - cannot initialize CLOB client")

    # Ensure 0x prefix
    if not private_key.startswith("0x"):
        private_key = "0x" + private_key

    _client = ClobClient(
        host=CLOB_HOST,
        key=private_key,
        chain_id=CHAIN_ID,
    )

    # Set up L2 API credentials (auto-derives from private key if not set)
    api_key = os.getenv("POLY_API_KEY")
    api_secret = os.getenv("POLY_API_SECRET")
    api_passphrase = os.getenv("POLY_API_PASSPHRASE")

    if api_key and api_secret and api_passphrase:
        from py_clob_client.clob_types import ApiCreds
        _client.set_api_creds(ApiCreds(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
        ))
        log.info("CLOB client initialized with provided API creds")
    else:
        creds = _client.create_or_derive_api_creds()
        _client.set_api_creds(creds)
        log.info("CLOB client initialized — derived API creds from private key")
        log.info("Save these to .env to avoid re-deriving:")
        log.info("  POLY_API_KEY=%s", creds.api_key)
        log.info("  POLY_API_SECRET=%s", creds.api_secret)
        log.info("  POLY_API_PASSPHRASE=%s", creds.api_passphrase)

    return _client


def execute(intent: OrderIntent, signal: TradeSignal | None = None) -> bool:
    """
    Place a live order via Polymarket Global (direct wallet signing).

    Uses Fill-Or-Kill (FOK) market orders for immediate execution.
    """
    try:
        client = _get_client()

        if intent.side == "BUY":
            order = client.create_market_order(MarketOrderArgs(
                token_id=intent.token_id,
                amount=intent.usdc_amount,
                side="BUY",
            ))
            resp = client.post_order(order, OrderType.FOK)

            if not resp or not resp.get("success"):
                log.warning("BUY order rejected: %s | market=%s", resp, intent.market_id[:12])
                log_trade(intent.market_id, intent.trader_wallet, intent.token_id,
                          "BUY", 0, intent.usdc_amount, mode="global", success=False,
                          outcome=intent.outcome)
                return False

            # Parse fill — response contains matched trades
            trades_matched = resp.get("matched", []) or resp.get("trades", [])
            shares_filled = sum(float(t.get("size", 0)) for t in trades_matched) if trades_matched else 0
            avg_price = intent.usdc_amount / shares_filled if shares_filled > 0 else 0

            if shares_filled <= 0:
                log.warning("BUY filled 0 shares for market %s", intent.market_id[:12])
                return False

            # Update position
            position = get_position(intent.market_id, intent.trader_wallet)
            upsert_position(
                market_id=intent.market_id,
                trader_wallet=intent.trader_wallet,
                token_id=intent.token_id,
                side="BUY",
                shares=(position["shares"] if position else 0) + shares_filled,
                usdc_spent=(position["usdc_spent"] if position else 0) + intent.usdc_amount,
                outcome=intent.outcome,
            )

            log_trade(intent.market_id, intent.trader_wallet, intent.token_id,
                      "BUY", shares_filled, intent.usdc_amount, mode="global",
                      success=True, price=avg_price, outcome=intent.outcome)

            log.info("BUY %.4f shares @ $%.4f | spent $%.2f | market=%s",
                     shares_filled, avg_price, intent.usdc_amount, intent.market_id[:12])
            return True

        elif intent.side == "SELL":
            order = client.create_market_order(MarketOrderArgs(
                token_id=intent.token_id,
                amount=intent.shares_to_sell,
                side="SELL",
            ))
            resp = client.post_order(order, OrderType.FOK)

            if not resp or not resp.get("success"):
                log.warning("SELL order rejected: %s | market=%s", resp, intent.market_id[:12])
                log_trade(intent.market_id, intent.trader_wallet, intent.token_id,
                          "SELL", intent.shares_to_sell, 0, mode="global", success=False,
                          outcome=intent.outcome)
                return False

            # Parse fill
            trades_matched = resp.get("matched", []) or resp.get("trades", [])
            usdc_received = sum(float(t.get("size", 0)) * float(t.get("price", 0))
                                for t in trades_matched) if trades_matched else 0
            avg_price = usdc_received / intent.shares_to_sell if intent.shares_to_sell > 0 else 0

            # Update position
            position = get_position(intent.market_id, intent.trader_wallet)
            if position:
                remaining = position["shares"] - intent.shares_to_sell
                if remaining <= 0.0001:
                    delete_position(intent.market_id, intent.trader_wallet)
                else:
                    sell_pct = intent.shares_to_sell / position["shares"]
                    upsert_position(
                        market_id=intent.market_id,
                        trader_wallet=intent.trader_wallet,
                        token_id=intent.token_id,
                        side=position["side"],
                        shares=remaining,
                        usdc_spent=position["usdc_spent"] * (1 - sell_pct),
                        outcome=intent.outcome,
                    )

            log_trade(intent.market_id, intent.trader_wallet, intent.token_id,
                      "SELL", intent.shares_to_sell, usdc_received, mode="global",
                      success=True, price=avg_price, outcome=intent.outcome)

            log.info("SELL %.4f shares @ $%.4f | received $%.2f | market=%s",
                     intent.shares_to_sell, avg_price, usdc_received, intent.market_id[:12])
            return True

        else:
            log.error("Unknown intent side: %s", intent.side)
            return False

    except Exception as e:
        log.error("Order failed for market %s: %s", intent.market_id[:12], e, exc_info=True)
        return False
