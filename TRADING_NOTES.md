# Trading Strategy Analysis & Learning Notes

## 2026-03-02: Why We're Stuck in the $9-12k Range (Futures)

### The Problem
Paper trading with BTC-PERP futures has plateaued around $9-12k despite starting from $1,000. Meanwhile, our previous Polymarket strategy showed stronger growth characteristics.

---

## Current Strategy (Futures - How It's Working)

### What We Have Now
```
Starting Balance: $1,000 USDC
Current Range: $9,000 - $12,000 (9-12x return)

Risk Parameters:
  - Max Leverage: 10x (capped from trader's actual)
  - Max Margin/Trade: 10% of balance
  - Max Margin/Wallet: 30% of balance
  - Min Margin: $5

Position Sizing:
  - Copy trader's margin amount directly
  - Apply caps, then execute
  - No scaling based on our balance tier

Exit Strategy:
  - Close ONLY when trader closes (CLOSE_LONG/CLOSE_SHORT signal)
  - No independent stop-loss
  - No take-profit levels
```

### What's Working
- Signal capture from Hyperliquid is functional
- Paper execution with slippage simulation
- Position tracking and P&L calculation
- Basic margin management
- UI shows positions, trades, balance

### What's NOT Working (The Plateau Causes)
1. **No compounding acceleration** - Linear 10% growth doesn't compound fast
2. **No risk tightening** - Same aggression at $1k and $10k
3. **Dependent on trader exits** - Miss a signal = stuck in position
4. **No profit protection** - All gains exposed to next trade

---

## Target Strategy (What We're Moving Towards)

### Phase 1: Tiered Margin Sizing (Like Polymarket)
```python
FUTURES_MARGIN_TIERS = [
    # (min_balance, max_margin_pct)
    (100000, 0.02),   # $100k+: 2% per trade (capital preservation)
    (50000,  0.03),   # $50k+: 3% per trade
    (25000,  0.05),   # $25k+: 5% per trade
    (10000,  0.07),   # $10k+: 7% per trade (where we are now!)
    (5000,   0.10),   # $5k+: 10% per trade
    (1000,   0.12),   # $1k+: 12% per trade (growth phase)
    (0,      0.15),   # <$1k: 15% per trade (seed phase - aggressive)
]
```

### Phase 2: Wallet Performance Tracking
```python
# Track each wallet's P&L to identify top performers
# Give winning wallets more allocation (50% vs 30%)
# Reduce allocation to underperformers

def get_futures_wallet_stats(wallet: str) -> dict:
    return {
        "wins": count_winning_closes(),
        "losses": count_losing_closes(),
        "win_rate": wins / (wins + losses),
        "total_pnl": sum_of_realized_pnl(),
        "avg_win": avg_pnl_when_winning(),
        "avg_loss": avg_pnl_when_losing(),
    }
```

### Phase 3: Independent Risk Controls
```python
# Stop-loss: Close if unrealized loss > 20% of margin
# Take-profit: Partial close at +30%, +50%, +100%
# Trailing stop: Lock in gains on big winners

FUTURES_STOP_LOSS_PCT = 0.20      # Close if -20% on margin
FUTURES_TAKE_PROFIT_LEVELS = [
    (0.30, 0.25),  # At +30%: close 25% of position
    (0.50, 0.25),  # At +50%: close another 25%
    (1.00, 0.25),  # At +100%: close another 25%
    # Remaining 25% rides with trailing stop
]
```

### Phase 4: Reserve System Integration
```python
# Already have ReserveManager built!
# Need to integrate with futures:
# - At each profit milestone, move % to reserve
# - Reserve excluded from trading
# - Cycling returns reserve to trading over time

FUTURES_RESERVE_TRIGGERS = [
    (2000, 0.10),   # At $2k profit: reserve 10%
    (5000, 0.15),   # At $5k profit: reserve 15%
    (10000, 0.20),  # At $10k profit: reserve 20%
]
```

---

## Key Differences: Futures vs Polymarket Strategy

### Polymarket Strategy (What Was Working)

| Feature | Implementation |
|---------|----------------|
| **Tiered Sizing** | Trade size scales with balance phases (5% at <$1k, down to 0.25% at $500k+) |
| **Dynamic Risk Scaling** | Risk limits tighten automatically as capital grows |
| **Top Performer Boost** | #1 wallet by win rate gets 50% allocation cap vs 30% for others |
| **Inactive Reallocation** | 30% of inactive wallet budgets redistributed to active traders |
| **Proportional Option** | Can mirror trader's portfolio allocation % |
| **Liquidity Gating** | Rejects thin markets at higher balances |
| **Multi-Fund System** | Profit allocation to charity/savings/family funds |

### Futures Strategy (Current - Simpler)

| Feature | Implementation |
|---------|----------------|
| **Margin Sizing** | Flat 10% of balance max per trade |
| **Wallet Cap** | Flat 30% per tracked wallet |
| **Leverage** | Capped at 10x regardless of trader's leverage |
| **No Tiered Scaling** | Same % caps at $1k and $100k |
| **No Performance Boost** | All wallets treated equally |
| **No Compounding Logic** | Linear growth only |

---

## Identified Holes in Futures Strategy

### 1. **No Aggressive Early Compounding**
- Polymarket: 5-6% per trade at <$1k balance (aggressive growth phase)
- Futures: Fixed 10% max margin cap regardless of balance
- **Impact**: We're not compounding aggressively enough when balance is small

### 2. **Static Risk Limits Don't Adapt**
- Polymarket has `RISK_SCALING_TIERS` that tighten as balance grows
- Futures uses same 10%/30% limits forever
- **Impact**: At $10k+ we should be more conservative, but we're not

### 3. **Conservative Leverage Cap (10x)**
- We cap at 10x even if trader uses 20x-50x
- If a trader's edge includes leverage selection, we're diluting it
- **Impact**: Underperformance vs the traders we're copying

### 4. **No Top Performer Recognition**
- Polymarket gives 50% wallet allocation to #1 performer
- Futures treats all wallets equally at 30%
- **Impact**: Not maximizing exposure to best traders

### 5. **Close Signal Dependency (CRITICAL)**
- We ONLY close when we receive a CLOSE_LONG or CLOSE_SHORT signal
- If we miss the signal (network issue, timing), position stays open
- No independent stop-loss or take-profit
- **Impact**: Can get stuck in losing positions or miss exits

### 6. **No Profit Protection**
- No mechanism to lock in gains (like reserve system)
- No profit allocation to separate funds
- All gains stay exposed to next trade
- **Impact**: Paper gains can evaporate on one bad trade

### 7. **Linear vs Exponential Growth**
- At $10k balance with 10% margin cap = $1k margin
- At 10x leverage = $10k notional
- A 1% BTC move = $100 P&L (1% of balance)
- **Impact**: Growth rate doesn't accelerate with success

---

## What Made Polymarket Climb Higher

1. **Phase-Based Aggression**: At small balances, 5-6% per trade compounds faster
2. **Success Amplification**: Top performer gets more allocation
3. **Smart Risk Tightening**: As you win, automatically become more conservative
4. **Profit Isolation**: Reserve system + fund allocation protects gains
5. **Liquidity Awareness**: Won't over-trade thin markets

---

## Local Changes Ready to Push

### Files Modified (1,122+ lines of changes)

| File | Changes |
|------|---------|
| `db.py` | +412 lines - Futures tables, market cache, wallet tracking |
| `web.py` | +156 lines - Futures API endpoints, reserve integration |
| `static/js/app.js` | +350 lines - Futures tab UI, wallet management |
| `templates/index.html` | +139 lines - Futures dashboard, position cards |
| `main.py` | +45 lines - Futures watcher integration |
| `position_manager.py` | +28 lines - Market data integration |

### New Files (Already Created)

| File | Purpose |
|------|---------|
| `futures_watcher.py` | Monitor Hyperliquid API for BTC-PERP trades |
| `futures_position_manager.py` | Evaluate signals, apply risk caps |
| `futures_paper_trader.py` | Simulated execution with slippage |
| `reserve.py` | Reserve system with cycling scheduler |
| `market_data.py` | Liquidity gating and market cache |
| `check_futures.py` | Utility script |
| `check_status.py` | Utility script |

### Reserve System (READY - Just Needs Push)
```
File: reserve.py
Features:
  - ReserveManager class
  - set_reserve_percentage(account_id, pct)
  - configure_cycling(account_id, enabled, schedule, cycle_pct)
  - trigger_cycle(account_id) - manual cycle
  - move_to_reserve / move_from_reserve
  - Background cycling loop (hourly/daily/weekly)

UI Integration:
  - Settings tab has Reserve Settings section
  - Slider for reserve percentage
  - Toggle for cycling
  - Schedule selector (hourly/daily/weekly)
  - Cycle amount slider
```

---

## Performance Chart Data Available

### Trader Stats API: `/api/trader-stats`
```json
{
  "wallet": "0x...",
  "label": "Square-Guy",
  "wins": 12,
  "losses": 5,
  "win_rate": 70.6,
  "total_buys": 25,
  "total_deployed": 1500.00,
  "total_payouts": 2100.00,
  "realized_pnl": 600.00
}
```

### Futures Stats API: `/api/futures/stats`
```json
{
  "balance": 10500.00,
  "margin_used": 1200.00,
  "margin_available": 9300.00,
  "total_pnl": 9500.00,
  "unrealized_pnl": 350.00,
  "total_trades": 87,
  "open_positions": 2
}
```

### Need to Add: Futures Wallet Performance
```sql
-- Query to add for wallet-level futures stats
SELECT
  trader_wallet,
  COUNT(*) as total_trades,
  SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
  SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
  SUM(realized_pnl) as total_pnl,
  AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) as avg_win,
  AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END) as avg_loss
FROM futures_trades
WHERE side IN ('CLOSE_LONG', 'CLOSE_SHORT')
GROUP BY trader_wallet
```

---

## Implementation Roadmap

### Immediate (Do Now)
- [x] Document current vs target strategy
- [x] Push local changes to git (commit 6982192)
- [x] Add futures wallet performance tracking API (commit 10f4572)

### This Week
- [x] Implement tiered margin sizing for futures (DONE - commit 10f4572)
- [x] Top performer wallet boost (DONE - commit 10f4572)
- [ ] Add stop-loss monitoring background task
- [ ] Add futures performance chart to UI

### Next Week
- [ ] Integrate reserve system with futures
- [ ] Add take-profit levels
- [ ] Add performance comparison chart (trader growth vs fund growth)

---

## Questions to Investigate

1. What's the win rate of the wallets we're copying? (need wallet-level stats)
2. Are we missing close signals? (check logs for orphaned positions)
3. What leverage are the copied traders actually using?
4. How do drawdowns compare between strategies?

---

## Metrics to Track Going Forward

| Metric | Target | Current |
|--------|--------|---------|
| Win rate | >55% | ? |
| Avg win size | >Avg loss size | ? |
| Max drawdown | <20% | ? |
| Time in position | <24h avg | ? |
| Signal capture rate | >95% | ? |

---

## Key Insight

> The Polymarket strategy was designed for **compounding growth** with built-in phase transitions.
> The futures strategy is designed for **copy trading** without growth optimization.
>
> **We're running a growth strategy mindset with a flat copy-trade engine.**

---

*Last updated: 2026-03-02*
