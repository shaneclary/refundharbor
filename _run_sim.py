"""Run simulation with new tiered fixed strategy on all historical trades."""
import sys
sys.path.insert(0, r"c:\shaneclary\Polytrade")

from db import get_conn
from simulator import SimParams, run_simulation, DEFAULT_TIERS

# Fetch all trades
with get_conn() as conn:
    trades = [dict(r) for r in conn.execute(
        "SELECT * FROM trade_history ORDER BY timestamp ASC"
    ).fetchall()]

print(f"Loaded {len(trades)} trades")
print(f"Time range: {trades[0]['timestamp']} -> {trades[-1]['timestamp']}")
print()

# --- 1) New tiered fixed strategy (what we just implemented) ---
params_tiered = SimParams(
    starting_balance=186.0,
    max_trade_pct=0.15,
    max_wallet_pct=0.50,
    max_market_pct=0.15,
    slippage_bps=10,
    copy_strategy="tiered_fixed",
    copy_amount_tiers=DEFAULT_TIERS,
)
result_tiered = run_simulation(trades, params_tiered, actual_starting_balance=186.0)

print("=== TIERED FIXED (new strategy) ===")
print(f"  Starting:    $186.00")
print(f"  Final:       ${result_tiered.final_balance:,.2f}")
print(f"  P&L:         ${result_tiered.total_pnl:,.2f}")
print(f"  Return:      {result_tiered.pnl_pct:,.1f}%")
print(f"  Max DD:      {result_tiered.max_drawdown_pct:.1f}%")
print(f"  Buys:        {result_tiered.buys_executed}")
print(f"  Wins/Losses: {result_tiered.wins}/{result_tiered.losses}")
wr = result_tiered.wins / (result_tiered.wins + result_tiered.losses) * 100 if (result_tiered.wins + result_tiered.losses) > 0 else 0
print(f"  Win Rate:    {wr:.1f}%")
print()

# --- 2) Old proportional strategy for comparison ---
params_prop = SimParams(
    starting_balance=186.0,
    max_trade_pct=0.05,
    max_wallet_pct=0.30,
    max_market_pct=0.10,
    slippage_bps=10,
    copy_strategy="proportional",
)
result_prop = run_simulation(trades, params_prop, actual_starting_balance=186.0)

print("=== PROPORTIONAL (old strategy) ===")
print(f"  Starting:    $186.00")
print(f"  Final:       ${result_prop.final_balance:,.2f}")
print(f"  P&L:         ${result_prop.total_pnl:,.2f}")
print(f"  Return:      {result_prop.pnl_pct:,.1f}%")
print(f"  Max DD:      {result_prop.max_drawdown_pct:.1f}%")
print(f"  Buys:        {result_prop.buys_executed}")
print(f"  Wins/Losses: {result_prop.wins}/{result_prop.losses}")
wr2 = result_prop.wins / (result_prop.wins + result_prop.losses) * 100 if (result_prop.wins + result_prop.losses) > 0 else 0
print(f"  Win Rate:    {wr2:.1f}%")
print()

# --- 3) Flat fixed $10 for reference ---
params_fixed = SimParams(
    starting_balance=186.0,
    max_trade_pct=0.15,
    max_wallet_pct=0.50,
    max_market_pct=0.15,
    slippage_bps=10,
    copy_strategy="fixed",
    copy_amount_usdc=10.0,
)
result_fixed = run_simulation(trades, params_fixed, actual_starting_balance=186.0)

print("=== FIXED $10 (baseline) ===")
print(f"  Starting:    $186.00")
print(f"  Final:       ${result_fixed.final_balance:,.2f}")
print(f"  P&L:         ${result_fixed.total_pnl:,.2f}")
print(f"  Return:      {result_fixed.pnl_pct:,.1f}%")
print(f"  Max DD:      {result_fixed.max_drawdown_pct:.1f}%")
print(f"  Buys:        {result_fixed.buys_executed}")
print(f"  Wins/Losses: {result_fixed.wins}/{result_fixed.losses}")
wr3 = result_fixed.wins / (result_fixed.wins + result_fixed.losses) * 100 if (result_fixed.wins + result_fixed.losses) > 0 else 0
print(f"  Win Rate:    {wr3:.1f}%")
print()

improvement = result_tiered.total_pnl / result_prop.total_pnl if result_prop.total_pnl > 0 else 0
print(f"Tiered vs Proportional: {improvement:.0f}x more profit")
print(f"Tiered vs Fixed $10:    {result_tiered.total_pnl / result_fixed.total_pnl:.1f}x more profit" if result_fixed.total_pnl > 0 else "")
