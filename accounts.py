# accounts.py — Multi-account management
#
# Handles creating, configuring, and switching between trading accounts.
# Each account has its own balance, credentials, and trading profile.

import logging
import re
from typing import Optional

from db import (
    apply_risk_preset,
    create_account,
    delete_account,
    delete_all_account_credentials,
    get_account,
    get_account_balance,
    get_account_by_slug,
    get_account_tradable_balance,
    get_all_accounts,
    get_reserve_config,
    get_trading_profile,
    update_account,
    update_account_balance,
    update_reserve_config,
    update_trading_profile,
)

log = logging.getLogger(__name__)

# Current active account (thread-safe via GIL for simple cases)
_active_account_id: int = 1  # Default to main account


def get_active_account_id() -> int:
    """Get the currently active account ID."""
    return _active_account_id


def set_active_account(account_id: int) -> bool:
    """
    Set the active account.

    Returns True if the account exists and was activated.
    """
    global _active_account_id

    account = get_account(account_id)
    if not account:
        log.warning("Cannot activate non-existent account: %d", account_id)
        return False

    if account["status"] != "active":
        log.warning("Cannot activate disabled account: %d", account_id)
        return False

    _active_account_id = account_id
    log.info("Switched to account: %s (id=%d)", account["name"], account_id)
    return True


def get_active_account() -> dict:
    """Get the currently active account details."""
    account = get_account(_active_account_id)
    if not account:
        # Fallback to main account
        account = get_account(1)
    return account or {"id": 1, "name": "Main", "slug": "main"}


def slugify(name: str) -> str:
    """Convert a name to a URL-safe slug."""
    # Lowercase and replace spaces with hyphens
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "-", slug)
    return slug[:50]  # Max 50 chars


class AccountManager:
    """
    High-level account management operations.
    """

    @staticmethod
    def create(
        name: str,
        description: str = "",
        account_type: str = "trading",
        starting_balance: float = 0,
        risk_level: str = "moderate",
    ) -> dict:
        """
        Create a new trading account.

        Args:
            name: Display name for the account
            description: Optional description
            account_type: Type of account (trading, charity, etc.)
            starting_balance: Initial balance in USDC
            risk_level: Risk preset to apply (conservative/moderate/aggressive)

        Returns:
            The created account dict

        Raises:
            ValueError: If name/slug is invalid or already exists
        """
        slug = slugify(name)
        if not slug:
            raise ValueError("Account name cannot be empty")

        existing = get_account_by_slug(slug)
        if existing:
            raise ValueError(f"Account with slug '{slug}' already exists")

        account_id = create_account(
            name=name,
            slug=slug,
            description=description,
            account_type=account_type,
            starting_balance=starting_balance,
        )

        # Apply risk preset
        if risk_level:
            apply_risk_preset(account_id, risk_level)

        return AccountManager.get_full_account(account_id)

    @staticmethod
    def get_full_account(account_id: int) -> Optional[dict]:
        """
        Get complete account info including balance and profile.
        """
        account = get_account(account_id)
        if not account:
            return None

        balance = get_account_balance(account_id)
        profile = get_trading_profile(account_id)
        reserve = get_reserve_config(account_id)
        tradable = get_account_tradable_balance(account_id)

        return {
            **account,
            "balance_usdc": balance["balance_usdc"],
            "total_pnl": balance["total_pnl"],
            "reserve_balance": balance["reserve_balance"],
            "tradable_balance": tradable,
            "total_trades": balance["total_trades"],
            "profile": {
                "auto_trade_enabled": bool(profile["auto_trade_enabled"]),
                "copy_strategy": profile["copy_strategy"],
                "max_trade_pct": profile["max_trade_pct"],
                "max_wallet_pct": profile["max_wallet_pct"],
                "max_market_pct": profile["max_market_pct"],
                "risk_level": profile["risk_level"],
            },
            "reserve": {
                "reserve_pct": reserve["reserve_pct"],
                "cycling_enabled": bool(reserve["cycling_enabled"]),
                "cycle_schedule": reserve["cycle_schedule"],
                "cycle_pct": reserve["cycle_pct"],
                "last_cycle_at": reserve["last_cycle_at"],
            },
        }

    @staticmethod
    def list_accounts(include_inactive: bool = False) -> list[dict]:
        """List all accounts with balances."""
        accounts = get_all_accounts(include_inactive)
        return [
            AccountManager.get_full_account(a["id"])
            for a in accounts
        ]

    @staticmethod
    def update(
        account_id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[dict]:
        """Update account details."""
        if not update_account(account_id, name=name, description=description, status=status):
            return None
        return AccountManager.get_full_account(account_id)

    @staticmethod
    def delete(account_id: int) -> bool:
        """
        Delete an account and all associated data.

        Cannot delete the main account (id=1).
        """
        if account_id == 1:
            raise ValueError("Cannot delete the main account")

        # Delete credentials first
        delete_all_account_credentials(account_id)

        return delete_account(account_id)

    @staticmethod
    def set_balance(account_id: int, balance: float) -> dict:
        """Set the account balance directly (for resets, deposits, etc.)."""
        update_account_balance(account_id, balance_usdc=balance)
        return get_account_balance(account_id)

    @staticmethod
    def add_funds(account_id: int, amount: float) -> dict:
        """Add funds to an account."""
        current = get_account_balance(account_id)
        new_balance = current["balance_usdc"] + amount
        update_account_balance(account_id, balance_usdc=new_balance)
        log.info("Added $%.2f to account %d (new balance: $%.2f)", amount, account_id, new_balance)
        return get_account_balance(account_id)

    @staticmethod
    def withdraw_funds(account_id: int, amount: float) -> dict:
        """
        Withdraw funds from an account.

        Raises ValueError if insufficient balance.
        """
        current = get_account_balance(account_id)
        tradable = current["balance_usdc"]  # Only withdraw from tradable, not reserve
        if amount > tradable:
            raise ValueError(f"Insufficient balance: ${tradable:.2f} available, ${amount:.2f} requested")

        new_balance = tradable - amount
        update_account_balance(account_id, balance_usdc=new_balance)
        log.info("Withdrew $%.2f from account %d (new balance: $%.2f)", amount, account_id, new_balance)
        return get_account_balance(account_id)

    @staticmethod
    def update_profile(
        account_id: int,
        auto_trade_enabled: Optional[bool] = None,
        copy_strategy: Optional[str] = None,
        max_trade_pct: Optional[float] = None,
        max_wallet_pct: Optional[float] = None,
        max_market_pct: Optional[float] = None,
        risk_level: Optional[str] = None,
    ) -> dict:
        """Update trading profile settings."""
        # If risk_level is set, apply the preset
        if risk_level:
            apply_risk_preset(account_id, risk_level)

        # Apply individual settings (these override preset values if provided)
        update_trading_profile(
            account_id,
            auto_trade_enabled=auto_trade_enabled,
            copy_strategy=copy_strategy,
            max_trade_pct=max_trade_pct,
            max_wallet_pct=max_wallet_pct,
            max_market_pct=max_market_pct,
        )

        return get_trading_profile(account_id)

    @staticmethod
    def update_reserve(
        account_id: int,
        reserve_pct: Optional[float] = None,
        cycling_enabled: Optional[bool] = None,
        cycle_schedule: Optional[str] = None,
        cycle_pct: Optional[float] = None,
    ) -> dict:
        """Update reserve configuration."""
        from config import RESERVE_CYCLE_SCHEDULES

        # Validate schedule
        if cycle_schedule and cycle_schedule not in RESERVE_CYCLE_SCHEDULES:
            raise ValueError(f"Invalid cycle schedule: {cycle_schedule}")

        update_reserve_config(
            account_id,
            reserve_pct=reserve_pct,
            cycling_enabled=cycling_enabled,
            cycle_schedule=cycle_schedule,
            cycle_pct=cycle_pct,
        )

        return get_reserve_config(account_id)


# ── Account-Aware Trading Helpers ─────────────────────────────────────────────


def get_account_for_trading(account_id: Optional[int] = None) -> dict:
    """
    Get account details needed for trading evaluation.

    If account_id is None, uses the active account.
    Returns a dict with balance, profile, and reserve info.
    """
    if account_id is None:
        account_id = get_active_account_id()

    return AccountManager.get_full_account(account_id)


def can_account_trade(account_id: int) -> tuple[bool, str]:
    """
    Check if an account can trade.

    Returns (can_trade, reason).
    """
    account = get_account(account_id)
    if not account:
        return False, "Account not found"

    if account["status"] != "active":
        return False, "Account is not active"

    profile = get_trading_profile(account_id)
    if not profile["auto_trade_enabled"]:
        return False, "Auto-trading is disabled"

    balance = get_account_tradable_balance(account_id)
    if balance <= 0:
        return False, "No tradable balance"

    return True, "OK"


def get_accounts_for_signal() -> list[dict]:
    """
    Get all accounts that should receive trade signals.

    Returns accounts with auto_trade_enabled=True and positive tradable balance.
    """
    accounts = get_all_accounts()
    result = []

    for account in accounts:
        can_trade, reason = can_account_trade(account["id"])
        if can_trade:
            full = AccountManager.get_full_account(account["id"])
            if full:
                result.append(full)

    return result
