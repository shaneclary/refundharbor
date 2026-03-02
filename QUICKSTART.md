# 🚀 Quick Start Guide

Get your Polymarket paper trader running in 5 minutes.

## Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

Or use the setup wizard:

```bash
python setup.py
```

## Step 2: Add Wallet Addresses

Edit [config.py](config.py) and add wallets to track:

```python
TARGET_WALLETS = [
    "0x1234567890abcdef1234567890abcdef12345678",  # Replace with real addresses
]
```

**Where to find good wallets:**
- [Polymarket Leaderboard](https://polymarket.com/leaderboard)
- Twitter/X — search "Polymarket trader" or "@polymarket"
- Watch for large positions on trending markets

## Step 3: Run Paper Trading

```bash
python main.py
```

You'll see:

```
📋 PAPER MODE — no real orders, simulated fills
🚀 Polybot running — watching 2 wallets
👀 Watching 2 wallets (poll interval: 30s)
```

## Step 4: Monitor Performance

```bash
python stats.py
```

Output:

```
📊 PAPER TRADING ACCOUNT SUMMARY
Current Balance:  $10,000.00 USDC
Total P&L:        +$234.56 USDC
Total Trades:     12
Return:           +2.35% 📈
```

## Customizing

### Change Copy Amount

Edit [.env](.env):

```bash
COPY_AMOUNT_USDC=25.0  # Copy $25 per trade instead of $10
```

### Change Starting Balance

```bash
PAPER_STARTING_BALANCE=50000.0  # Start with $50k virtual
```

### Faster Polling

```bash
POLL_INTERVAL=10  # Check every 10 seconds instead of 30
```

## Troubleshooting

### "No TARGET_WALLETS configured"

You forgot step 2! Add wallet addresses to `config.py`.

### No trades happening

- Make sure wallets are actually trading (check on Polymarket)
- Wait longer (poll interval is 30s by default)
- Check logs for API errors

### Want to reset?

Delete the database and start fresh:

```bash
rm densewealth.db
python main.py
```

## Next Steps

- Read the full [README.md](README.md) for advanced features
- Adjust risk settings in [.env](.env)
- Customize copy logic in [position_manager.py](position_manager.py)
- When confident, switch to live mode (see README)

---

**That's it!** You're now paper trading on Polymarket. 📈

Watch the logs, check your stats, and refine your strategy risk-free.
