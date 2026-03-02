# 🏗️ Architecture Documentation

Deep dive into how DenseWealth works internally.

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                         main.py                              │
│                    (orchestrator)                            │
└────────────┬────────────────────────────┬───────────────────┘
             │                            │
    ┌────────▼────────┐          ┌───────▼────────┐
    │   watcher.py    │          │ position_      │
    │   (monitors     │          │ manager.py     │
    │   wallets)      │          │ (evaluates)    │
    └────────┬────────┘          └───────┬────────┘
             │                            │
             │ TradeSignal               │ OrderIntent
             │                            │
             ▼                            ▼
    ┌─────────────────────────────────────────────┐
    │            Signal Queue                      │
    └─────────────────┬───────────────────────────┘
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
    ┌────────┐  ┌──────────┐  ┌──────────┐
    │ paper_ │  │executor.│  │executor_ │
    │trader  │  │py       │  │us.py     │
    │.py     │  │(global) │  │(US)      │
    └────┬───┘  └────┬─────┘  └────┬─────┘
         │           │             │
         └───────────┼─────────────┘
                     ▼
              ┌─────────────┐
              │   db.py     │
              │  (SQLite)   │
              └─────────────┘
```

## Core Components

### 1. main.py — Orchestrator

**Purpose:** Entry point and mode router

**Flow:**
1. Parse CLI arguments (`--paper` flag)
2. Load environment and validate config
3. Initialize database
4. Start two async tasks:
   - `start_watchers()` — monitor wallets
   - `process_signals()` — execute trades
5. Route signals to correct executor based on mode

**Key Functions:**
- `_get_mode()` — resolve trading mode from env/args
- `_validate_config()` — ensure required credentials exist
- `_load_executor()` — dynamic import of executor module

### 2. watcher.py — Wallet Monitor

**Purpose:** Poll Polymarket API for new trades

**Flow:**
1. Every `POLL_INTERVAL` seconds:
2. For each wallet in `TARGET_WALLETS`:
3. Fetch recent trades from API
4. Parse trade data (market, token, side, shares, price)
5. Check if already processed (deduplication)
6. Emit `TradeSignal` to queue

**Data Structure:**
```python
@dataclass
class TradeSignal:
    trader_wallet: str    # Who traded
    market_id: str        # Which market
    token_id: str         # Which outcome
    side: str             # "BUY" | "SELL"
    shares: float         # How many shares
    price: float          # At what price
    usdc_value: float     # Total $ value
    timestamp: int        # When
```

**Deduplication:**
- Uses `watched_trades` table
- Stores hash of (wallet, market, timestamp)
- Prevents processing same trade twice

### 3. position_manager.py — Trade Evaluator

**Purpose:** Decide if/how to copy a trade

**Flow:**
1. Receive `TradeSignal`
2. Check filters:
   - Is trade size above `MIN_POSITION_SIZE_USDC`?
   - Would it exceed `MAX_POSITION_SIZE_USDC`?
3. Generate `OrderIntent` with sizing strategy
4. Return intent or None (skip)

**Sizing Strategies:**

**Fixed Amount (default):**
- Always copy with `COPY_AMOUNT_USDC`
- Simple, predictable
- Good for paper trading

**Proportional (future):**
- Scale based on their position size
- Mirror their % allocation
- More sophisticated

**Data Structure:**
```python
@dataclass
class OrderIntent:
    market_id: str
    trader_wallet: str
    token_id: str
    side: str              # "BUY" | "SELL"
    usdc_amount: float     # For buys
    shares_to_sell: float  # For sells
```

### 4. paper_trader.py — Simulated Execution

**Purpose:** Execute trades without real API calls

**Flow:**
1. Receive `OrderIntent`
2. Check virtual balance
3. Fetch market price (from signal, API, or simulate)
4. Apply slippage (`PAPER_SLIPPAGE_BPS`)
5. Calculate shares filled
6. Update position in database
7. Update virtual balance
8. Log trade to history

**Price Sources (priority order):**
1. Signal price (from watched trade)
2. Live API (order book midpoint)
3. Simulated (random 0.30-0.70)

**Slippage Model:**
- Buys: pay `price * (1 + slippage)`
- Sells: receive `price * (1 - slippage)`
- Simulates market impact

### 5. executor.py / executor_us.py — Live Execution

**Purpose:** Place real orders via Polymarket API

**Global Mode (executor.py):**
- Direct wallet signing (EIP-712)
- Uses `py-clob-client` library
- Requires private key
- For non-US users

**US Mode (executor_us.py):**
- FCM-intermediated
- API key authentication
- No private key needed
- CFTC regulated

**Both implement same interface:**
```python
def execute(intent: OrderIntent, signal: TradeSignal | None) -> bool:
    # Place order
    # Update database
    # Return success/failure
```

### 6. db.py — Data Persistence

**Purpose:** SQLite-based storage

**Tables:**

**positions:**
```sql
market_id, trader_wallet, token_id, side, shares, usdc_spent
```
Tracks current holdings

**trade_history:**
```sql
market_id, trader_wallet, token_id, side, shares,
usdc_amount, price, timestamp, mode, success
```
Full audit log

**paper_account:**
```sql
balance_usdc, total_pnl, total_trades, updated_at
```
Virtual balance tracking

**watched_trades:**
```sql
trader_wallet, market_id, timestamp
```
Deduplication

**Key Functions:**
- `init_db()` — create tables
- `get_position()` — fetch current position
- `upsert_position()` — update position
- `log_trade()` — record trade history
- `update_paper_balance()` — modify virtual balance

### 7. config.py — Configuration

**Purpose:** Central configuration

**Key Settings:**
- `TARGET_WALLETS` — wallets to mirror
- `COPY_AMOUNT_USDC` — how much to trade
- `MIN_POSITION_SIZE_USDC` — filter small trades
- `MAX_POSITION_SIZE_USDC` — risk limit
- `POLL_INTERVAL` — API polling frequency

**Environment Override:**
All settings can be overridden via `.env` file

## Data Flow

### Buy Flow

```
1. Watcher sees: Whale buys 100 shares @ $0.50
   └─> Emits TradeSignal(side="BUY", shares=100, price=0.50, usdc_value=50)

2. Position Manager evaluates:
   ├─> Check: $50 > MIN_POSITION_SIZE_USDC ✅
   ├─> Check: Current position + $10 < MAX_POSITION_SIZE_USDC ✅
   └─> Generates OrderIntent(side="BUY", usdc_amount=10)

3. Paper Trader executes:
   ├─> Check balance: $10,000 - $10 = OK ✅
   ├─> Get price: $0.50 (from signal)
   ├─> Apply slippage: $0.50 * 1.001 = $0.5005
   ├─> Calculate shares: $10 / $0.5005 = 19.98 shares
   ├─> Update position: shares += 19.98, spent += $10
   ├─> Update balance: $10,000 - $10 = $9,990
   └─> Log trade to history

4. Result: Bought 19.98 shares for $10
```

### Sell Flow

```
1. Watcher sees: Whale sells 50 shares @ $0.60
   └─> Emits TradeSignal(side="SELL", shares=50, price=0.60)

2. Position Manager evaluates:
   ├─> Check current position: 19.98 shares ✅
   ├─> Calculate sell amount: min(50, 19.98) = 19.98 shares
   └─> Generates OrderIntent(side="SELL", shares_to_sell=19.98)

3. Paper Trader executes:
   ├─> Check position: 19.98 shares available ✅
   ├─> Get price: $0.60
   ├─> Apply slippage: $0.60 * 0.999 = $0.5994
   ├─> Calculate USDC: 19.98 * $0.5994 = $11.97
   ├─> Calculate P&L: ($0.5994 - $0.5005) * 19.98 = +$1.98
   ├─> Update position: CLOSED (0 shares remaining)
   ├─> Update balance: $9,990 + $11.97 = $10,001.97
   └─> Log trade to history

4. Result: Sold 19.98 shares for $11.97, profit = +$1.97
```

## Async Architecture

### Event Loop

```python
async def main():
    queue = asyncio.Queue()

    await asyncio.gather(
        start_watchers(queue),    # Producer
        process_signals(queue),   # Consumer
    )
```

**Producer (watcher):**
- Async HTTP requests to API
- Emits signals to queue
- Non-blocking

**Consumer (processor):**
- Reads from queue
- Evaluates + executes
- Async execution for paper trades

### Concurrency Model

- Single-threaded async
- One watcher per wallet (parallel API calls)
- Sequential trade execution (prevent race conditions)
- Database uses connection pooling

## Error Handling

### Retry Strategy

**API failures:**
- Log warning
- Continue to next wallet
- Retry on next poll interval

**Execution failures:**
- Log to `trade_history` with `success=0`
- Don't update position
- Alert user in logs

### Position Reconciliation

- For live mode: periodic sync with on-chain state
- For paper mode: n/a (database is source of truth)

## Performance Considerations

### Polling Efficiency

**Current:** Poll all wallets every 30s

**Optimization:**
- Webhook subscriptions (real-time)
- Websocket feeds
- Blockchain event listeners

### Database Optimization

**Current:** SQLite with auto-commit

**For scale:**
- Add indexes on `(market_id, trader_wallet)`
- Connection pooling
- WAL mode for concurrent reads

### Rate Limiting

**Current:** No explicit rate limiting

**Production:**
- Respect Polymarket API limits
- Exponential backoff on errors
- Jitter in poll intervals

## Security

### Credential Management

- Never log private keys or API secrets
- Load from environment variables only
- `.env` file excluded from git

### Input Validation

- Validate wallet addresses (0x + 40 hex chars)
- Sanitize all API inputs
- Bounds checking on trade sizes

### Sandboxing

- Paper mode has zero external side effects
- Clear separation between modes
- Explicit user consent for live trading

## Testing Strategy

### Unit Tests

```python
# Test position manager logic
def test_evaluate_buy():
    signal = TradeSignal(...)
    intent = evaluate(signal)
    assert intent.usdc_amount == COPY_AMOUNT_USDC

# Test paper execution
async def test_paper_buy():
    intent = OrderIntent(side="BUY", usdc_amount=10)
    result = await execute_paper(intent, None)
    assert result == True
    assert get_paper_balance() == 9990
```

### Integration Tests

```python
# Test full flow
async def test_end_to_end():
    # Emit signal
    signal = TradeSignal(...)
    await queue.put(signal)

    # Wait for processing
    await asyncio.sleep(2)

    # Verify position created
    pos = get_position(signal.market_id, signal.trader_wallet)
    assert pos is not None
```

### Manual Testing

1. Run with test wallets (known trades)
2. Verify positions match expectations
3. Check P&L calculations
4. Test edge cases (depleted balance, etc.)

## Monitoring & Observability

### Logging Levels

- **INFO:** Trade executions, major events
- **WARNING:** Skipped trades, API issues
- **ERROR:** Execution failures, config errors
- **DEBUG:** Signal processing, price fetches

### Metrics to Track

- Trades per hour
- Win rate (% profitable trades)
- Average slippage
- API response times
- Queue depth

### Alerting

For production:
- Email/SMS on critical errors
- Daily P&L reports
- Low balance warnings
- API downtime alerts

## Future Enhancements

### Planned Features

1. **Web Dashboard**
   - Real-time position viewer
   - P&L charts
   - Trade history browser

2. **Advanced Strategies**
   - Kelly criterion sizing
   - Stop-loss automation
   - Take-profit targets

3. **Multi-Market Arbitrage**
   - Cross-market hedging
   - Correlation-based trading
   - Portfolio optimization

4. **Machine Learning**
   - Trader scoring (who to copy)
   - Outcome prediction
   - Risk assessment

### Architecture Evolution

**Current:** Monolithic async app

**Future:**
- Microservices (watcher, executor, API)
- Message queue (RabbitMQ, Kafka)
- Redis for caching
- PostgreSQL for production
- Kubernetes for scaling

---

**Questions?** See [README.md](README.md) or open an issue.
