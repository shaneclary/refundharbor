# executor_us.py — order execution for Polymarket US (CFTC-regulated)
#
# ── HOW POLYMARKET US AUTH WORKS ────────────────────────────────────────────
#
# The US version is intermediated — you don't sign orders directly with your
# private key like on global Polymarket. Instead:
#
#   1. You connect MetaMask on polymarket.com (US portal)
#   2. Polymarket issues you API credentials tied to your KYC'd account
#   3. Orders are placed via HTTPS API calls authenticated with those credentials
#   4. Your broker/FCM handles clearing — you don't touch private keys for orders
#
# WHAT YOU NEED (fill these into .env when US access is granted):
#   POLY_US_API_KEY        — from polymarket.com → Settings → API
#   POLY_US_API_SECRET     — same
#   POLY_US_API_PASSPHRASE — same
#   POLY_US_WALLET_ADDRESS — your MetaMask wallet address (0x...) — NOT private key
#
# WHAT YOU DO NOT NEED for US:
#   POLY_PRIVATE_KEY — not required, FCM handles signing
#
# ── ENDPOINTS ───────────────────────────────────────────────────────────────
# Polymarket US uses the same CLOB infrastructure but behind a different
# auth layer. Endpoint base may differ — confirm at:
#   https://docs.polymarket.com  (check for US-specific base URL when live)
#
# As of Feb 2026, full US API docs are pending. This file is structured to
# slot in the correct calls as soon as docs are published.
# ────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import os

import httpx

from db import delete_position, get_position, upsert_position
from position_manager import OrderIntent
from watcher import TradeSignal

log = logging.getLogger(__name__)

# ── Will be confirmed when US API docs are published ────────────────────────
US_CLOB_HOST = os.getenv("POLY_US_CLOB_HOST", "https://clob.polymarket.com")


def _auth_headers() -> dict[str, str]:
    """
    Build auth headers for Polymarket US API.
    Same header format as global — key/secret/passphrase based.
    Confirmed working for global; US may add an additional KYC token header.
    Update here if Polymarket US requires extra headers.
    """
    return {
        "POLY-API-KEY":        os.environ["POLY_US_API_KEY"],
        "POLY-API-SECRET":     os.environ["POLY_US_API_SECRET"],
        "POLY-API-PASSPHRASE": os.environ["POLY_US_API_PASSPHRASE"],
        "Content-Type":        "application/json",
    }


def _place_market_order(token_id: str, side: str, amount: float) -> dict:
    """
    POST a market order to Polymarket US CLOB.
    `amount` = USDC for buys, shares for sells.

    TODO: Confirm exact request body schema from US API docs when published.
    Global schema shown here — likely identical but verify.
    """
    payload = {
        "side":     side,            # "BUY" | "SELL"
        "tokenID":  token_id,
        "amount":   amount,
        "type":     "MARKET",
    }
    with httpx.Client() as client:
        r = client.post(
            f"{US_CLOB_HOST}/order",
            headers=_auth_headers(),
            json=payload,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()


def execute(intent: OrderIntent, signal: TradeSignal | None = None) -> bool:
    """
    Place a live order via Polymarket US (FCM-intermediated).
    Same signature as executor.py — drop-in replacement.
    """
    try:
        if intent.side == "BUY":
            resp = _place_market_order(intent.token_id, "BUY", intent.usdc_amount)

            # Parse fill from response
            # TODO: confirm exact response field names from US API docs
            shares_filled = float(resp.get("size", 0) or resp.get("filled", 0))
            if shares_filled <= 0:
                log.warning("BUY returned 0 shares for market %s", intent.market_id)
                return False

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
            log.info("✅ US BUY %.4f shares | market=%s", shares_filled, intent.market_id)
            return True

        elif intent.side == "SELL":
            resp = _place_market_order(intent.token_id, "SELL", intent.shares_to_sell)

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
            log.info("✅ US SELL %.4f shares | market=%s", intent.shares_to_sell, intent.market_id)
            return True

        else:
            log.error("❌ Unknown intent side: %s", intent.side)
            return False

    except Exception as e:
        log.error("❌ US order failed for market %s: %s", intent.market_id, e)
        return False
