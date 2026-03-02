# 📋 DenseWealth — Polymarket Paper Trading Bot

A production-ready paper trading bot that mirrors trades from top Polymarket wallets. Perfect for testing strategies risk-free before going live.

## Features

- ✅ **Paper Trading Mode** — Simulate trades with virtual balance, zero risk
- 👀 **Wallet Tracking** — Monitor multiple wallets and copy their trades
- 💰 **Position Management** — Automatic position tracking and P&L calculation
- 🎯 **Risk Controls** — Max position sizes, minimum trade amounts
- 📊 **Trade History** — Full audit log of all simulated trades
- 🔄 **Live Mode Ready** — Swap to real trading (Global or US) with one env var

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure

Copy the example config and add wallet addresses:

```bash
cp .env.example .env
```

Edit `config.py` and add wallets to track:

```python
TARGET_WALLETS = [
    "0x1234567890abcdef1234567890abcdef12345678",  # smart trader
    "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd",  # whale
]
```

### 3. Run Paper Trading

```bash
python main.py
```

That's it! The bot will:
- Start with $10,000 virtual USDC
- Watch your target wallets every 30 seconds
- Copy their trades with simulated execution
- Track your paper P&L

## Configuration

### Environment Variables (.env)

```bash
# Trading mode
POLYMARKET_MODE=paper    # paper | global | us

# Paper trading settings
PAPER_STARTING_BALANCE=10000.0
PAPER_SLIPPAGE_BPS=10           # 0.1% slippage
PAPER_FILL_DELAY=1.0            # 1 second fill delay

# Copy trading parameters
COPY_AMOUNT_USDC=10.0           # Fixed amount per trade
MIN_POSITION_SIZE_USDC=5.0      # Ignore trades < $5
MAX_POSITION_SIZE_USDC=100.0    # Max $100 per market

# Polling
POLL_INTERVAL=30                # Check for new trades every 30s
```

### Target Wallets (config.py)

Add Ethereum addresses of traders you want to mirror:

```python
TARGET_WALLETS = [
    "0x...",  # Add as many as you want
]
```

**Where to find good wallets:**
- [Polymarket Leaderboard](https://polymarket.com/leaderboard)
- Twitter/X — search for Polymarket whales
- [Dune Analytics](https://dune.com) — query top performers

## How It Works

### Paper Trading Flow

```
1. Watcher polls Polymarket API for new trades
   └─> Checks each TARGET_WALLET every POLL_INTERVAL

2. New trade detected
   └─> TradeSignal emitted to queue

3. Position Manager evaluates signal
   └─> Should we copy? How much?
   └─> Generates OrderIntent

4. Paper Trader executes (simulated)
   └─> Fetches market price
   └─> Applies slippage
   └─> Updates virtual balance
   └─> Records in database

5. Position tracking
   └─> SQLite database stores:
       - Current positions
       - Trade history
       - P&L stats
```

### Database Schema

All data stored in `densewealth.db` (SQLite):

- **positions** — Current holdings per market
- **trade_history** — Every trade executed
- **paper_account** — Virtual balance and stats
- **watched_trades** — Deduplication tracking

## Switching to Live Trading

### Global Mode (Non-US, Direct Wallet)

1. Get API credentials from [polymarket.com/settings](https://polymarket.com/settings)
2. Export your MetaMask private key
3. Update `.env`:

```bash
POLYMARKET_MODE=global
POLY_API_KEY=your_key
POLY_API_SECRET=your_secret
POLY_API_PASSPHRASE=your_passphrase
POLY_PRIVATE_KEY=your_private_key
```

4. Install live trading dependencies:

```bash
pip install py-clob-client web3 eth-account
```

5. Implement executor logic in `executor.py` (see comments)

### US Mode (CFTC-Regulated, FCM)

1. Get approved from US waitlist
2. Get API credentials from US portal
3. Update `.env`:

```bash
POLYMARKET_MODE=us
POLY_US_API_KEY=your_key
POLY_US_API_SECRET=your_secret
POLY_US_API_PASSPHRASE=your_passphrase
POLY_US_WALLET_ADDRESS=0x...
```

4. Ready to go! (See `executor_us.py` for implementation)

## Risk Management

Built-in safety features:

- **Max Position Size** — Won't exceed `MAX_POSITION_SIZE_USDC` per market
- **Min Trade Size** — Ignores dust trades below `MIN_POSITION_SIZE_USDC`
- **Balance Checks** — Paper mode validates virtual balance before trades
- **Deduplication** — Never processes the same trade twice

## Monitoring

Check your paper trading performance:

```bash
# View recent trades
sqlite3 densewealth.db "SELECT * FROM trade_history ORDER BY timestamp DESC LIMIT 10"

# Check current positions
sqlite3 densewealth.db "SELECT * FROM positions"

# View account stats
sqlite3 densewealth.db "SELECT * FROM paper_account"
```

Or use any SQLite browser (DB Browser for SQLite, etc.)

## Troubleshooting

### "No TARGET_WALLETS configured"

Add wallet addresses to `config.py`:

```python
TARGET_WALLETS = [
    "0x1234567890abcdef1234567890abcdef12345678",
]
```

### Trades not appearing

1. Check wallet addresses are correct (lowercase)
2. Verify wallets are actually trading on Polymarket
3. Check `POLL_INTERVAL` — default is 30s
4. Review logs for API errors

### API timeouts

- Polymarket API might be rate-limited
- Increase `POLL_INTERVAL` to reduce request frequency
- Consider implementing retry logic with backoff

### Paper balance depleted

Reset your paper account:

```bash
sqlite3 densewealth.db "UPDATE paper_account SET balance_usdc = 10000.0 WHERE id = 1"
```

## Advanced Usage

### Custom Copy Strategies

Edit `position_manager.py` to implement:
- Proportional copying (scale with their position size)
- Kelly criterion position sizing
- Risk-adjusted copy amounts
- Market-specific rules

### Webhook Watchers

Replace polling with real-time webhooks:
- Use Alchemy/Infura for blockchain events
- Subscribe to Polymarket websocket feeds
- See comments in `watcher.py`

### Portfolio Analytics

Add dashboards and reporting:
- Sharpe ratio calculation
- Win rate tracking
- Market-by-market performance
- Correlation analysis

## Project Structure

```
DenseWealth/
├── main.py              # Entry point
├── config.py            # Configuration
├── watcher.py           # Wallet monitoring
├── position_manager.py  # Trade evaluation logic
├── paper_trader.py      # Paper execution
├── executor.py          # Global mode executor
├── executor_us.py       # US mode executor
├── reconciler.py        # Position reconciliation
├── db.py               # Database layer
├── requirements.txt     # Dependencies
├── .env.example        # Config template
└── densewealth.db        # SQLite database (created on first run)
```

## Safety & Disclaimers

- **Paper trading only by default** — No real money at risk
- **Always test strategies** before going live
- **Review all trades** — Automatic copying can amplify losses
- **Check legal requirements** — Prediction markets may be restricted in your jurisdiction
- **No guarantees** — Past performance ≠ future results

## Contributing

Found a bug? Have a feature request?

1. Open an issue with details
2. Submit a PR with improvements
3. Share your winning strategies (if you want!)

## License

MIT — Use at your own risk

---

**Ready to start paper trading?**

```bash
python main.py
```

Watch your virtual portfolio grow (or shrink) risk-free! 📈
