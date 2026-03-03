#!/usr/bin/env python3
from db import get_paper_balance, get_all_positions, get_futures_account

# Polymarket
balance = get_paper_balance()
positions = get_all_positions()
print("=== POLYMARKET PAPER ACCOUNT ===")
print(f"Balance: ${balance:.2f}")
print(f"Open Positions: {len(positions)}")
for p in positions[:5]:
    print(f"  - {p['side']} {p.get('title', p['market_id'][:12])[:40]} | {p['shares']:.2f} shares")

# Futures
fut = get_futures_account()
print("\n=== FUTURES PAPER ACCOUNT (BTC-PERP) ===")
print(f"Balance: ${fut.get('balance_usdc', 0):.2f}")
print(f"Margin Used: ${fut.get('margin_used', 0):.2f}")
print(f"Total PnL: ${fut.get('total_pnl', 0):.2f}")
print(f"Total Trades: {fut.get('total_trades', 0)}")
