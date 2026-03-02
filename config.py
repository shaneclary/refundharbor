# config.py — bot configuration
#
# Add wallet addresses you want to copy-trade here.
# Format: list of Ethereum addresses (0x...)

import os

# ── TARGET WALLETS ──────────────────────────────────────────────────────────
# Initial seed wallets. After first run these are stored in the DB and
# managed from the dashboard UI. This list is only used for seeding.

TARGET_WALLETS = [
    "0x1979ae6b7e6534de9c4539d0c205e582ca637c9d",  # Square-Guy
    "0x1d0034134e339a309700ff2d34e99fa2d48b0313",  # Canine-Commandment
    "0x2d8b401d2f0e6937afebf18e19e11ca568a5260a",  # vidarx
]

# Seed labels (used once when seeding DB, then managed from dashboard)
WALLET_SEED_LABELS = {
    "0x1979ae6b7e6534de9c4539d0c205e582ca637c9d": "Square-Guy",
    "0x1d0034134e339a309700ff2d34e99fa2d48b0313": "Canine-Commandment",
    "0x2d8b401d2f0e6937afebf18e19e11ca568a5260a": "vidarx",
}


def get_active_wallets() -> list[str]:
    """Get active wallet addresses from DB. Falls back to TARGET_WALLETS if DB not ready."""
    try:
        from db import get_tracked_wallets
        wallets = get_tracked_wallets()
        if wallets:
            return [w["address"] for w in wallets]
    except Exception:
        pass
    return TARGET_WALLETS

# ── COPY PARAMETERS ─────────────────────────────────────────────────────────

# Copy strategy:
#   "tiered_fixed" → fixed dollar amount that scales up with balance (recommended)
#   "fixed"        → always trade COPY_AMOUNT_USDC per signal
#   "proportional" → mirrors the trader's allocation % of their portfolio
COPY_STRATEGY = os.getenv("COPY_STRATEGY", "tiered_fixed")

# Fixed mode: base USDC per trade (used by "fixed" strategy; "tiered_fixed" uses tiers below)
COPY_AMOUNT_USDC = float(os.getenv("COPY_AMOUNT_USDC", "10.0"))

# Tiered fixed amounts: as balance grows, trade size scales up.
# Format: list of (min_balance, trade_amount) — evaluated top-down, first match wins.
#
# SCALING PHILOSOPHY — path to $1M:
#
#   Phase 1 "Seed"      ($0–$1k):       ~5% per trade — aggressive compounding,
#                                        small absolute risk, max growth velocity.
#
#   Phase 2 "Growth"    ($1k–$10k):     ~2-3% per trade — proven edge, scale up
#                                        trade size but start respecting drawdowns.
#
#   Phase 3 "Scale"     ($10k–$100k):   ~1-2% per trade — capital preservation
#                                        matters now. Losing $2k hurts. Tighten
#                                        per-trade sizing, widen market caps to
#                                        stay deployed across more markets.
#
#   Phase 4 "Compound"  ($100k–$500k):  ~0.5-1% per trade — institutional sizing.
#                                        Liquidity becomes a factor on Polymarket.
#                                        Spread across many markets, never move
#                                        the book. Switch to proportional strategy.
#
#   Phase 5 "Harvest"   ($500k–$1M+):   ~0.25-0.5% per trade — protect the bag.
#                                        Reserve system active, profit-taking on.
#                                        At this level you ARE the liquidity.
#
COPY_AMOUNT_TIERS = [
    # ── Phase 5: Harvest ($500k+) ─── ~0.25-0.5% per trade
    (750000, 2500),  # balance >= $750k  →  $2,500/trade  (0.33%)
    (500000, 2000),  # balance >= $500k  →  $2,000/trade  (0.40%)
    # ── Phase 4: Compound ($100k-$500k) ─── ~0.5-1% per trade
    (250000, 1500),  # balance >= $250k  →  $1,500/trade  (0.60%)
    (100000, 1000),  # balance >= $100k  →  $1,000/trade  (1.00%)
    # ── Phase 3: Scale ($10k-$100k) ─── ~1-2% per trade
    (50000,   750),  # balance >= $50k   →    $750/trade  (1.50%)
    (25000,   500),  # balance >= $25k   →    $500/trade  (2.00%)
    (10000,   200),  # balance >= $10k   →    $200/trade  (2.00%)
    # ── Phase 2: Growth ($1k-$10k) ─── ~2-3% per trade
    (7500,    150),  # balance >= $7,500 →    $150/trade  (2.00%)
    (5000,    100),  # balance >= $5,000 →    $100/trade  (2.00%)
    (2000,     75),  # balance >= $2,000 →     $75/trade  (3.75%)
    (1000,     50),  # balance >= $1,000 →     $50/trade  (5.00%)
    # ── Phase 1: Seed ($0-$1k) ─── ~5% per trade
    (500,      25),  # balance >= $500   →     $25/trade  (5.00%)
    (250,      15),  # balance >= $250   →     $15/trade  (6.00%)
    (0,        10),  # balance < $250    →     $10/trade  (5.40% @ $186)
]

# Minimum copy amount (skip dust-sized copies)
MIN_COPY_USDC = float(os.getenv("MIN_COPY_USDC", "0.50"))

# ── RISK LIMITS (all % of current balance) ─────────────────────────────────
# These are the BASE defaults. At higher balances, the dynamic risk system
# (RISK_SCALING_TIERS below) tightens these automatically.

# Max single trade as % of current balance
MAX_TRADE_PCT = float(os.getenv("MAX_TRADE_PCT", "0.15"))

# Max exposure per tracked wallet as % of current balance
MAX_WALLET_PCT = float(os.getenv("MAX_WALLET_PCT", "0.50"))

# Max exposure per individual market as % of current balance
MAX_MARKET_PCT = float(os.getenv("MAX_MARKET_PCT", "0.15"))

# ── DYNAMIC RISK SCALING ──────────────────────────────────────────────────
# As capital grows, risk limits tighten to protect gains.
# Format: (min_balance, max_trade_pct, max_wallet_pct, max_market_pct)
# Evaluated top-down, first match wins. Falls back to base limits above.
#
# Key insight: at small balances you need concentration to grow.
# At large balances you need diversification to survive.
RISK_SCALING_TIERS = [
    #  min_bal   trade%  wallet%  market%
    (500000,    0.005,   0.15,    0.05),   # $500k+: 0.5% trade, 15% wallet, 5% market
    (250000,    0.008,   0.20,    0.06),   # $250k+: tiny trades, wide spread
    (100000,    0.01,    0.25,    0.08),   # $100k+: institutional discipline
    (50000,     0.02,    0.30,    0.10),   # $50k+:  tightening up
    (25000,     0.03,    0.35,    0.12),   # $25k+:  moderate concentration
    (10000,     0.05,    0.40,    0.15),   # $10k+:  balanced risk
    # Below $10k: use base limits (0.15 / 0.50 / 0.15) for max growth
]


def get_risk_limits_for_balance(balance: float) -> dict:
    """Return the risk limits appropriate for the given balance."""
    for min_bal, trade_pct, wallet_pct, market_pct in RISK_SCALING_TIERS:
        if balance >= min_bal:
            return {
                "max_trade_pct": trade_pct,
                "max_wallet_pct": wallet_pct,
                "max_market_pct": market_pct,
            }
    # Fallback to base limits (small capital — aggressive growth)
    return {
        "max_trade_pct": MAX_TRADE_PCT,
        "max_wallet_pct": MAX_WALLET_PCT,
        "max_market_pct": MAX_MARKET_PCT,
    }

# ── PAPER TRADING SETTINGS ──────────────────────────────────────────────────

# Starting virtual balance for paper trading
PAPER_STARTING_BALANCE = float(os.getenv("PAPER_STARTING_BALANCE", "186.0"))

# Simulated slippage (% of price movement on fills)
PAPER_SLIPPAGE_BPS = float(os.getenv("PAPER_SLIPPAGE_BPS", "10"))  # 10 bps = 0.1%

# Simulated fill delay (seconds)
PAPER_FILL_DELAY = float(os.getenv("PAPER_FILL_DELAY", "1.0"))

# ── MARKET DATA ─────────────────────────────────────────────────────────────

# How often to poll for new trades (seconds)
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))

# How often to check for resolved markets (seconds)
RESOLUTION_CHECK_INTERVAL = int(os.getenv("RESOLUTION_CHECK_INTERVAL", "60"))

# Enable WebSocket price feed for real-time market data
USE_WEBSOCKET_PRICES = os.getenv("USE_WEBSOCKET_PRICES", "false").lower() == "true"

# ── PROFIT ALLOCATION ─────────────────────────────────────────────────────────

# Percentage of realized profits to allocate to each fund
ALLOC_CHARITY_PCT = float(os.getenv("ALLOC_CHARITY_PCT", "10")) / 100
ALLOC_SAVINGS_PCT = float(os.getenv("ALLOC_SAVINGS_PCT", "5")) / 100
ALLOC_FAMILY_PCT = float(os.getenv("ALLOC_FAMILY_PCT", "5")) / 100

# Fund wallet addresses (for on-chain transfers)
ALLOC_CHARITY_WALLET = os.getenv("ALLOC_CHARITY_WALLET", "")
ALLOC_SAVINGS_WALLET = os.getenv("ALLOC_SAVINGS_WALLET", "")
ALLOC_FAMILY_WALLET = os.getenv("ALLOC_FAMILY_WALLET", "")

ALLOCATION_FUNDS = [
    {"name": "Charity", "pct": ALLOC_CHARITY_PCT, "wallet": ALLOC_CHARITY_WALLET},
    {"name": "Savings", "pct": ALLOC_SAVINGS_PCT, "wallet": ALLOC_SAVINGS_WALLET},
    {"name": "Family", "pct": ALLOC_FAMILY_PCT, "wallet": ALLOC_FAMILY_WALLET},
]

# ── MULTI-FUND PORTFOLIOS ─────────────────────────────────────────────────
# Each fund independently copies trades with its own balance and risk params.
# "pct" = share of PAPER_STARTING_BALANCE at init.
#
# Main fund starts with 100% of the balance. Allocation funds (charity,
# savings, family) start empty and are funded from main's realized profits
# once main exceeds ALLOCATION_THRESHOLD.

ALLOCATION_THRESHOLD = float(os.getenv("ALLOCATION_THRESHOLD", "1000.0"))

FUND_CONFIGS = {
    "main": {
        "pct": 1.00,
        "max_trade_pct": MAX_TRADE_PCT,
        "max_wallet_pct": MAX_WALLET_PCT,
        "max_market_pct": MAX_MARKET_PCT,
        "copy_strategy": COPY_STRATEGY,
    },
    "charity": {
        "pct": 0.00,
        "max_trade_pct": float(os.getenv("FUND_CHARITY_MAX_TRADE_PCT", "0.10")),
        "max_wallet_pct": float(os.getenv("FUND_CHARITY_MAX_WALLET_PCT", "0.50")),
        "max_market_pct": float(os.getenv("FUND_CHARITY_MAX_MARKET_PCT", "0.15")),
        "copy_strategy": os.getenv("FUND_CHARITY_STRATEGY", "tiered_fixed"),
    },
    "savings": {
        "pct": 0.00,
        "max_trade_pct": float(os.getenv("FUND_SAVINGS_MAX_TRADE_PCT", "0.10")),
        "max_wallet_pct": float(os.getenv("FUND_SAVINGS_MAX_WALLET_PCT", "0.50")),
        "max_market_pct": float(os.getenv("FUND_SAVINGS_MAX_MARKET_PCT", "0.15")),
        "copy_strategy": os.getenv("FUND_SAVINGS_STRATEGY", "tiered_fixed"),
    },
    "family": {
        "pct": 0.00,
        "max_trade_pct": float(os.getenv("FUND_FAMILY_MAX_TRADE_PCT", "0.10")),
        "max_wallet_pct": float(os.getenv("FUND_FAMILY_MAX_WALLET_PCT", "0.50")),
        "max_market_pct": float(os.getenv("FUND_FAMILY_MAX_MARKET_PCT", "0.15")),
        "copy_strategy": os.getenv("FUND_FAMILY_STRATEGY", "tiered_fixed"),
    },
}

# Polymarket API endpoints
POLYMARKET_DATA_API = "https://data-api.polymarket.com"
POLYMARKET_CLOB = "https://clob.polymarket.com"

# ── MASTER ENCRYPTION KEY ─────────────────────────────────────────────────────
# Required for storing encrypted API credentials per account.
# Generate with: python -c "from credentials import generate_master_key; print(generate_master_key())"
DENSEWEALTH_MASTER_KEY = os.getenv("DENSEWEALTH_MASTER_KEY", "")
if not DENSEWEALTH_MASTER_KEY:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "DENSEWEALTH_MASTER_KEY is not set — encrypted credential storage will not work. "
        "Generate one with: python -c \"from credentials import generate_master_key; print(generate_master_key())\""
    )

# ── RESERVE SYSTEM ────────────────────────────────────────────────────────────
# Default reserve percentage (0-100%) - portion of balance excluded from trading
DEFAULT_RESERVE_PCT = float(os.getenv("DEFAULT_RESERVE_PCT", "0"))

# Reserve cycling schedules
RESERVE_CYCLE_SCHEDULES = ["disabled", "hourly", "daily", "weekly"]

# Default cycle percentage - how much of reserve to redistribute per cycle
DEFAULT_CYCLE_PCT = float(os.getenv("DEFAULT_CYCLE_PCT", "10"))

# ── MULTI-ACCOUNT DEFAULTS ────────────────────────────────────────────────────
# Default trading profile for new accounts
DEFAULT_ACCOUNT_PROFILE = {
    "auto_trade_enabled": False,
    "copy_strategy": "tiered_fixed",
    "max_trade_pct": 0.15,
    "max_wallet_pct": 0.50,
    "max_market_pct": 0.15,
    "risk_level": "moderate",  # conservative / moderate / aggressive
}

# Risk level presets (base values — dynamic scaling overrides these at higher balances)
RISK_PRESETS = {
    "conservative": {
        "max_trade_pct": 0.05,
        "max_wallet_pct": 0.25,
        "max_market_pct": 0.10,
    },
    "moderate": {
        "max_trade_pct": 0.15,
        "max_wallet_pct": 0.50,
        "max_market_pct": 0.15,
    },
    "aggressive": {
        "max_trade_pct": 0.25,
        "max_wallet_pct": 0.75,
        "max_market_pct": 0.25,
    },
}

# ── STRATEGY TRANSITION THRESHOLDS ────────────────────────────────────────
# At higher balances, consider switching from tiered_fixed to proportional
# for smoother scaling. These thresholds are advisory — logged as suggestions.
STRATEGY_SWITCH_THRESHOLD = float(os.getenv("STRATEGY_SWITCH_THRESHOLD", "100000"))
# Above this balance, the bot logs a recommendation to switch to proportional
# strategy which mirrors trader allocation % rather than fixed dollar amounts.
# At $100k+ with fixed tiers, you need high signal volume to stay deployed.
# Proportional automatically sizes to the opportunity.
