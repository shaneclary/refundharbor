# wallet.py — wallet utilities
#
# Derives wallet address from private key and queries USDC balance
# on Polygon via RPC. No web3 dependency — uses direct JSON-RPC calls.

from __future__ import annotations

import logging
import os

import httpx

log = logging.getLogger(__name__)

# Polygon USDC.e contract (used by Polymarket CLOB)
USDC_CONTRACT = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
USDC_DECIMALS = 6

# Public Polygon RPC endpoints (fallback chain)
POLYGON_RPCS = [
    "https://polygon-rpc.com",
    "https://rpc.ankr.com/polygon",
]


def get_wallet_address() -> str | None:
    """Derive wallet address from POLY_PRIVATE_KEY. Returns None if not set."""
    private_key = os.getenv("POLY_PRIVATE_KEY", "").strip()
    if not private_key:
        return None

    try:
        from eth_account import Account
        return Account.from_key(private_key).address
    except Exception as e:
        log.warning("Could not derive wallet address: %s", e)
        return None


def get_usdc_balance(address: str) -> float | None:
    """
    Query USDC.e balance on Polygon via JSON-RPC.
    Returns balance in USDC (float) or None on failure.
    """
    if not address:
        return None

    # balanceOf(address) = 0x70a08231 + address padded to 32 bytes
    padded = address[2:].lower().zfill(64)
    call_data = f"0x70a08231{padded}"

    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": USDC_CONTRACT, "data": call_data}, "latest"],
        "id": 1,
    }

    for rpc_url in POLYGON_RPCS:
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(rpc_url, json=payload)
                result = resp.json().get("result", "0x0")
                balance_raw = int(result, 16)
                return balance_raw / (10 ** USDC_DECIMALS)
        except Exception as e:
            log.debug("RPC %s failed: %s", rpc_url, e)
            continue

    log.warning("All Polygon RPCs failed for balance query")
    return None


def get_wallet_status() -> dict:
    """
    Get full wallet status: address, balance, and readiness.
    Works in both paper and live modes.
    """
    address = get_wallet_address()
    balance = None

    if address:
        balance = get_usdc_balance(address)

    return {
        "address": address,
        "address_short": f"{address[:6]}...{address[-4:]}" if address else None,
        "usdc_balance": round(balance, 2) if balance is not None else None,
        "connected": address is not None,
    }
