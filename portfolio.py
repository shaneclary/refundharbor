# portfolio.py — estimate trader portfolio sizes from Polymarket API
#
# Fetches each tracked wallet's current positions to determine their
# total portfolio value. Used by position_manager to calculate true
# proportional trade sizing.

from __future__ import annotations

import logging

import httpx

from config import POLYMARKET_DATA_API, get_active_wallets

log = logging.getLogger(__name__)

# Cache: wallet → estimated portfolio value (USD)
_portfolio_cache: dict[str, float] = {}


def get_trader_portfolio(wallet: str) -> float:
    """Get cached portfolio value for a trader. Returns 0 if unknown."""
    return _portfolio_cache.get(wallet, 0.0)


def refresh_all_portfolios() -> None:
    """Fetch portfolio sizes for all tracked wallets from Polymarket."""
    wallets = get_active_wallets()
    log.info("Fetching portfolio sizes for %d wallets...", len(wallets))

    for wallet in wallets:
        try:
            value = _fetch_portfolio_value(wallet)
            _portfolio_cache[wallet] = value
            log.info("  %s... portfolio: $%s", wallet[:10], f"{value:,.2f}")
        except Exception as e:
            log.warning("  %s... failed: %s", wallet[:10], e)
            if wallet not in _portfolio_cache:
                _portfolio_cache[wallet] = 0.0


def _fetch_portfolio_value(wallet: str) -> float:
    """
    Fetch a trader's total portfolio value from Polymarket positions API.

    Uses initialValue (total capital deployed) as the portfolio estimate,
    which represents how much USDC they've put into active positions.
    """
    with httpx.Client(timeout=15) as client:
        resp = client.get(
            f"{POLYMARKET_DATA_API}/positions",
            params={"user": wallet.lower(), "limit": 500, "sizeThreshold": 0},
        )

        if resp.status_code != 200:
            log.warning("Positions API returned %d for %s", resp.status_code, wallet[:10])
            return 0.0

        positions = resp.json()
        if not isinstance(positions, list):
            return 0.0

        # Sum initialValue (total capital deployed into positions)
        total = sum(float(p.get("initialValue", 0)) for p in positions)
        return total
