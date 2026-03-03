# reserve.py — Reserve system and cycling scheduler
#
# Manages reserve balances (funds excluded from trading) and
# automated reserve cycling (redistributing reserve back to trading).

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from db import (
    apply_reserve_to_balance,
    cycle_reserve,
    get_account_balance,
    get_all_accounts,
    get_reserve_config,
    mark_reserve_cycled,
    update_account_balance,
    update_reserve_config,
)

log = logging.getLogger(__name__)


class ReserveManager:
    """
    Manages reserve balances for trading accounts.

    Reserve is a percentage of total funds that are excluded from trading.
    This provides a safety buffer and capital preservation mechanism.
    """

    @staticmethod
    def set_reserve_percentage(account_id: int, reserve_pct: float) -> dict:
        """
        Set the reserve percentage for an account.

        Args:
            account_id: Trading account ID
            reserve_pct: Reserve percentage (0-100)

        Returns:
            Updated reserve config
        """
        # Clamp to valid range
        reserve_pct = max(0, min(100, reserve_pct))

        update_reserve_config(account_id, reserve_pct=reserve_pct)

        # Apply the new reserve percentage to the balance
        if reserve_pct > 0:
            apply_reserve_to_balance(account_id)

        config = get_reserve_config(account_id)
        balance = get_account_balance(account_id)

        log.info(
            "Reserve set: account=%d pct=%.1f%% reserve=$%.2f tradable=$%.2f",
            account_id, reserve_pct, balance["reserve_balance"], balance["balance_usdc"]
        )

        return {
            **config,
            "reserve_balance": balance["reserve_balance"],
            "tradable_balance": balance["balance_usdc"],
        }

    @staticmethod
    def configure_cycling(
        account_id: int,
        enabled: bool,
        schedule: str = "daily",
        cycle_pct: float = 10,
    ) -> dict:
        """
        Configure reserve cycling for an account.

        Args:
            account_id: Trading account ID
            enabled: Enable/disable cycling
            schedule: Cycle frequency (disabled, hourly, daily, weekly)
            cycle_pct: Percentage of reserve to cycle each time (1-100)

        Returns:
            Updated reserve config
        """
        from config import RESERVE_CYCLE_SCHEDULES

        if schedule not in RESERVE_CYCLE_SCHEDULES:
            raise ValueError(f"Invalid schedule: {schedule}. Use one of: {RESERVE_CYCLE_SCHEDULES}")

        cycle_pct = max(1, min(100, cycle_pct))

        update_reserve_config(
            account_id,
            cycling_enabled=enabled,
            cycle_schedule=schedule if enabled else "disabled",
            cycle_pct=cycle_pct,
        )

        config = get_reserve_config(account_id)

        log.info(
            "Reserve cycling configured: account=%d enabled=%s schedule=%s pct=%.1f%%",
            account_id, enabled, schedule, cycle_pct
        )

        return config

    @staticmethod
    def trigger_cycle(account_id: int) -> dict:
        """
        Manually trigger a reserve cycle for an account.

        Returns:
            Dict with cycle_amount and new balances
        """
        config = get_reserve_config(account_id)
        before = get_account_balance(account_id)

        if before["reserve_balance"] <= 0:
            return {
                "cycled": False,
                "reason": "No reserve balance to cycle",
                "cycle_amount": 0,
                "reserve_balance": 0,
                "tradable_balance": before["balance_usdc"],
            }

        # Temporarily enable cycling if not enabled
        was_enabled = config["cycling_enabled"]
        if not was_enabled:
            update_reserve_config(account_id, cycling_enabled=True)

        cycle_amount = cycle_reserve(account_id)

        # Restore original setting
        if not was_enabled:
            update_reserve_config(account_id, cycling_enabled=False)

        after = get_account_balance(account_id)

        return {
            "cycled": cycle_amount > 0,
            "cycle_amount": round(cycle_amount, 2),
            "reserve_balance": round(after["reserve_balance"], 2),
            "tradable_balance": round(after["balance_usdc"], 2),
        }

    @staticmethod
    def move_to_reserve(account_id: int, amount: float) -> dict:
        """
        Move funds from trading balance to reserve.

        Args:
            account_id: Trading account ID
            amount: Amount to move to reserve

        Returns:
            Updated balances
        """
        balance = get_account_balance(account_id)

        if amount > balance["balance_usdc"]:
            raise ValueError(f"Insufficient tradable balance: ${balance['balance_usdc']:.2f}")

        new_tradable = balance["balance_usdc"] - amount
        new_reserve = balance["reserve_balance"] + amount

        update_account_balance(
            account_id,
            balance_usdc=new_tradable,
            reserve_balance=new_reserve,
        )

        log.info(
            "Moved to reserve: account=%d amount=$%.2f reserve=$%.2f → $%.2f",
            account_id, amount, balance["reserve_balance"], new_reserve
        )

        return {
            "tradable_balance": round(new_tradable, 2),
            "reserve_balance": round(new_reserve, 2),
        }

    @staticmethod
    def move_from_reserve(account_id: int, amount: float) -> dict:
        """
        Move funds from reserve to trading balance.

        Args:
            account_id: Trading account ID
            amount: Amount to move from reserve

        Returns:
            Updated balances
        """
        balance = get_account_balance(account_id)

        if amount > balance["reserve_balance"]:
            raise ValueError(f"Insufficient reserve: ${balance['reserve_balance']:.2f}")

        new_tradable = balance["balance_usdc"] + amount
        new_reserve = balance["reserve_balance"] - amount

        update_account_balance(
            account_id,
            balance_usdc=new_tradable,
            reserve_balance=new_reserve,
        )

        log.info(
            "Moved from reserve: account=%d amount=$%.2f reserve=$%.2f → $%.2f",
            account_id, amount, balance["reserve_balance"], new_reserve
        )

        return {
            "tradable_balance": round(new_tradable, 2),
            "reserve_balance": round(new_reserve, 2),
        }

    @staticmethod
    def get_status(account_id: int) -> dict:
        """
        Get complete reserve status for an account.
        """
        config = get_reserve_config(account_id)
        balance = get_account_balance(account_id)

        total = balance["balance_usdc"] + balance["reserve_balance"]
        actual_pct = (balance["reserve_balance"] / total * 100) if total > 0 else 0

        return {
            "reserve_pct": config["reserve_pct"],
            "actual_reserve_pct": round(actual_pct, 2),
            "reserve_balance": round(balance["reserve_balance"], 2),
            "tradable_balance": round(balance["balance_usdc"], 2),
            "total_balance": round(total, 2),
            "cycling_enabled": bool(config["cycling_enabled"]),
            "cycle_schedule": config["cycle_schedule"],
            "cycle_pct": config["cycle_pct"],
            "last_cycle_at": config["last_cycle_at"],
        }


def _should_cycle(config: dict) -> bool:
    """Check if an account should cycle based on its schedule."""
    if not config["cycling_enabled"]:
        return False

    schedule = config["cycle_schedule"]
    if schedule == "disabled":
        return False

    last_cycle = config["last_cycle_at"]
    if not last_cycle:
        return True  # Never cycled, do it now

    # Parse last_cycle timestamp
    if isinstance(last_cycle, str):
        try:
            last_cycle = datetime.fromisoformat(last_cycle.replace("Z", "+00:00"))
        except ValueError:
            return True  # Can't parse, cycle now

    now = datetime.now()

    if schedule == "hourly":
        return now - last_cycle >= timedelta(hours=1)
    elif schedule == "daily":
        return now - last_cycle >= timedelta(days=1)
    elif schedule == "weekly":
        return now - last_cycle >= timedelta(weeks=1)

    return False


async def reserve_cycling_loop() -> None:
    """
    Background loop that processes reserve cycling for all accounts.

    Runs every 5 minutes to check for accounts that need cycling.
    """
    log.info("Reserve cycling scheduler started")

    while True:
        try:
            accounts = get_all_accounts()

            for account in accounts:
                account_id = account["id"]
                config = get_reserve_config(account_id)

                if _should_cycle(config):
                    balance = get_account_balance(account_id)
                    if balance["reserve_balance"] > 0:
                        cycle_amount = cycle_reserve(account_id)
                        if cycle_amount > 0:
                            log.info(
                                "Scheduled reserve cycle: account=%d (%s) amount=$%.2f",
                                account_id, account["name"], cycle_amount
                            )

        except Exception as e:
            log.error("Error in reserve cycling loop: %s", e)

        # Check every 5 minutes
        await asyncio.sleep(300)


def get_all_reserve_statuses() -> list[dict]:
    """Get reserve status for all accounts."""
    accounts = get_all_accounts()
    return [
        {
            "account_id": a["id"],
            "account_name": a["name"],
            **ReserveManager.get_status(a["id"]),
        }
        for a in accounts
    ]


# ── FULL MOON HARVEST SCHEDULER ──────────────────────────────────────────────


async def full_moon_harvest_loop() -> None:
    """
    Background loop that checks for full moon transit and triggers harvest.

    Traditional farming approach: profits compound in the pool until
    the full moon, when they are harvested at the moon's peak (transit time).

    Uses NASA/USNO data for precise transit timing - when the moon is at
    its highest point in the sky, the fullest moment of the full moon.

    Runs every 5 minutes to catch the transit window.
    """
    from db import trigger_full_moon_harvest, get_harvest_dashboard
    from full_moon import is_full_moon_day, is_harvest_time, get_harvest_status

    log.info("🌕 Full Moon Harvest scheduler started (using NASA transit data)")

    while True:
        try:
            status = get_harvest_status()

            if status["is_full_moon_day"]:
                # Check if we're at the transit time (moon's peak)
                if is_harvest_time(tolerance_minutes=30):
                    transit = status.get("transit_time", "unknown")
                    log.info(
                        "🌕 Full moon at peak! Transit time: %s - Initiating harvest...",
                        transit
                    )
                    result = trigger_full_moon_harvest()

                    if result["harvested"] and result["total_harvested"] > 0:
                        log.info(
                            "🌾 Harvest complete at moon's zenith: $%.2f distributed",
                            result["total_harvested"]
                        )
                else:
                    transit = status.get("transit_time", "unknown")
                    log.debug(
                        "🌕 Full moon day - waiting for transit at %s PST",
                        transit
                    )
            else:
                # Log moon phase periodically
                dashboard = get_harvest_dashboard()
                if dashboard["days_until_harvest"] <= 3:
                    log.info(
                        "🌔 %s - $%.2f pending harvest",
                        dashboard["moon_phase"],
                        dashboard["pending_harvest"]["total"]
                    )

        except Exception as e:
            log.error("Error in full moon harvest loop: %s", e)

        # Check every 5 minutes to catch the transit window
        await asyncio.sleep(300)
