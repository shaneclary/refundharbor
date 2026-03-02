#!/usr/bin/env python3
# stats.py — view paper trading performance stats
#
# Usage:
#   python stats.py              → show account summary
#   python stats.py --positions  → show current positions
#   python stats.py --trades     → show recent trades
#   python stats.py --all        → show everything

import argparse
import sys
from pathlib import Path

# Ensure emoji output works on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from config import PAPER_STARTING_BALANCE
from db import get_all_positions, get_paper_stats, get_trade_history

DB_PATH = Path(__file__).parent / "densewealth.db"


def print_account_summary():
    """Print paper account summary."""
    stats = get_paper_stats()
    if not stats:
        print("❌ No paper account found. Run main.py first to initialize.")
        return

    balance = stats.get("balance_usdc", 0)
    total_pnl = stats.get("total_pnl", 0)
    total_trades = stats.get("total_trades", 0)

    print("\n" + "=" * 60)
    print("📊 PAPER TRADING ACCOUNT SUMMARY")
    print("=" * 60)
    print(f"Current Balance:  ${balance:,.2f} USDC")
    print(f"Total P&L:        ${total_pnl:+,.2f} USDC")
    print(f"Total Trades:     {total_trades}")

    if total_pnl != 0 and PAPER_STARTING_BALANCE > 0:
        pnl_pct = (total_pnl / PAPER_STARTING_BALANCE) * 100
        emoji = "📈" if total_pnl > 0 else "📉"
        print(f"Return:           {pnl_pct:+.2f}% {emoji}")

    print("=" * 60 + "\n")


def print_positions():
    """Print current positions."""
    positions = get_all_positions()

    print("\n" + "=" * 80)
    print("📍 CURRENT POSITIONS")
    print("=" * 80)

    if not positions:
        print("No open positions.")
        print("=" * 80 + "\n")
        return

    print(f"{'Market ID':<45} {'Side':<6} {'Shares':<12} {'Spent':<12} {'Avg Cost':<10}")
    print("-" * 80)

    for pos in positions:
        market_id = pos["market_id"][:42] + "..." if len(pos["market_id"]) > 45 else pos["market_id"]
        side = pos["side"]
        shares = pos["shares"]
        spent = pos["usdc_spent"]
        avg_cost = spent / shares if shares > 0 else 0

        print(f"{market_id:<45} {side:<6} {shares:>12.4f} ${spent:>11.2f} ${avg_cost:>9.4f}")

    print("=" * 80 + "\n")


def print_trade_history(limit=20):
    """Print recent trade history."""
    trades = get_trade_history(limit)

    print("\n" + "=" * 100)
    print(f"📜 RECENT TRADES (last {limit})")
    print("=" * 100)

    if not trades:
        print("No trades yet.")
        print("=" * 100 + "\n")
        return

    print(f"{'Time':<20} {'Side':<6} {'Shares':<12} {'Price':<10} {'Amount':<12} {'Market ID':<30}")
    print("-" * 100)

    for trade in trades:
        timestamp = trade["timestamp"]
        side = trade["side"]
        shares = trade["shares"]
        price = trade["price"] or 0
        amount = trade["usdc_amount"]
        market = trade["market_id"][:27] + "..." if len(trade["market_id"]) > 30 else trade["market_id"]
        success = "✅" if trade["success"] else "❌"

        print(f"{timestamp:<20} {side:<6} {shares:>12.4f} ${price:>9.4f} ${amount:>11.2f} {market:<30} {success}")

    print("=" * 100 + "\n")


def main():
    parser = argparse.ArgumentParser(description="View paper trading stats")
    parser.add_argument("--positions", action="store_true", help="Show current positions")
    parser.add_argument("--trades", action="store_true", help="Show recent trades")
    parser.add_argument("--all", action="store_true", help="Show everything")
    parser.add_argument("--limit", type=int, default=20, help="Number of trades to show (default: 20)")

    args = parser.parse_args()

    # Check if database exists
    if not DB_PATH.exists():
        print(f"❌ Database not found at {DB_PATH}")
        print("Run main.py first to initialize the database.")
        sys.exit(1)

    # Show everything if --all or no specific flags
    show_all = args.all or not (args.positions or args.trades)

    if show_all or not (args.positions or args.trades):
        print_account_summary()

    if show_all or args.positions:
        print_positions()

    if show_all or args.trades:
        print_trade_history(args.limit)


if __name__ == "__main__":
    main()
