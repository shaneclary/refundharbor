#!/usr/bin/env python3
from db import get_futures_account, get_all_futures_positions, get_futures_trade_history, get_futures_tracked_wallets

# Account stats
acct = get_futures_account()
print("=== FUTURES ACCOUNT ===")
print(f"Balance: ${acct.get('balance_usdc', 0):.2f}")
print(f"Margin Used: ${acct.get('margin_used', 0):.2f}")
print(f"Total PnL: ${acct.get('total_pnl', 0):.2f}")
print(f"Total Trades: {acct.get('total_trades', 0)}")

# Positions
positions = get_all_futures_positions()
print(f"\n=== OPEN POSITIONS ({len(positions)}) ===")
if positions:
    for p in positions[:5]:
        pnl = p.get('unrealized_pnl', 0)
        print(f"  {p['side']} {p['symbol']} | {p['size']:.4f} BTC @ ${p['entry_price']:.2f} | {p['leverage']}x | PnL: ${pnl:.2f}")
else:
    print("  No open positions")

# Trades
trades = get_futures_trade_history(limit=10)
print(f"\n=== RECENT TRADES ({len(trades)}) ===")
if trades:
    for t in trades[:5]:
        print(f"  {t['side']} {t['size']:.4f} BTC @ ${t['price']:.2f} | PnL: ${t.get('realized_pnl', 0):.2f}")
else:
    print("  No trades yet")

# Tracked wallets
wallets = get_futures_tracked_wallets()
print(f"\n=== TRACKED WALLETS ({len(wallets)}) ===")
if wallets:
    for w in wallets:
        print(f"  {w['address'][:12]}... | {w.get('label', 'unlabeled')}")
else:
    print("  No wallets tracked - add Hyperliquid wallets to start copying!")
