# 🎯 Example Configurations

Real-world setup examples for different use cases.

## 1️⃣ Conservative Paper Trader (Beginner)

Perfect for learning and testing.

### config.py
```python
TARGET_WALLETS = [
    "0x1234567890abcdef1234567890abcdef12345678",  # One trusted trader
]
```

### .env
```bash
POLYMARKET_MODE=paper
PAPER_STARTING_BALANCE=5000.0      # Smaller balance
COPY_AMOUNT_USDC=5.0               # Small positions
MIN_POSITION_SIZE_USDC=10.0        # Ignore small trades
MAX_POSITION_SIZE_USDC=50.0        # Conservative limit
POLL_INTERVAL=60                   # Check every minute
```

**Profile:**
- Low risk
- Small positions
- One trader to learn from
- Good for understanding mechanics

---

## 2️⃣ Aggressive Multi-Wallet (Intermediate)

Copy multiple top traders with higher limits.

### config.py
```python
TARGET_WALLETS = [
    "0x1111111111111111111111111111111111111111",  # Whale #1
    "0x2222222222222222222222222222222222222222",  # Whale #2
    "0x3333333333333333333333333333333333333333",  # Whale #3
    "0x4444444444444444444444444444444444444444",  # Whale #4
    "0x5555555555555555555555555555555555555555",  # Whale #5
]
```

### .env
```bash
POLYMARKET_MODE=paper
PAPER_STARTING_BALANCE=50000.0     # Larger bankroll
COPY_AMOUNT_USDC=50.0              # Bigger positions
MIN_POSITION_SIZE_USDC=25.0        # Filter noise
MAX_POSITION_SIZE_USDC=500.0       # Allow larger positions
POLL_INTERVAL=15                   # More frequent checks
PAPER_SLIPPAGE_BPS=20              # Higher slippage simulation
```

**Profile:**
- Higher risk/reward
- Multiple traders = diversification
- Faster polling
- Good for testing scalability

---

## 3️⃣ High-Frequency Scalper (Advanced)

Maximum activity, tight spreads.

### config.py
```python
# Large list of active traders
TARGET_WALLETS = [
    # Top 20 traders from leaderboard
    "0x...",
    "0x...",
    # ... 20 total wallets
]
```

### .env
```bash
POLYMARKET_MODE=paper
PAPER_STARTING_BALANCE=100000.0
COPY_AMOUNT_USDC=25.0
MIN_POSITION_SIZE_USDC=5.0         # Catch more trades
MAX_POSITION_SIZE_USDC=200.0
POLL_INTERVAL=10                   # Very frequent
PAPER_SLIPPAGE_BPS=5               # Tighter execution
PAPER_FILL_DELAY=0.5               # Faster fills
```

**Profile:**
- High activity
- Many opportunities
- Requires monitoring
- Tests system limits

---

## 4️⃣ Sector-Specific Specialist

Focus on specific market types (e.g., politics, sports).

### config.py
```python
# Traders known for political market expertise
TARGET_WALLETS = [
    "0xPoliticalWhale1...",
    "0xPoliticalWhale2...",
    "0xPoliticalWhale3...",
]
```

### .env
```bash
POLYMARKET_MODE=paper
PAPER_STARTING_BALANCE=20000.0
COPY_AMOUNT_USDC=30.0
MIN_POSITION_SIZE_USDC=15.0
MAX_POSITION_SIZE_USDC=300.0
POLL_INTERVAL=30
```

**Custom Logic (position_manager.py):**
```python
def evaluate(signal: TradeSignal) -> Optional[OrderIntent]:
    # Only copy political markets
    if not is_political_market(signal.market_id):
        return None

    # Standard evaluation
    if signal.side == "BUY":
        return _evaluate_buy(signal)
    elif signal.side == "SELL":
        return _evaluate_sell(signal)
```

**Profile:**
- Niche expertise
- Curated trader list
- Custom filtering
- Domain-specific edge

---

## 5️⃣ Live Trading (Global) — REAL MONEY

Ready for production.

### config.py
```python
TARGET_WALLETS = [
    "0xVettedTrader1...",  # Proven track record
    "0xVettedTrader2...",
]
```

### .env
```bash
# ⚠️ LIVE MODE - REAL MONEY AT RISK
POLYMARKET_MODE=global

# Your credentials
POLY_API_KEY=your_actual_key
POLY_API_SECRET=your_actual_secret
POLY_API_PASSPHRASE=your_actual_passphrase
POLY_PRIVATE_KEY=your_actual_private_key

# Conservative live settings
COPY_AMOUNT_USDC=10.0              # Start small
MIN_POSITION_SIZE_USDC=50.0        # Ignore small trades
MAX_POSITION_SIZE_USDC=100.0       # Strict limits
POLL_INTERVAL=30
```

**Safety Checklist:**
- [ ] Tested extensively in paper mode
- [ ] Small position sizes
- [ ] Limited wallets (1-3)
- [ ] Monitor constantly
- [ ] Have stop-loss plan

---

## 6️⃣ Live Trading (US) — REAL MONEY

US-compliant setup.

### config.py
```python
TARGET_WALLETS = [
    "0xUSCompliantTrader1...",
]
```

### .env
```bash
# ⚠️ LIVE MODE - REAL MONEY AT RISK
POLYMARKET_MODE=us

# Your US credentials (no private key)
POLY_US_API_KEY=your_us_key
POLY_US_API_SECRET=your_us_secret
POLY_US_API_PASSPHRASE=your_us_passphrase
POLY_US_WALLET_ADDRESS=0xyour_wallet_address

# Ultra-conservative (CFTC regulated)
COPY_AMOUNT_USDC=5.0
MIN_POSITION_SIZE_USDC=100.0
MAX_POSITION_SIZE_USDC=50.0
POLL_INTERVAL=60
```

---

## 📊 Comparison Table

| Profile | Wallets | Copy Amount | Max Position | Poll | Risk |
|---------|---------|-------------|--------------|------|------|
| Conservative | 1 | $5 | $50 | 60s | ⭐ |
| Aggressive | 5 | $50 | $500 | 15s | ⭐⭐⭐ |
| High-Freq | 20 | $25 | $200 | 10s | ⭐⭐⭐⭐ |
| Specialist | 3 | $30 | $300 | 30s | ⭐⭐ |
| Live Global | 2 | $10 | $100 | 30s | ⭐⭐⭐⭐⭐ |
| Live US | 1 | $5 | $50 | 60s | ⭐⭐⭐⭐⭐ |

---

## 🎓 Progression Path

### Week 1: Learn
```
Conservative setup → Run paper mode → Understand logs
```

### Week 2-4: Optimize
```
Add wallets → Tune settings → Track performance
```

### Month 2: Specialize
```
Pick niche → Custom logic → Refine strategy
```

### Month 3+: Scale
```
Increase capital → More wallets → Advanced features
```

---

## 🛠️ Customization Examples

### Custom Sizing by Confidence
```python
# In position_manager.py
def _evaluate_buy(signal):
    # Scale amount by trader reputation
    trader_score = get_trader_score(signal.trader_wallet)
    scaled_amount = COPY_AMOUNT_USDC * trader_score

    return OrderIntent(
        usdc_amount=scaled_amount,
        ...
    )
```

### Time-Based Filters
```python
# Only trade during active hours
def evaluate(signal):
    from datetime import datetime

    hour = datetime.now().hour
    if hour < 9 or hour > 21:  # Outside 9am-9pm
        return None

    return _evaluate_buy(signal)
```

### Market Category Filters
```python
# Prefer certain market types
PREFERRED_CATEGORIES = ["politics", "crypto"]

def evaluate(signal):
    market_info = fetch_market_info(signal.market_id)
    if market_info["category"] not in PREFERRED_CATEGORIES:
        return None

    return _evaluate_buy(signal)
```

---

## 📈 Real-World Examples

### Example 1: Top Leaderboard Trader
```python
# Whale who made $500k profit
TARGET_WALLETS = ["0xWhaleAddress..."]

# Settings
COPY_AMOUNT_USDC=20.0    # Bigger bets
MAX_POSITION_SIZE_USDC=200.0
```

### Example 2: Consistent Winner (60% Win Rate)
```python
# Steady trader with proven track record
TARGET_WALLETS = ["0xConsistentTrader..."]

# Settings
COPY_AMOUNT_USDC=15.0
MAX_POSITION_SIZE_USDC=150.0
```

### Example 3: Political Market Expert
```python
# Specialized in US politics markets
TARGET_WALLETS = ["0xPoliticsExpert..."]

# Settings
COPY_AMOUNT_USDC=25.0
# + custom market filtering
```

---

## ⚠️ Anti-Patterns (What NOT to Do)

### ❌ Over-Diversification
```python
# DON'T: Follow 100 random wallets
TARGET_WALLETS = [random addresses...]  # Bad
```

### ❌ No Risk Limits
```python
# DON'T: Unlimited positions
MAX_POSITION_SIZE_USDC=999999  # Dangerous
```

### ❌ Chasing Every Trade
```python
# DON'T: Copy all trades blindly
MIN_POSITION_SIZE_USDC=0.01  # Too low
```

### ❌ Over-Polling
```python
# DON'T: Hammer the API
POLL_INTERVAL=1  # Too aggressive, may get banned
```

---

## 💡 Pro Tips

1. **Start Small, Scale Up**
   ```
   Week 1: $5 copies, 1 wallet
   Week 4: $10 copies, 3 wallets
   Week 8: $20 copies, 5 wallets
   ```

2. **Track Everything**
   ```bash
   # Daily routine
   python stats.py > daily_report_$(date +%Y%m%d).txt
   ```

3. **A/B Test Strategies**
   ```
   Run two bots:
   - Bot A: Conservative settings
   - Bot B: Aggressive settings
   Compare after 30 days
   ```

4. **Review Weekly**
   ```sql
   -- Check best/worst markets
   SELECT market_id, SUM(usdc_amount) as total_pnl
   FROM trade_history
   WHERE timestamp > date('now', '-7 days')
   GROUP BY market_id
   ORDER BY total_pnl DESC;
   ```

---

**Choose a profile, customize to taste, and start trading!** 🚀
