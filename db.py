# db.py — position tracking database
#
# SQLite-based storage for:
#   - Positions (our current holdings)
#   - Trade history (log of all executed trades)
#   - Paper trading balance

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "densewealth.db"


@contextmanager
def get_conn():
    """Context manager for database connections."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database tables."""
    with get_conn() as conn:
        # Positions table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                trader_wallet TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT '',
                shares REAL NOT NULL,
                usdc_spent REAL NOT NULL,
                fund_id TEXT NOT NULL DEFAULT 'main',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(market_id, trader_wallet, fund_id)
            )
        """)

        # Trade history
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                trader_wallet TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT '',
                shares REAL NOT NULL,
                usdc_amount REAL NOT NULL,
                price REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                mode TEXT NOT NULL,
                success INTEGER NOT NULL DEFAULT 1,
                fund_id TEXT NOT NULL DEFAULT 'main'
            )
        """)

        # Paper trading account balance (legacy — kept for backwards compat)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                balance_usdc REAL NOT NULL,
                total_pnl REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Multi-fund accounts (each fund has its own balance)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fund_accounts (
                fund_id TEXT PRIMARY KEY,
                balance_usdc REAL NOT NULL,
                total_pnl REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Performance index for trade history queries
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_trade_history_timestamp
            ON trade_history (timestamp DESC)
        """)

        # Fund allocations (profit splits)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS allocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fund_name TEXT NOT NULL,
                amount REAL NOT NULL,
                source_market TEXT,
                source_pnl REAL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                transferred_at TIMESTAMP
            )
        """)

        # Monthly distributions (fund → wallet sweeps on 1st of month)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS monthly_distributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                month TEXT NOT NULL,
                fund_id TEXT NOT NULL,
                amount REAL NOT NULL,
                wallet_address TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'paper',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(month, fund_id)
            )
        """)

        # Watched trades (to avoid duplicates)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watched_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trader_wallet TEXT NOT NULL,
                market_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                shares REAL NOT NULL,
                timestamp INTEGER NOT NULL,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trader_wallet, market_id, timestamp)
            )
        """)

        # Tracked wallets (dynamic, editable from dashboard)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_wallets (
                address TEXT PRIMARY KEY,
                label TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                allocation_pct REAL DEFAULT NULL,
                account_id INTEGER DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── MULTI-ACCOUNT TABLES ─────────────────────────────────────────────

        # Trading accounts (separate portfolios with own credentials)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trading_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                account_type TEXT NOT NULL DEFAULT 'trading',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Account credentials (encrypted API keys)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS account_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                credential_type TEXT NOT NULL,
                encrypted_value TEXT NOT NULL,
                nonce TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE,
                UNIQUE(account_id, platform, credential_type)
            )
        """)

        # Account trading profiles (per-account risk settings)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS account_trading_profiles (
                account_id INTEGER PRIMARY KEY,
                auto_trade_enabled INTEGER NOT NULL DEFAULT 0,
                copy_strategy TEXT NOT NULL DEFAULT 'tiered_fixed',
                max_trade_pct REAL NOT NULL DEFAULT 0.15,
                max_wallet_pct REAL NOT NULL DEFAULT 0.50,
                max_market_pct REAL NOT NULL DEFAULT 0.15,
                risk_level TEXT NOT NULL DEFAULT 'moderate',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
            )
        """)

        # Account balances (separate from fund_accounts)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS account_balances (
                account_id INTEGER PRIMARY KEY,
                balance_usdc REAL NOT NULL DEFAULT 0,
                total_pnl REAL NOT NULL DEFAULT 0,
                reserve_balance REAL NOT NULL DEFAULT 0,
                total_trades INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
            )
        """)

        # Reserve configuration (per-account reserve settings)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reserve_config (
                account_id INTEGER PRIMARY KEY,
                reserve_pct REAL NOT NULL DEFAULT 0,
                cycling_enabled INTEGER NOT NULL DEFAULT 0,
                cycle_schedule TEXT NOT NULL DEFAULT 'disabled',
                cycle_pct REAL NOT NULL DEFAULT 10,
                last_cycle_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
            )
        """)

        # Settings change requests (viewer submits, operator approves)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER,
                request_type TEXT NOT NULL,
                category TEXT NOT NULL,
                current_value TEXT,
                requested_value TEXT NOT NULL,
                reason TEXT DEFAULT '',
                submitted_by TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewed_by TEXT,
                review_note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP,
                FOREIGN KEY (account_id) REFERENCES trading_accounts(id) ON DELETE CASCADE
            )
        """)

        # Market liquidity cache — stores volume/liquidity data fetched from Polymarket
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_cache (
                market_id TEXT PRIMARY KEY,
                volume_24h REAL DEFAULT 0,
                volume_total REAL DEFAULT 0,
                liquidity REAL DEFAULT 0,
                best_bid REAL DEFAULT 0,
                best_ask REAL DEFAULT 0,
                spread REAL DEFAULT 0,
                last_trade_price REAL DEFAULT 0,
                active INTEGER DEFAULT 1,
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── FUTURES COPY-TRADING TABLES ────────────────────────────────────────

        # Futures positions (BTC-PERP from Hyperliquid)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS futures_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER DEFAULT 1,
                symbol TEXT NOT NULL,
                trader_wallet TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                size REAL NOT NULL,
                leverage REAL NOT NULL DEFAULT 1,
                margin_used REAL NOT NULL,
                unrealized_pnl REAL DEFAULT 0,
                liquidation_price REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(account_id, symbol, trader_wallet)
            )
        """)

        # Futures trade history
        conn.execute("""
            CREATE TABLE IF NOT EXISTS futures_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id INTEGER DEFAULT 1,
                symbol TEXT NOT NULL,
                trader_wallet TEXT NOT NULL,
                side TEXT NOT NULL,
                size REAL NOT NULL,
                price REAL NOT NULL,
                leverage REAL NOT NULL,
                realized_pnl REAL DEFAULT 0,
                mode TEXT NOT NULL,
                success INTEGER DEFAULT 1,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Futures paper account
        conn.execute("""
            CREATE TABLE IF NOT EXISTS futures_accounts (
                account_id INTEGER PRIMARY KEY DEFAULT 1,
                balance_usdc REAL DEFAULT 1000,
                margin_used REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Tracked Hyperliquid wallets for futures
        conn.execute("""
            CREATE TABLE IF NOT EXISTS futures_tracked_wallets (
                address TEXT PRIMARY KEY,
                label TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Futures deduplication (using tx_hash from Hyperliquid)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS futures_watched_trades (
                trader_wallet TEXT NOT NULL,
                tx_hash TEXT NOT NULL,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(trader_wallet, tx_hash)
            )
        """)

        # ── STRATEGY & BACKTESTING TABLES ────────────────────────────────────

        # User-defined trading strategies
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                is_active INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Individual rules for each strategy
        conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                rule_type TEXT NOT NULL,
                rule_name TEXT NOT NULL,
                rule_config TEXT NOT NULL,
                priority INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
            )
        """)

        # Cached trader data from leaderboard
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trader_cache (
                wallet_address TEXT PRIMARY KEY,
                username TEXT DEFAULT '',
                pnl REAL DEFAULT 0,
                volume REAL DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                last_fetched TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Stored backtest results
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER,
                trader_wallet TEXT,
                starting_balance REAL,
                final_balance REAL,
                total_pnl REAL,
                win_rate REAL,
                max_drawdown REAL,
                result_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── FULL MOON HARVEST SYSTEM ────────────────────────────────────────────
        # Tracks pending profits that accumulate until the full moon, then distribute

        # Pending harvest amounts (funds stay in pool, amounts tracked here)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_harvest (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fund_id TEXT NOT NULL,
                amount REAL NOT NULL,
                source_market TEXT DEFAULT '',
                source_pnl REAL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Full moon harvest history
        conn.execute("""
            CREATE TABLE IF NOT EXISTS harvest_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                harvest_date TEXT NOT NULL,
                fund_id TEXT NOT NULL,
                amount REAL NOT NULL,
                full_moon_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Pending settlements (funds allocated but not yet locked)
        # Status: pending (tradable) → settled (locked) → distributed (sent)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_settlements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fund_id TEXT NOT NULL,
                amount REAL NOT NULL,
                full_moon_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                settle_at TIMESTAMP NOT NULL,
                distribute_at TIMESTAMP,
                settled_at TIMESTAMP,
                distributed_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Migrations ──────────────────────────────────────────────────────
        # Add outcome column to existing tables (safe to run multiple times)
        for table in ("positions", "trade_history"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN outcome TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Add fund_id column to positions and trade_history
        for table in ("positions", "trade_history"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN fund_id TEXT NOT NULL DEFAULT 'main'")
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Unique index for per-fund positions (allows same market in multiple funds)
        try:
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_fund
                ON positions (market_id, trader_wallet, fund_id)
            """)
        except sqlite3.OperationalError:
            pass

        # ── Multi-account migrations ──────────────────────────────────────────
        # Add allocation_pct and account_id to tracked_wallets
        for col, default in [("allocation_pct", "NULL"), ("account_id", "NULL")]:
            try:
                conn.execute(f"ALTER TABLE tracked_wallets ADD COLUMN {col} REAL DEFAULT {default}")
            except sqlite3.OperationalError:
                pass

        # Add account_id to positions and trade_history
        for table in ("positions", "trade_history"):
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN account_id INTEGER DEFAULT NULL")
            except sqlite3.OperationalError:
                pass

        # Add activation_status to trading_accounts (pending | activated | suspended)
        try:
            conn.execute("ALTER TABLE trading_accounts ADD COLUMN activation_status TEXT NOT NULL DEFAULT 'pending'")
        except sqlite3.OperationalError:
            pass  # Column already exists

        # Create default "main" account if none exists
        conn.execute("""
            INSERT OR IGNORE INTO trading_accounts (id, name, slug, description, account_type)
            VALUES (1, 'Main', 'main', 'Default trading account', 'trading')
        """)

    log.info("Database initialized at %s", DB_PATH)


# ── POSITIONS ───────────────────────────────────────────────────────────────


def get_position(market_id: str, trader_wallet: str, fund_id: str = "main") -> Optional[dict]:
    """Get current position for a market and fund."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE market_id = ? AND trader_wallet = ? AND fund_id = ?",
            (market_id, trader_wallet, fund_id),
        ).fetchone()
        return dict(row) if row else None


def upsert_position(
    market_id: str,
    trader_wallet: str,
    token_id: str,
    side: str,
    shares: float,
    usdc_spent: float,
    outcome: str = "",
    fund_id: str = "main",
) -> None:
    """Insert or update a position for a specific fund."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO positions (market_id, trader_wallet, token_id, side, outcome, shares, usdc_spent, fund_id, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(market_id, trader_wallet, fund_id) DO UPDATE SET
                token_id = excluded.token_id,
                side = excluded.side,
                outcome = excluded.outcome,
                shares = excluded.shares,
                usdc_spent = excluded.usdc_spent,
                updated_at = CURRENT_TIMESTAMP
            """,
            (market_id, trader_wallet, token_id, side, outcome, shares, usdc_spent, fund_id),
        )


def delete_position(market_id: str, trader_wallet: str, fund_id: str = "main") -> None:
    """Delete a position (when fully closed)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM positions WHERE market_id = ? AND trader_wallet = ? AND fund_id = ?",
            (market_id, trader_wallet, fund_id),
        )


def get_all_positions(fund_id: Optional[str] = None) -> list[dict]:
    """Get all active positions. Optionally filter by fund_id."""
    with get_conn() as conn:
        if fund_id:
            rows = conn.execute(
                "SELECT * FROM positions WHERE fund_id = ? ORDER BY updated_at DESC",
                (fund_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM positions ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]


def get_wallet_exposure(trader_wallet: str, fund_id: str = "main") -> float:
    """Get total USDC spent across all positions for a specific tracked wallet and fund."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(usdc_spent), 0) as total FROM positions WHERE trader_wallet = ? AND fund_id = ?",
            (trader_wallet, fund_id),
        ).fetchone()
        return row["total"]


# ── TRADE HISTORY ───────────────────────────────────────────────────────────


def log_trade(
    market_id: str,
    trader_wallet: str,
    token_id: str,
    side: str,
    shares: float,
    usdc_amount: float,
    mode: str,
    success: bool = True,
    price: Optional[float] = None,
    outcome: str = "",
    fund_id: str = "main",
) -> None:
    """Log a trade to history."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO trade_history (market_id, trader_wallet, token_id, side, outcome, shares, usdc_amount, price, mode, success, fund_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (market_id, trader_wallet, token_id, side, outcome, shares, usdc_amount, price, mode, 1 if success else 0, fund_id),
        )


def get_trade_history(limit: int = 50) -> list[dict]:
    """Get recent trade history."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trade_history ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_pnl_by_period(minutes: int | None = None) -> dict:
    """
    Calculate P&L stats for a given time window.
    minutes=None means all-time. Returns dict with pnl, trades, wins, losses,
    first/last trade timestamps, and elapsed minutes.
    """
    with get_conn() as conn:
        where = "WHERE success = 1"
        params: list = []
        if minutes is not None:
            where += " AND timestamp >= datetime('now', ?)"
            params.append(f"-{minutes} minutes")

        row = conn.execute(f"""
            SELECT
                COALESCE(SUM(CASE WHEN side = 'RESOLVE' AND usdc_amount > 0 THEN usdc_amount ELSE 0 END), 0) as payouts,
                COALESCE(SUM(CASE WHEN side = 'BUY' THEN usdc_amount ELSE 0 END), 0) as spent,
                COALESCE(SUM(CASE WHEN side = 'SELL' THEN usdc_amount ELSE 0 END), 0) as sell_proceeds,
                COUNT(CASE WHEN side = 'BUY' THEN 1 END) as buys,
                COUNT(CASE WHEN side = 'RESOLVE' AND usdc_amount > 0 THEN 1 END) as wins,
                COUNT(CASE WHEN side = 'RESOLVE' AND usdc_amount = 0 THEN 1 END) as losses,
                MIN(timestamp) as first_trade,
                MAX(timestamp) as last_trade
            FROM trade_history {where}
        """, params).fetchone()

        payouts = row["payouts"] or 0
        spent = row["spent"] or 0
        sell_proceeds = row["sell_proceeds"] or 0
        pnl = payouts + sell_proceeds - spent

        # Calculate actual elapsed minutes from first to last trade
        elapsed_row = conn.execute(f"""
            SELECT
                (julianday(MAX(timestamp)) - julianday(MIN(timestamp))) * 24 * 60 as elapsed_min
            FROM trade_history {where}
        """, params).fetchone()
        elapsed_min = elapsed_row["elapsed_min"] if elapsed_row and elapsed_row["elapsed_min"] else 0

        return {
            "pnl": pnl,
            "payouts": payouts,
            "spent": spent,
            "sell_proceeds": sell_proceeds,
            "buys": row["buys"] or 0,
            "wins": row["wins"] or 0,
            "losses": row["losses"] or 0,
            "first_trade": row["first_trade"],
            "last_trade": row["last_trade"],
            "elapsed_min": elapsed_min,
        }


def get_trades_for_simulation(
    hours: int = 24,
    trader_wallet: Optional[str] = None,
) -> list[dict]:
    """
    Fetch trade history for simulation replay.
    Returns successful trades ordered chronologically (oldest first).
    """
    with get_conn() as conn:
        query = """
            SELECT * FROM trade_history
            WHERE success = 1
              AND timestamp >= datetime('now', ?)
        """
        params: list = [f"-{hours} hours"]

        if trader_wallet:
            query += " AND trader_wallet = ?"
            params.append(trader_wallet)

        query += " ORDER BY timestamp ASC"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_trader_performance() -> list[dict]:
    """Per-trader stats from resolved trades (wins, losses, P&L)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                trader_wallet,
                SUM(CASE WHEN side = 'RESOLVE' AND price = 1.0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN side = 'RESOLVE' AND price = 0.0 THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN side = 'BUY' THEN 1 ELSE 0 END) as total_buys,
                SUM(CASE WHEN side = 'BUY' THEN usdc_amount ELSE 0 END) as total_deployed,
                SUM(CASE WHEN side = 'RESOLVE' THEN usdc_amount ELSE 0 END) as total_payouts,
                SUM(CASE WHEN side = 'RESOLVE' AND price = 1.0 THEN usdc_amount ELSE 0 END) as won_payouts,
                SUM(CASE WHEN side = 'RESOLVE' AND price = 0.0 THEN usdc_amount ELSE 0 END) as lost_cost
            FROM trade_history
            WHERE success = 1
            GROUP BY trader_wallet
        """).fetchall()
        return [dict(row) for row in rows]


# ── FUND ACCOUNTS (multi-portfolio) ────────────────────────────────────────


def init_fund_accounts(starting_balance: float, fund_configs: dict) -> None:
    """Initialize fund accounts from config. Each fund gets pct * starting_balance."""
    with get_conn() as conn:
        for fund_id, cfg in fund_configs.items():
            fund_balance = starting_balance * cfg["pct"]
            conn.execute(
                """
                INSERT OR IGNORE INTO fund_accounts (fund_id, balance_usdc, total_pnl, total_trades)
                VALUES (?, ?, 0, 0)
                """,
                (fund_id, fund_balance),
            )


def get_fund_balance(fund_id: str) -> float:
    """Get current balance for a specific fund."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT balance_usdc FROM fund_accounts WHERE fund_id = ?",
            (fund_id,),
        ).fetchone()
        return row["balance_usdc"] if row else 0.0


def update_fund_balance(fund_id: str, new_balance: float, trade_count_delta: int = 0, pnl_delta: float = 0) -> None:
    """Update a fund's balance."""
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE fund_accounts SET
                balance_usdc = ?,
                total_trades = total_trades + ?,
                total_pnl = total_pnl + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE fund_id = ?
            """,
            (new_balance, trade_count_delta, pnl_delta, fund_id),
        )


def get_fund_stats(fund_id: str) -> dict:
    """Get stats for a specific fund."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM fund_accounts WHERE fund_id = ?",
            (fund_id,),
        ).fetchone()
        return dict(row) if row else {}


def get_all_fund_stats() -> list[dict]:
    """Get stats for all funds."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM fund_accounts ORDER BY fund_id").fetchall()
        return [dict(row) for row in rows]


# ── LEGACY PAPER ACCOUNT WRAPPERS ─────────────────────────────────────────
# These delegate to fund_accounts with fund_id="main" for backwards compat.


def init_paper_account(starting_balance: float) -> None:
    """Initialize paper trading account (legacy — delegates to fund_accounts)."""
    from config import FUND_CONFIGS
    init_fund_accounts(starting_balance, FUND_CONFIGS)
    # Also keep legacy table in sync
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO paper_account (id, balance_usdc, total_pnl, total_trades)
            VALUES (1, ?, 0, 0)
            """,
            (starting_balance * FUND_CONFIGS["main"]["pct"],),
        )


def get_paper_balance() -> float:
    """Get current paper trading balance (main fund)."""
    return get_fund_balance("main")


def update_paper_balance(new_balance: float, trade_count_delta: int = 0, pnl_delta: float = 0) -> None:
    """Update paper trading balance (main fund)."""
    update_fund_balance("main", new_balance, trade_count_delta, pnl_delta)


def get_paper_stats() -> dict:
    """Get paper trading account stats (main fund)."""
    return get_fund_stats("main")


# ── WATCHED TRADES (deduplication) ──────────────────────────────────────────


def is_trade_processed(trader_wallet: str, market_id: str, timestamp: int) -> bool:
    """Check if we've already processed this trade."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM watched_trades WHERE trader_wallet = ? AND market_id = ? AND timestamp = ?",
            (trader_wallet, market_id, timestamp),
        ).fetchone()
        return row is not None


def mark_trade_processed(
    trader_wallet: str,
    market_id: str,
    token_id: str,
    side: str,
    shares: float,
    timestamp: int,
) -> None:
    """Mark a trade as processed."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO watched_trades (trader_wallet, market_id, token_id, side, shares, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (trader_wallet, market_id, token_id, side, shares, timestamp),
        )


def get_last_trade_time(trader_wallet: str) -> int | None:
    """Get the most recent trade timestamp for a wallet (unix epoch)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(timestamp) as last_ts FROM watched_trades WHERE trader_wallet = ?",
            (trader_wallet,),
        ).fetchone()
        return row["last_ts"] if row and row["last_ts"] else None


def get_wallet_activity(wallets: list[str], inactive_minutes: int = 7) -> dict:
    """
    Check which wallets are active vs inactive.
    Returns dict with 'active' and 'inactive' wallet lists.
    """
    import time
    now = int(time.time() * 1000)  # Current time in ms (Polymarket uses ms timestamps)
    cutoff = now - (inactive_minutes * 60 * 1000)

    active = []
    inactive = []

    for wallet in wallets:
        last_trade = get_last_trade_time(wallet)
        if last_trade and last_trade >= cutoff:
            active.append(wallet)
        else:
            inactive.append(wallet)

    return {"active": active, "inactive": inactive}


# ── PROFIT DISTRIBUTION (Full Moon Harvest System) ───────────────────────────
#
# Traditional farming approach: profits accumulate and stay in the trading pool
# for maximum compounding, then are harvested on the full moon.


def distribute_profit(profit_amount: float, source_market: str = "profit_share") -> None:
    """
    Track profit allocation for full moon harvest.

    Instead of immediately moving funds, we track pending harvest amounts.
    Funds stay in the main trading pool for compounding until the full moon,
    when they are harvested (distributed to allocation funds).

    Called after profitable sells or winning resolutions on the main fund.
    Only tracks allocations if main balance is above ALLOCATION_THRESHOLD.
    """
    if profit_amount <= 0.01:
        return

    from config import (
        ALLOCATION_THRESHOLD,
        ALLOC_CHARITY_PCT,
        ALLOC_FAMILY_PCT,
        ALLOC_SAVINGS_PCT,
    )

    main_balance = get_fund_balance("main")
    if main_balance < ALLOCATION_THRESHOLD:
        return

    # Calculate allocation amounts (but don't move funds yet)
    distributions = [
        ("charity", profit_amount * ALLOC_CHARITY_PCT),
        ("savings", profit_amount * ALLOC_SAVINGS_PCT),
        ("family", profit_amount * ALLOC_FAMILY_PCT),
    ]

    total_pending = sum(amt for _, amt in distributions)
    if total_pending < 0.01:
        return

    # Track pending harvest amounts (funds stay in pool for compounding)
    for fund_id, amount in distributions:
        if amount >= 0.01:
            add_pending_harvest(fund_id, amount, source_market, profit_amount)

    log.info(
        "Pending harvest tracked: $%.2f profit → $%.2f pending (%s) - awaiting full moon",
        profit_amount,
        total_pending,
        ", ".join(f"{fid}=${amt:.2f}" for fid, amt in distributions if amt >= 0.01),
    )


def trigger_full_moon_harvest() -> dict:
    """
    Trigger the full moon harvest - distribute all pending funds.

    This is called when the full moon occurs (checked by the harvest scheduler).
    Moves accumulated funds from main to allocation funds.

    Returns dict with harvest results.
    """
    from full_moon import get_full_moon_for_date, is_full_moon_day
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    full_moon_date = get_full_moon_for_date(now)

    if not full_moon_date:
        return {
            "harvested": False,
            "reason": "Not a full moon day",
            "next_check": "Wait for full moon",
        }

    results = {
        "harvested": True,
        "full_moon_date": full_moon_date,
        "funds": {},
        "total_harvested": 0,
    }

    # Harvest each fund
    for fund_id in ["charity", "savings", "family"]:
        if is_harvest_done_for_moon(full_moon_date, fund_id):
            results["funds"][fund_id] = {"amount": 0, "status": "already_harvested"}
            continue

        amount = harvest_pending_funds(fund_id, full_moon_date)
        results["funds"][fund_id] = {"amount": amount, "status": "harvested"}
        results["total_harvested"] += amount

    log.info(
        "🌕 Full Moon Harvest complete: $%.2f distributed on %s",
        results["total_harvested"],
        full_moon_date,
    )

    return results


def get_harvest_dashboard() -> dict:
    """
    Get harvest status for dashboard display.

    Shows pending amounts building up and countdown to full moon.
    Includes settlement status (pending = tradable, settled = locked).
    """
    from full_moon import get_harvest_status, format_moon_phase_display
    from config import SETTLEMENT_HOURS_BEFORE

    moon_status = get_harvest_status()
    pending = get_all_pending_harvests()

    # Calculate totals
    pending_by_fund = {p["fund_id"]: p["pending_amount"] for p in pending}
    total_pending = sum(p["pending_amount"] for p in pending)

    # Get settlement status (allocated funds in pending/settled state)
    settlement_info = get_settlement_dashboard()

    return {
        "moon_phase": format_moon_phase_display(moon_status["days_until_full_moon"]),
        "days_until_harvest": moon_status["days_until_full_moon"],
        "next_harvest_date": moon_status["next_full_moon_date"],
        "is_harvest_day": moon_status["is_full_moon_day"],
        "harvest_window_open": moon_status["harvest_window_open"],
        "transit_time": moon_status.get("transit_time"),  # Moon's peak from NASA data
        "harvest_ready": moon_status.get("harvest_ready", False),  # Within 30min of transit
        "pending_harvest": {
            "charity": pending_by_fund.get("charity", 0),
            "savings": pending_by_fund.get("savings", 0),
            "family": pending_by_fund.get("family", 0),
            "total": total_pending,
        },
        "recent_harvests": get_harvest_history(5),
        # Settlement policy info
        "settlement": {
            "policy": f"Funds settle {SETTLEMENT_HOURS_BEFORE}h before distribution",
            "pending_tradable": settlement_info["pending_total"],  # Still available for trading
            "settled_locked": settlement_info["settled_total"],  # Locked, awaiting distribution
            "next_settlement": settlement_info["next_settlement"],
            "next_distribution": settlement_info["next_distribution"],
        },
    }


# ── FUND ALLOCATIONS ─────────────────────────────────────────────────────────


def record_allocation(fund_name: str, amount: float, source_market: str = "", source_pnl: float = 0) -> None:
    """Record a profit allocation to a fund."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO allocations (fund_name, amount, source_market, source_pnl)
            VALUES (?, ?, ?, ?)
            """,
            (fund_name, amount, source_market, source_pnl),
        )


def get_allocation_summary() -> list[dict]:
    """Get cumulative allocation totals per fund."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                fund_name,
                SUM(amount) as total_allocated,
                SUM(CASE WHEN status = 'pending' THEN amount ELSE 0 END) as pending,
                SUM(CASE WHEN status = 'transferred' THEN amount ELSE 0 END) as transferred,
                COUNT(*) as num_allocations
            FROM allocations
            GROUP BY fund_name
        """).fetchall()
        return [dict(row) for row in rows]


def get_recent_allocations(limit: int = 20) -> list[dict]:
    """Get recent allocation entries."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM allocations ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


# ── FULL MOON HARVEST ────────────────────────────────────────────────────────


def add_pending_harvest(fund_id: str, amount: float, source_market: str = "", source_pnl: float = 0) -> None:
    """
    Track a pending harvest amount for a fund.
    Funds stay in the trading pool and compound until the full moon.
    """
    if amount < 0.01:
        return
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO pending_harvest (fund_id, amount, source_market, source_pnl)
            VALUES (?, ?, ?, ?)
            """,
            (fund_id, amount, source_market, source_pnl),
        )


def get_pending_harvest_total(fund_id: str) -> float:
    """Get total pending harvest amount for a fund."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM pending_harvest WHERE fund_id = ?",
            (fund_id,),
        ).fetchone()
        return row["total"] if row else 0.0


def get_all_pending_harvests() -> list[dict]:
    """Get pending harvest totals for all funds."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                fund_id,
                SUM(amount) as pending_amount,
                COUNT(*) as num_entries,
                MIN(created_at) as earliest,
                MAX(created_at) as latest
            FROM pending_harvest
            GROUP BY fund_id
        """).fetchall()
        return [dict(row) for row in rows]


def harvest_pending_funds(fund_id: str, full_moon_date: str) -> float:
    """
    Harvest pending funds for a fund on the full moon.

    NEW POLICY: Funds are marked as 'pending' and remain available for trading.
    They are settled (locked) 3 hours before distribution.

    Returns the amount harvested (scheduled for settlement).
    """
    pending = get_pending_harvest_total(fund_id)
    if pending < 0.01:
        return 0.0

    from datetime import datetime, timedelta
    from config import SETTLEMENT_HOURS_BEFORE

    # Get main balance and ensure we have enough
    main_balance = get_fund_balance("main")
    harvest_amount = min(pending, main_balance)

    if harvest_amount < 0.01:
        return 0.0

    now = datetime.now()

    # Schedule settlement 3 hours before distribution
    # Distribution happens at next scheduled time (e.g., 24 hours from now)
    distribute_at = now + timedelta(hours=24)
    settle_at = distribute_at - timedelta(hours=SETTLEMENT_HOURS_BEFORE)

    # Create pending settlement (funds stay in pool until settle_at)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO pending_settlements
                (fund_id, amount, full_moon_date, status, settle_at, distribute_at)
            VALUES (?, ?, ?, 'pending', ?, ?)
            """,
            (fund_id, harvest_amount, full_moon_date,
             settle_at.isoformat(), distribute_at.isoformat()),
        )
        # Clear the pending harvest entries for this fund
        conn.execute("DELETE FROM pending_harvest WHERE fund_id = ?", (fund_id,))

    # Record the harvest event (but funds not moved yet)
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO harvest_history (harvest_date, fund_id, amount, full_moon_date, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (now.strftime("%Y-%m-%d"), fund_id, harvest_amount, full_moon_date),
        )

    log.info(
        "Full Moon Harvest: %s scheduled $%.2f (pending until %s, settles at %s)",
        fund_id, harvest_amount, distribute_at.strftime("%H:%M"), settle_at.strftime("%H:%M")
    )

    return harvest_amount


def get_pending_settlements(status: str = "pending") -> list[dict]:
    """Get pending settlements by status."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_settlements WHERE status = ? ORDER BY settle_at",
            (status,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_total_pending_settlement_amount() -> float:
    """Get total amount in pending settlements (still tradable)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM pending_settlements WHERE status = 'pending'"
        ).fetchone()
        return row["total"] if row else 0.0


def get_total_settled_amount() -> float:
    """Get total amount in settled state (locked, not tradable)."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) as total FROM pending_settlements WHERE status = 'settled'"
        ).fetchone()
        return row["total"] if row else 0.0


def settle_pending_allocations() -> list[dict]:
    """
    Settle any pending allocations that have reached their settle_at time.

    Moves funds from main balance to locked state (no longer tradable).
    Returns list of settlements that were processed.
    """
    from datetime import datetime

    now = datetime.now()
    settled = []

    with get_conn() as conn:
        # Find pending settlements that need to be settled
        rows = conn.execute(
            """
            SELECT * FROM pending_settlements
            WHERE status = 'pending' AND settle_at <= ?
            """,
            (now.isoformat(),),
        ).fetchall()

        for row in rows:
            settlement = dict(row)
            fund_id = settlement["fund_id"]
            amount = settlement["amount"]

            # Lock the funds: debit from main balance
            main_balance = get_fund_balance("main")
            if main_balance >= amount:
                update_fund_balance("main", main_balance - amount)

                # Update settlement status to settled
                conn.execute(
                    """
                    UPDATE pending_settlements
                    SET status = 'settled', settled_at = ?
                    WHERE id = ?
                    """,
                    (now.isoformat(), settlement["id"]),
                )

                log.info(
                    "Settlement locked: %s $%.2f (main balance now $%.2f)",
                    fund_id, amount, main_balance - amount
                )
                settled.append(settlement)
            else:
                # Not enough balance - partial settlement
                if main_balance > 0.01:
                    update_fund_balance("main", 0)
                    conn.execute(
                        """
                        UPDATE pending_settlements
                        SET amount = ?, status = 'settled', settled_at = ?
                        WHERE id = ?
                        """,
                        (main_balance, now.isoformat(), settlement["id"]),
                    )
                    log.warning(
                        "Partial settlement: %s $%.2f of $%.2f (insufficient balance)",
                        fund_id, main_balance, amount
                    )
                    settlement["amount"] = main_balance
                    settled.append(settlement)

    return settled


def distribute_settled_allocations() -> list[dict]:
    """
    Distribute settled allocations to their destination funds.

    Called when distribute_at time is reached.
    Returns list of distributions processed.
    """
    from datetime import datetime

    now = datetime.now()
    distributed = []

    with get_conn() as conn:
        # Find settled allocations ready for distribution
        rows = conn.execute(
            """
            SELECT * FROM pending_settlements
            WHERE status = 'settled' AND distribute_at <= ?
            """,
            (now.isoformat(),),
        ).fetchall()

        for row in rows:
            settlement = dict(row)
            fund_id = settlement["fund_id"]
            amount = settlement["amount"]

            # Credit the allocation fund
            fund_balance = get_fund_balance(fund_id)
            update_fund_balance(fund_id, fund_balance + amount)

            # Update settlement status to distributed
            conn.execute(
                """
                UPDATE pending_settlements
                SET status = 'distributed', distributed_at = ?
                WHERE id = ?
                """,
                (now.isoformat(), settlement["id"]),
            )

            # Update harvest history status
            conn.execute(
                """
                UPDATE harvest_history
                SET status = 'completed'
                WHERE fund_id = ? AND full_moon_date = ? AND status = 'pending'
                """,
                (fund_id, settlement["full_moon_date"]),
            )

            # Record as allocation for tracking
            record_allocation(
                fund_name=fund_id.capitalize(),
                amount=amount,
                source_market="full_moon_harvest",
                source_pnl=amount,
            )

            log.info(
                "Distribution complete: %s received $%.2f",
                fund_id, amount
            )
            distributed.append(settlement)

    return distributed


def get_settlement_dashboard() -> dict:
    """Get settlement status for dashboard display."""
    pending = get_pending_settlements("pending")
    settled = get_pending_settlements("settled")

    pending_total = sum(s["amount"] for s in pending)
    settled_total = sum(s["amount"] for s in settled)

    # Next settlement time
    next_settle = None
    if pending:
        next_settle = pending[0]["settle_at"]

    # Next distribution time
    next_distribute = None
    if settled:
        next_distribute = settled[0]["distribute_at"]

    return {
        "pending_count": len(pending),
        "pending_total": round(pending_total, 2),
        "pending_tradable": True,  # Pending funds are available for trading
        "settled_count": len(settled),
        "settled_total": round(settled_total, 2),
        "settled_locked": True,  # Settled funds are locked
        "next_settlement": next_settle,
        "next_distribution": next_distribute,
        "pending_settlements": pending,
        "settled_settlements": settled,
    }


def get_harvest_history(limit: int = 20) -> list[dict]:
    """Get recent harvest history."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM harvest_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def is_harvest_done_for_moon(full_moon_date: str, fund_id: str) -> bool:
    """Check if a fund has already been harvested for a given full moon."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM harvest_history WHERE full_moon_date = ? AND fund_id = ?",
            (full_moon_date, fund_id),
        ).fetchone()
        return row is not None


# ── MONTHLY DISTRIBUTIONS ────────────────────────────────────────────────────


def is_month_distributed(month: str, fund_id: str) -> bool:
    """Check if a fund has already been distributed for a given month."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM monthly_distributions WHERE month = ? AND fund_id = ?",
            (month, fund_id),
        ).fetchone()
        return row is not None


def record_monthly_distribution(
    month: str, fund_id: str, amount: float,
    wallet_address: str = "", status: str = "paper",
) -> None:
    """Record a monthly distribution (sweep to wallet)."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO monthly_distributions (month, fund_id, amount, wallet_address, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (month, fund_id, amount, wallet_address, status),
        )


def get_distribution_history(limit: int = 20) -> list[dict]:
    """Get recent monthly distributions."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM monthly_distributions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_last_distribution(fund_id: str) -> dict | None:
    """Get the most recent distribution for a fund."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM monthly_distributions WHERE fund_id = ? ORDER BY created_at DESC LIMIT 1",
            (fund_id,),
        ).fetchone()
        return dict(row) if row else None


# ── TRACKED WALLETS ──────────────────────────────────────────────────────────


def seed_wallets(wallets: list[str], labels: dict[str, str]) -> None:
    """Seed tracked_wallets table from config.py on first run (INSERT OR IGNORE)."""
    with get_conn() as conn:
        for addr in wallets:
            label = labels.get(addr, "")
            conn.execute(
                "INSERT OR IGNORE INTO tracked_wallets (address, label) VALUES (?, ?)",
                (addr.lower(), label),
            )


def get_tracked_wallets() -> list[dict]:
    """Get all tracked wallets (enabled ones only)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tracked_wallets WHERE enabled = 1 ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_all_tracked_wallets() -> list[dict]:
    """Get all tracked wallets including disabled."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM tracked_wallets ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def add_tracked_wallet(address: str, label: str = "") -> bool:
    """Add a new wallet to track. Returns True if added, False if already exists."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO tracked_wallets (address, label) VALUES (?, ?)",
                (address.lower(), label),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_tracked_wallet(address: str) -> bool:
    """Remove a tracked wallet. Returns True if found and removed."""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM tracked_wallets WHERE address = ?",
            (address.lower(),),
        )
        return cursor.rowcount > 0


def update_wallet_label(address: str, label: str) -> bool:
    """Update a wallet's label. Returns True if found and updated."""
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE tracked_wallets SET label = ? WHERE address = ?",
            (label, address.lower()),
        )
        return cursor.rowcount > 0


def get_wallet_label(address: str) -> str:
    """Get label for a wallet address. Returns truncated address if no label."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT label FROM tracked_wallets WHERE address = ?",
            (address.lower(),),
        ).fetchone()
        if row and row["label"]:
            return row["label"]
        return address[:10] + "..."


def update_wallet_allocation(address: str, allocation_pct: Optional[float]) -> bool:
    """Update a wallet's allocation percentage. Returns True if found and updated."""
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE tracked_wallets SET allocation_pct = ? WHERE address = ?",
            (allocation_pct, address.lower()),
        )
        return cursor.rowcount > 0


def get_wallet_allocations() -> list[dict]:
    """Get all wallets with their allocation percentages."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT address, label, allocation_pct FROM tracked_wallets WHERE enabled = 1"
        ).fetchall()
        return [dict(row) for row in rows]


def normalize_wallet_allocations() -> None:
    """Normalize wallet allocations to sum to 100%. Missing allocations get equal share."""
    wallets = get_wallet_allocations()
    if not wallets:
        return

    # Calculate total of explicit allocations
    explicit_total = sum(w["allocation_pct"] or 0 for w in wallets)
    wallets_without_alloc = [w for w in wallets if w["allocation_pct"] is None]

    if wallets_without_alloc:
        # Distribute remaining among wallets without allocation
        remaining = max(0, 100 - explicit_total)
        share = remaining / len(wallets_without_alloc) if wallets_without_alloc else 0
        for w in wallets_without_alloc:
            update_wallet_allocation(w["address"], share)
    elif explicit_total > 0 and explicit_total != 100:
        # Scale all allocations to sum to 100%
        scale = 100 / explicit_total
        for w in wallets:
            if w["allocation_pct"]:
                update_wallet_allocation(w["address"], w["allocation_pct"] * scale)


# ── TRADING ACCOUNTS ────────────────────────────────────────────────────────


def create_account(
    name: str,
    slug: str,
    description: str = "",
    account_type: str = "trading",
    starting_balance: float = 0,
) -> int:
    """Create a new trading account. Returns the account ID."""
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trading_accounts (name, slug, description, account_type)
            VALUES (?, ?, ?, ?)
            """,
            (name, slug.lower(), description, account_type),
        )
        account_id = cursor.lastrowid

        # Initialize balance
        conn.execute(
            """
            INSERT INTO account_balances (account_id, balance_usdc, total_pnl, reserve_balance)
            VALUES (?, ?, 0, 0)
            """,
            (account_id, starting_balance),
        )

        # Initialize trading profile with defaults
        conn.execute(
            """
            INSERT INTO account_trading_profiles
            (account_id, auto_trade_enabled, copy_strategy, max_trade_pct, max_wallet_pct, max_market_pct, risk_level)
            VALUES (?, 0, 'tiered_fixed', 0.15, 0.50, 0.15, 'moderate')
            """,
            (account_id,),
        )

        # Initialize reserve config
        conn.execute(
            """
            INSERT INTO reserve_config (account_id, reserve_pct, cycling_enabled, cycle_schedule, cycle_pct)
            VALUES (?, 0, 0, 'disabled', 10)
            """,
            (account_id,),
        )

        log.info("Created account: id=%d name=%s slug=%s", account_id, name, slug)
        return account_id


def get_account(account_id: int) -> Optional[dict]:
    """Get an account by ID."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM trading_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()
        return dict(row) if row else None


def get_account_by_slug(slug: str) -> Optional[dict]:
    """Get an account by slug."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM trading_accounts WHERE slug = ?",
            (slug.lower(),),
        ).fetchone()
        return dict(row) if row else None


def get_all_accounts(include_inactive: bool = False) -> list[dict]:
    """Get all trading accounts."""
    with get_conn() as conn:
        if include_inactive:
            rows = conn.execute(
                "SELECT * FROM trading_accounts ORDER BY id ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trading_accounts WHERE status = 'active' ORDER BY id ASC"
            ).fetchall()
        return [dict(row) for row in rows]


def update_account(
    account_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    status: Optional[str] = None,
) -> bool:
    """Update an account. Returns True if found and updated."""
    updates = []
    params = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if status is not None:
        updates.append("status = ?")
        params.append(status)

    if not updates:
        return False

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(account_id)

    with get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE trading_accounts SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        return cursor.rowcount > 0


def delete_account(account_id: int) -> bool:
    """Delete an account and all related data. Returns True if found and deleted."""
    if account_id == 1:
        log.warning("Cannot delete the default main account")
        return False

    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM trading_accounts WHERE id = ?",
            (account_id,),
        )
        if cursor.rowcount > 0:
            log.info("Deleted account: id=%d", account_id)
            return True
        return False


# ── ACCOUNT BALANCES ────────────────────────────────────────────────────────


def get_account_balance(account_id: int) -> dict:
    """Get balance info for an account."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM account_balances WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row:
            return dict(row)
        return {
            "account_id": account_id,
            "balance_usdc": 0,
            "total_pnl": 0,
            "reserve_balance": 0,
            "total_trades": 0,
        }


def update_account_balance(
    account_id: int,
    balance_usdc: Optional[float] = None,
    pnl_delta: float = 0,
    reserve_balance: Optional[float] = None,
    trade_count_delta: int = 0,
) -> None:
    """Update account balance."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

    if balance_usdc is not None:
        updates.append("balance_usdc = ?")
        params.append(balance_usdc)

    if pnl_delta != 0:
        updates.append("total_pnl = total_pnl + ?")
        params.append(pnl_delta)

    if reserve_balance is not None:
        updates.append("reserve_balance = ?")
        params.append(reserve_balance)

    if trade_count_delta != 0:
        updates.append("total_trades = total_trades + ?")
        params.append(trade_count_delta)

    params.append(account_id)

    with get_conn() as conn:
        conn.execute(
            f"UPDATE account_balances SET {', '.join(updates)} WHERE account_id = ?",
            params,
        )


def get_account_tradable_balance(account_id: int) -> float:
    """Get tradable balance (total balance minus reserve)."""
    bal = get_account_balance(account_id)
    return max(0, bal["balance_usdc"] - bal["reserve_balance"])


# ── ACCOUNT TRADING PROFILES ────────────────────────────────────────────────


def get_trading_profile(account_id: int) -> dict:
    """Get trading profile for an account."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM account_trading_profiles WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row:
            return dict(row)
        # Return defaults
        from config import DEFAULT_ACCOUNT_PROFILE
        return {
            "account_id": account_id,
            **DEFAULT_ACCOUNT_PROFILE,
        }


def update_trading_profile(
    account_id: int,
    auto_trade_enabled: Optional[bool] = None,
    copy_strategy: Optional[str] = None,
    max_trade_pct: Optional[float] = None,
    max_wallet_pct: Optional[float] = None,
    max_market_pct: Optional[float] = None,
    risk_level: Optional[str] = None,
) -> bool:
    """Update trading profile. Returns True if updated."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

    if auto_trade_enabled is not None:
        updates.append("auto_trade_enabled = ?")
        params.append(1 if auto_trade_enabled else 0)
    if copy_strategy is not None:
        updates.append("copy_strategy = ?")
        params.append(copy_strategy)
    if max_trade_pct is not None:
        updates.append("max_trade_pct = ?")
        params.append(max_trade_pct)
    if max_wallet_pct is not None:
        updates.append("max_wallet_pct = ?")
        params.append(max_wallet_pct)
    if max_market_pct is not None:
        updates.append("max_market_pct = ?")
        params.append(max_market_pct)
    if risk_level is not None:
        updates.append("risk_level = ?")
        params.append(risk_level)

    params.append(account_id)

    with get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE account_trading_profiles SET {', '.join(updates)} WHERE account_id = ?",
            params,
        )
        return cursor.rowcount > 0


def apply_risk_preset(account_id: int, preset: str) -> bool:
    """Apply a risk level preset to an account."""
    from config import RISK_PRESETS
    if preset not in RISK_PRESETS:
        return False

    settings = RISK_PRESETS[preset]
    return update_trading_profile(
        account_id,
        max_trade_pct=settings["max_trade_pct"],
        max_wallet_pct=settings["max_wallet_pct"],
        max_market_pct=settings["max_market_pct"],
        risk_level=preset,
    )


# ── RESERVE CONFIG ──────────────────────────────────────────────────────────


def get_reserve_config(account_id: int) -> dict:
    """Get reserve configuration for an account."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reserve_config WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row:
            return dict(row)
        return {
            "account_id": account_id,
            "reserve_pct": 0,
            "cycling_enabled": False,
            "cycle_schedule": "disabled",
            "cycle_pct": 10,
            "last_cycle_at": None,
        }


def update_reserve_config(
    account_id: int,
    reserve_pct: Optional[float] = None,
    cycling_enabled: Optional[bool] = None,
    cycle_schedule: Optional[str] = None,
    cycle_pct: Optional[float] = None,
) -> bool:
    """Update reserve configuration."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

    if reserve_pct is not None:
        # Clamp to 0-100%
        reserve_pct = max(0, min(100, reserve_pct))
        updates.append("reserve_pct = ?")
        params.append(reserve_pct)
    if cycling_enabled is not None:
        updates.append("cycling_enabled = ?")
        params.append(1 if cycling_enabled else 0)
    if cycle_schedule is not None:
        updates.append("cycle_schedule = ?")
        params.append(cycle_schedule)
    if cycle_pct is not None:
        updates.append("cycle_pct = ?")
        params.append(cycle_pct)

    params.append(account_id)

    with get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE reserve_config SET {', '.join(updates)} WHERE account_id = ?",
            params,
        )
        return cursor.rowcount > 0


def mark_reserve_cycled(account_id: int) -> None:
    """Update the last_cycle_at timestamp for an account."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE reserve_config SET last_cycle_at = CURRENT_TIMESTAMP WHERE account_id = ?",
            (account_id,),
        )


def apply_reserve_to_balance(account_id: int) -> float:
    """
    Apply reserve percentage to account balance, moving funds to reserve.
    Returns the new reserve amount.
    """
    balance = get_account_balance(account_id)
    config = get_reserve_config(account_id)

    if config["reserve_pct"] <= 0:
        return balance["reserve_balance"]

    total = balance["balance_usdc"] + balance["reserve_balance"]
    target_reserve = total * (config["reserve_pct"] / 100)
    current_reserve = balance["reserve_balance"]

    if target_reserve > current_reserve:
        # Move funds to reserve
        to_reserve = min(target_reserve - current_reserve, balance["balance_usdc"])
        new_balance = balance["balance_usdc"] - to_reserve
        new_reserve = current_reserve + to_reserve
        update_account_balance(account_id, balance_usdc=new_balance, reserve_balance=new_reserve)
        return new_reserve

    return current_reserve


def cycle_reserve(account_id: int) -> float:
    """
    Cycle a portion of reserve back to trading balance.
    Returns the amount cycled.
    """
    balance = get_account_balance(account_id)
    config = get_reserve_config(account_id)

    if not config["cycling_enabled"] or config["cycle_pct"] <= 0:
        return 0

    reserve = balance["reserve_balance"]
    if reserve <= 0:
        return 0

    cycle_amount = reserve * (config["cycle_pct"] / 100)
    new_reserve = reserve - cycle_amount
    new_balance = balance["balance_usdc"] + cycle_amount

    update_account_balance(account_id, balance_usdc=new_balance, reserve_balance=new_reserve)
    mark_reserve_cycled(account_id)

    log.info(
        "Reserve cycled: account=%d amount=$%.2f reserve=$%.2f → $%.2f",
        account_id, cycle_amount, reserve, new_reserve
    )
    return cycle_amount


# ── ACCOUNT CREDENTIALS ─────────────────────────────────────────────────────


def store_account_credential(
    account_id: int,
    platform: str,
    credential_type: str,
    encrypted_value: str,
    nonce: str,
) -> None:
    """Store an encrypted credential for an account."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO account_credentials (account_id, platform, credential_type, encrypted_value, nonce)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id, platform, credential_type) DO UPDATE SET
                encrypted_value = excluded.encrypted_value,
                nonce = excluded.nonce,
                created_at = CURRENT_TIMESTAMP
            """,
            (account_id, platform, credential_type, encrypted_value, nonce),
        )


def get_account_credential(
    account_id: int,
    platform: str,
    credential_type: str,
) -> Optional[dict]:
    """Get an encrypted credential."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT encrypted_value, nonce FROM account_credentials
            WHERE account_id = ? AND platform = ? AND credential_type = ?
            """,
            (account_id, platform, credential_type),
        ).fetchone()
        return dict(row) if row else None


def delete_account_credential(
    account_id: int,
    platform: str,
    credential_type: str,
) -> bool:
    """Delete a credential. Returns True if found and deleted."""
    with get_conn() as conn:
        cursor = conn.execute(
            """
            DELETE FROM account_credentials
            WHERE account_id = ? AND platform = ? AND credential_type = ?
            """,
            (account_id, platform, credential_type),
        )
        return cursor.rowcount > 0


def list_account_credentials(account_id: int) -> list[dict]:
    """List all credentials for an account (metadata only, no values)."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT platform, credential_type, created_at
            FROM account_credentials WHERE account_id = ?
            ORDER BY platform, credential_type
            """,
            (account_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def delete_all_account_credentials(account_id: int) -> int:
    """Delete all credentials for an account. Returns count deleted."""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM account_credentials WHERE account_id = ?",
            (account_id,),
        )
        return cursor.rowcount


# ── ACCOUNT POSITIONS ───────────────────────────────────────────────────────


def get_account_positions(account_id: int) -> list[dict]:
    """Get all positions for an account."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE account_id = ? ORDER BY updated_at DESC",
            (account_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_account_trade_history(account_id: int, limit: int = 50) -> list[dict]:
    """Get trade history for an account."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trade_history WHERE account_id = ? ORDER BY timestamp DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


# ── SETTINGS REQUESTS ───────────────────────────────────────────────────────


def create_settings_request(
    request_type: str,
    category: str,
    requested_value: str,
    submitted_by: str,
    account_id: Optional[int] = None,
    current_value: Optional[str] = None,
    reason: str = "",
) -> int:
    """
    Create a new settings change request.

    Args:
        request_type: Type of setting (e.g., "reserve_pct", "risk_level", "wallet_allocation")
        category: Category for grouping (e.g., "reserve", "trading", "wallet")
        requested_value: JSON string of the requested new value
        submitted_by: Username of requester
        account_id: Optional account ID (for account-specific settings)
        current_value: JSON string of current value (for reference)
        reason: Optional explanation for the request

    Returns:
        The request ID
    """
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO settings_requests
            (account_id, request_type, category, current_value, requested_value, reason, submitted_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, request_type, category, current_value, requested_value, reason, submitted_by),
        )
        log.info(
            "Settings request created: id=%d type=%s by=%s",
            cursor.lastrowid, request_type, submitted_by
        )
        return cursor.lastrowid


def get_pending_settings_requests() -> list[dict]:
    """Get all pending settings requests."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT sr.*, ta.name as account_name
            FROM settings_requests sr
            LEFT JOIN trading_accounts ta ON sr.account_id = ta.id
            WHERE sr.status = 'pending'
            ORDER BY sr.created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def get_settings_request(request_id: int) -> Optional[dict]:
    """Get a single settings request by ID."""
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT sr.*, ta.name as account_name
            FROM settings_requests sr
            LEFT JOIN trading_accounts ta ON sr.account_id = ta.id
            WHERE sr.id = ?
            """,
            (request_id,),
        ).fetchone()
        return dict(row) if row else None


def get_user_settings_requests(username: str, limit: int = 20) -> list[dict]:
    """Get settings requests submitted by a specific user."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT sr.*, ta.name as account_name
            FROM settings_requests sr
            LEFT JOIN trading_accounts ta ON sr.account_id = ta.id
            WHERE sr.submitted_by = ?
            ORDER BY sr.created_at DESC
            LIMIT ?
            """,
            (username, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def approve_settings_request(request_id: int, reviewed_by: str, note: str = "") -> Optional[dict]:
    """
    Approve a settings request.

    Returns the request dict if successful, None if not found.
    """
    with get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE settings_requests
            SET status = 'approved', reviewed_by = ?, review_note = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (reviewed_by, note, request_id),
        )
        if cursor.rowcount == 0:
            return None

        log.info("Settings request approved: id=%d by=%s", request_id, reviewed_by)
        return get_settings_request(request_id)


def deny_settings_request(request_id: int, reviewed_by: str, note: str = "") -> Optional[dict]:
    """
    Deny a settings request.

    Returns the request dict if successful, None if not found.
    """
    with get_conn() as conn:
        cursor = conn.execute(
            """
            UPDATE settings_requests
            SET status = 'denied', reviewed_by = ?, review_note = ?, reviewed_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status = 'pending'
            """,
            (reviewed_by, note, request_id),
        )
        if cursor.rowcount == 0:
            return None

        log.info("Settings request denied: id=%d by=%s", request_id, reviewed_by)
        return get_settings_request(request_id)


def get_all_settings_requests(limit: int = 50, include_resolved: bool = True) -> list[dict]:
    """Get all settings requests with optional filtering."""
    with get_conn() as conn:
        if include_resolved:
            rows = conn.execute(
                """
                SELECT sr.*, ta.name as account_name
                FROM settings_requests sr
                LEFT JOIN trading_accounts ta ON sr.account_id = ta.id
                ORDER BY sr.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT sr.*, ta.name as account_name
                FROM settings_requests sr
                LEFT JOIN trading_accounts ta ON sr.account_id = ta.id
                WHERE sr.status = 'pending'
                ORDER BY sr.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def get_pending_request_count() -> int:
    """Get count of pending settings requests."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as count FROM settings_requests WHERE status = 'pending'"
        ).fetchone()
        return row["count"] if row else 0


# ── ACCOUNT ACTIVATION ──────────────────────────────────────────────────────


def get_activation_status(account_id: int) -> dict:
    """
    Get activation status for an account.
    Returns status info including what's needed to activate.
    """
    with get_conn() as conn:
        # Get account info
        account = conn.execute(
            "SELECT * FROM trading_accounts WHERE id = ?",
            (account_id,),
        ).fetchone()

        if not account:
            return {"error": "Account not found"}

        account = dict(account)
        status = account.get("activation_status", "pending")

        # Check credentials
        creds = conn.execute(
            "SELECT platform, credential_type FROM account_credentials WHERE account_id = ?",
            (account_id,),
        ).fetchall()
        cred_list = [{"platform": r["platform"], "type": r["credential_type"]} for r in creds]

        # Check which platforms have full credentials
        global_creds = [c for c in cred_list if c["platform"] == "polymarket_global"]
        us_creds = [c for c in cred_list if c["platform"] == "polymarket_us"]

        global_ready = len([c for c in global_creds if c["type"] in ("api_key", "api_secret", "api_passphrase", "private_key")]) >= 4
        us_ready = len([c for c in us_creds if c["type"] in ("api_key", "api_secret", "api_passphrase")]) >= 3

        # Get profile
        profile = get_trading_profile(account_id)
        balance = get_account_balance(account_id)

        return {
            "account_id": account_id,
            "account_name": account["name"],
            "activation_status": status,
            "is_activated": status == "activated",
            "credentials": {
                "global_ready": global_ready,
                "us_ready": us_ready,
                "stored": cred_list,
            },
            "profile": {
                "auto_trade_enabled": bool(profile.get("auto_trade_enabled")),
                "risk_level": profile.get("risk_level", "moderate"),
            },
            "balance": balance.get("balance_usdc", 0),
            "can_activate": global_ready or us_ready,
        }


def activate_account(account_id: int, target_mode: str = "global") -> dict:
    """
    Activate an account for live trading.

    Args:
        account_id: The account to activate
        target_mode: "global" (default) or "us"

    Returns:
        Result dict with success status and details
    """
    status = get_activation_status(account_id)

    if status.get("error"):
        return {"success": False, "error": status["error"]}

    if not status["can_activate"]:
        return {
            "success": False,
            "error": "Missing credentials. Please add API keys before activating.",
            "details": status["credentials"],
        }

    # Default to global, fall back to us if global not ready
    if target_mode == "global" and not status["credentials"]["global_ready"]:
        if status["credentials"]["us_ready"]:
            target_mode = "us"
        else:
            return {"success": False, "error": "Global credentials incomplete"}

    if target_mode == "us" and not status["credentials"]["us_ready"]:
        return {"success": False, "error": "US credentials incomplete"}

    with get_conn() as conn:
        # Update activation status
        conn.execute(
            "UPDATE trading_accounts SET activation_status = 'activated', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (account_id,),
        )

        # Enable auto-trading
        conn.execute(
            "UPDATE account_trading_profiles SET auto_trade_enabled = 1, updated_at = CURRENT_TIMESTAMP WHERE account_id = ?",
            (account_id,),
        )

    # Switch system mode
    import mode as mode_module
    mode_module.set_mode(target_mode)

    log.info("Account %d activated for %s trading", account_id, target_mode.upper())

    return {
        "success": True,
        "account_id": account_id,
        "mode": target_mode,
        "auto_trade_enabled": True,
        "message": f"Account activated for {target_mode.upper()} trading!",
    }


def deactivate_account(account_id: int) -> dict:
    """Deactivate an account (switch back to paper mode)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE trading_accounts SET activation_status = 'pending', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (account_id,),
        )
        conn.execute(
            "UPDATE account_trading_profiles SET auto_trade_enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE account_id = ?",
            (account_id,),
        )

    import mode as mode_module
    mode_module.set_mode("paper")

    log.info("Account %d deactivated, switched to PAPER mode", account_id)

    return {
        "success": True,
        "account_id": account_id,
        "mode": "paper",
        "message": "Account deactivated. Switched to paper trading.",
    }


def needs_activation_prompt(account_id: int) -> bool:
    """Check if we should show the activation prompt for this account."""
    status = get_activation_status(account_id)
    return (
        status.get("activation_status") == "pending"
        and status.get("can_activate", False)
    )


# ── MARKET CACHE ──────────────────────────────────────────────────────────────


def upsert_market_cache(
    market_id: str,
    volume_24h: float = 0,
    volume_total: float = 0,
    liquidity: float = 0,
    best_bid: float = 0,
    best_ask: float = 0,
    spread: float = 0,
    last_trade_price: float = 0,
    active: bool = True,
) -> None:
    """Insert or update cached market data."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO market_cache (market_id, volume_24h, volume_total, liquidity,
                                      best_bid, best_ask, spread, last_trade_price, active, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(market_id) DO UPDATE SET
                volume_24h = excluded.volume_24h,
                volume_total = excluded.volume_total,
                liquidity = excluded.liquidity,
                best_bid = excluded.best_bid,
                best_ask = excluded.best_ask,
                spread = excluded.spread,
                last_trade_price = excluded.last_trade_price,
                active = excluded.active,
                fetched_at = CURRENT_TIMESTAMP
            """,
            (market_id, volume_24h, volume_total, liquidity, best_bid, best_ask,
             spread, last_trade_price, int(active)),
        )


def get_market_cache(market_id: str) -> Optional[dict]:
    """Get cached market data. Returns None if not cached."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM market_cache WHERE market_id = ?",
            (market_id,),
        ).fetchone()
        return dict(row) if row else None


def get_market_cache_age_seconds(market_id: str) -> Optional[float]:
    """Get how many seconds since the market cache was last updated."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT (julianday('now') - julianday(fetched_at)) * 86400 as age_seconds FROM market_cache WHERE market_id = ?",
            (market_id,),
        ).fetchone()
        return row["age_seconds"] if row else None


# ── FUTURES POSITIONS ────────────────────────────────────────────────────────


def get_futures_position(symbol: str, trader_wallet: str, account_id: int = 1) -> Optional[dict]:
    """Get current futures position for a symbol/wallet."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM futures_positions WHERE symbol = ? AND trader_wallet = ? AND account_id = ?",
            (symbol, trader_wallet.lower(), account_id),
        ).fetchone()
        return dict(row) if row else None


def upsert_futures_position(
    symbol: str,
    trader_wallet: str,
    side: str,
    entry_price: float,
    size: float,
    leverage: float,
    margin_used: float,
    unrealized_pnl: float = 0,
    liquidation_price: Optional[float] = None,
    account_id: int = 1,
) -> None:
    """Insert or update a futures position."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO futures_positions
            (account_id, symbol, trader_wallet, side, entry_price, size, leverage, margin_used, unrealized_pnl, liquidation_price, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_id, symbol, trader_wallet) DO UPDATE SET
                side = excluded.side,
                entry_price = excluded.entry_price,
                size = excluded.size,
                leverage = excluded.leverage,
                margin_used = excluded.margin_used,
                unrealized_pnl = excluded.unrealized_pnl,
                liquidation_price = excluded.liquidation_price,
                updated_at = CURRENT_TIMESTAMP
            """,
            (account_id, symbol, trader_wallet.lower(), side, entry_price, size, leverage, margin_used, unrealized_pnl, liquidation_price),
        )


def delete_futures_position(symbol: str, trader_wallet: str, account_id: int = 1) -> None:
    """Delete a futures position (when fully closed)."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM futures_positions WHERE symbol = ? AND trader_wallet = ? AND account_id = ?",
            (symbol, trader_wallet.lower(), account_id),
        )


def get_all_futures_positions(account_id: int = 1) -> list[dict]:
    """Get all active futures positions."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM futures_positions WHERE account_id = ? ORDER BY updated_at DESC",
            (account_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_futures_wallet_margin(trader_wallet: str, account_id: int = 1) -> float:
    """Get total margin used across all positions for a specific tracked wallet."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(margin_used), 0) as total FROM futures_positions WHERE trader_wallet = ? AND account_id = ?",
            (trader_wallet.lower(), account_id),
        ).fetchone()
        return row["total"]


# ── FUTURES TRADE HISTORY ────────────────────────────────────────────────────


def log_futures_trade(
    symbol: str,
    trader_wallet: str,
    side: str,
    size: float,
    price: float,
    leverage: float,
    realized_pnl: float = 0,
    mode: str = "paper",
    success: bool = True,
    account_id: int = 1,
) -> None:
    """Log a futures trade to history."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO futures_trades
            (account_id, symbol, trader_wallet, side, size, price, leverage, realized_pnl, mode, success)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, symbol, trader_wallet.lower(), side, size, price, leverage, realized_pnl, mode, 1 if success else 0),
        )


def get_futures_trade_history(account_id: int = 1, limit: int = 50) -> list[dict]:
    """Get recent futures trade history."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM futures_trades WHERE account_id = ? ORDER BY timestamp DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


# ── FUTURES ACCOUNTS ─────────────────────────────────────────────────────────


def init_futures_account(starting_balance: float, account_id: int = 1) -> None:
    """Initialize futures paper trading account."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO futures_accounts (account_id, balance_usdc, margin_used, total_pnl, total_trades)
            VALUES (?, ?, 0, 0, 0)
            """,
            (account_id, starting_balance),
        )


def get_futures_account(account_id: int = 1) -> dict:
    """Get futures account stats."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM futures_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        if row:
            return dict(row)
        return {
            "account_id": account_id,
            "balance_usdc": 0,
            "margin_used": 0,
            "total_pnl": 0,
            "total_trades": 0,
        }


def update_futures_account(
    account_id: int = 1,
    balance_usdc: Optional[float] = None,
    margin_used: Optional[float] = None,
    pnl_delta: float = 0,
    trade_count_delta: int = 0,
) -> None:
    """Update futures account."""
    updates = ["updated_at = CURRENT_TIMESTAMP"]
    params = []

    if balance_usdc is not None:
        updates.append("balance_usdc = ?")
        params.append(balance_usdc)
    if margin_used is not None:
        updates.append("margin_used = ?")
        params.append(margin_used)
    if pnl_delta != 0:
        updates.append("total_pnl = total_pnl + ?")
        params.append(pnl_delta)
    if trade_count_delta != 0:
        updates.append("total_trades = total_trades + ?")
        params.append(trade_count_delta)

    params.append(account_id)

    with get_conn() as conn:
        conn.execute(
            f"UPDATE futures_accounts SET {', '.join(updates)} WHERE account_id = ?",
            params,
        )


def reset_futures_account(starting_balance: float = 1000, account_id: int = 1) -> None:
    """Reset futures paper account, clearing all positions and trades."""
    with get_conn() as conn:
        conn.execute("DELETE FROM futures_positions WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM futures_trades WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM futures_accounts WHERE account_id = ?", (account_id,))
        conn.execute("DELETE FROM futures_watched_trades")
    init_futures_account(starting_balance, account_id)


# ── FUTURES TRACKED WALLETS ──────────────────────────────────────────────────


def get_futures_tracked_wallets() -> list[dict]:
    """Get all enabled futures tracked wallets."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM futures_tracked_wallets WHERE enabled = 1 ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_all_futures_tracked_wallets() -> list[dict]:
    """Get all futures tracked wallets including disabled."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM futures_tracked_wallets ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def add_futures_tracked_wallet(address: str, label: str = "") -> bool:
    """Add a new wallet to track for futures. Returns True if added."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO futures_tracked_wallets (address, label) VALUES (?, ?)",
                (address.lower(), label),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_futures_tracked_wallet(address: str) -> bool:
    """Remove a futures tracked wallet. Returns True if found and removed."""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM futures_tracked_wallets WHERE address = ?",
            (address.lower(),),
        )
        return cursor.rowcount > 0


def toggle_futures_wallet(address: str, enabled: bool) -> bool:
    """Enable or disable a futures tracked wallet."""
    with get_conn() as conn:
        cursor = conn.execute(
            "UPDATE futures_tracked_wallets SET enabled = ? WHERE address = ?",
            (1 if enabled else 0, address.lower()),
        )
        return cursor.rowcount > 0


# ── FUTURES DEDUPLICATION ────────────────────────────────────────────────────


def is_futures_trade_processed(trader_wallet: str, tx_hash: str) -> bool:
    """Check if we've already processed this futures trade."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM futures_watched_trades WHERE trader_wallet = ? AND tx_hash = ?",
            (trader_wallet.lower(), tx_hash),
        ).fetchone()
        return row is not None


def mark_futures_trade_processed(trader_wallet: str, tx_hash: str) -> None:
    """Mark a futures trade as processed."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO futures_watched_trades (trader_wallet, tx_hash)
            VALUES (?, ?)
            """,
            (trader_wallet.lower(), tx_hash),
        )


# ── FUTURES WALLET PERFORMANCE ───────────────────────────────────────────────


def get_futures_wallet_performance(trader_wallet: str, account_id: int = 1) -> dict:
    """
    Get performance stats for a single futures wallet.
    Returns wins, losses, total P&L, avg win/loss, win rate.
    """
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                trader_wallet,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) as breakeven,
                COALESCE(SUM(realized_pnl), 0) as total_pnl,
                AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) as avg_win,
                AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END) as avg_loss,
                MAX(realized_pnl) as best_trade,
                MIN(realized_pnl) as worst_trade
            FROM futures_trades
            WHERE trader_wallet = ? AND account_id = ?
              AND side IN ('CLOSE_LONG', 'CLOSE_SHORT')
            GROUP BY trader_wallet
            """,
            (trader_wallet.lower(), account_id),
        ).fetchone()

        if not row:
            return {
                "trader_wallet": trader_wallet,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "profit_factor": 0.0,
            }

        data = dict(row)
        wins = data["wins"] or 0
        losses = data["losses"] or 0
        resolved = wins + losses

        data["win_rate"] = (wins / resolved * 100) if resolved > 0 else 0.0

        # Profit factor = gross wins / |gross losses|
        avg_win = data["avg_win"] or 0
        avg_loss = abs(data["avg_loss"] or 0)
        if avg_loss > 0 and wins > 0:
            gross_wins = avg_win * wins
            gross_losses = avg_loss * losses
            data["profit_factor"] = gross_wins / gross_losses if gross_losses > 0 else 0
        else:
            data["profit_factor"] = 0.0

        return data


def get_all_futures_wallet_performance(account_id: int = 1) -> list[dict]:
    """
    Get performance stats for ALL futures wallets.
    Returns list sorted by total P&L (best first).
    """
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                trader_wallet,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) as breakeven,
                COALESCE(SUM(realized_pnl), 0) as total_pnl,
                AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) as avg_win,
                AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END) as avg_loss,
                MAX(realized_pnl) as best_trade,
                MIN(realized_pnl) as worst_trade
            FROM futures_trades
            WHERE account_id = ?
              AND side IN ('CLOSE_LONG', 'CLOSE_SHORT')
            GROUP BY trader_wallet
            ORDER BY total_pnl DESC
            """,
            (account_id,),
        ).fetchall()

        result = []
        for row in rows:
            data = dict(row)
            wins = data["wins"] or 0
            losses = data["losses"] or 0
            resolved = wins + losses

            data["win_rate"] = (wins / resolved * 100) if resolved > 0 else 0.0

            # Profit factor
            avg_win = data["avg_win"] or 0
            avg_loss = abs(data["avg_loss"] or 0)
            if avg_loss > 0 and wins > 0:
                gross_wins = avg_win * wins
                gross_losses = avg_loss * losses
                data["profit_factor"] = gross_wins / gross_losses if gross_losses > 0 else 0
            else:
                data["profit_factor"] = 0.0

            # Get wallet label if available
            label_row = conn.execute(
                "SELECT label FROM futures_tracked_wallets WHERE address = ?",
                (data["trader_wallet"],),
            ).fetchone()
            data["label"] = label_row["label"] if label_row else ""

            result.append(data)

        return result


def get_futures_performance_summary(account_id: int = 1) -> dict:
    """
    Get aggregate performance summary across all futures trading.
    """
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                COALESCE(SUM(realized_pnl), 0) as total_pnl,
                AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl END) as avg_win,
                AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl END) as avg_loss,
                MAX(realized_pnl) as best_trade,
                MIN(realized_pnl) as worst_trade
            FROM futures_trades
            WHERE account_id = ?
              AND side IN ('CLOSE_LONG', 'CLOSE_SHORT')
            """,
            (account_id,),
        ).fetchone()

        if not row or not row["total_trades"]:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
            }

        data = dict(row)
        wins = data["wins"] or 0
        losses = data["losses"] or 0
        resolved = wins + losses
        data["win_rate"] = (wins / resolved * 100) if resolved > 0 else 0.0

        return data


# ── STRATEGIES ───────────────────────────────────────────────────────────────


def create_strategy(name: str, description: str = "", is_active: bool = False) -> int:
    """Create a new strategy. Returns the strategy ID."""
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO strategies (name, description, is_active)
            VALUES (?, ?, ?)
            """,
            (name, description, 1 if is_active else 0),
        )
        return cursor.lastrowid


def get_strategy(strategy_id: int) -> Optional[dict]:
    """Get a strategy by ID."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM strategies WHERE id = ?",
            (strategy_id,),
        ).fetchone()
        return dict(row) if row else None


def get_all_strategies() -> list[dict]:
    """Get all strategies."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM strategies ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def update_strategy(
    strategy_id: int,
    name: Optional[str] = None,
    description: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> bool:
    """Update a strategy. Returns True if found and updated."""
    updates = []
    params = []
    if name is not None:
        updates.append("name = ?")
        params.append(name)
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if is_active else 0)

    if not updates:
        return False

    params.append(strategy_id)
    with get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE strategies SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        return cursor.rowcount > 0


def delete_strategy(strategy_id: int) -> bool:
    """Delete a strategy and all its rules. Returns True if found and deleted."""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM strategies WHERE id = ?",
            (strategy_id,),
        )
        return cursor.rowcount > 0


def set_active_strategy(strategy_id: int) -> bool:
    """Set a strategy as active, deactivating all others."""
    with get_conn() as conn:
        conn.execute("UPDATE strategies SET is_active = 0")
        cursor = conn.execute(
            "UPDATE strategies SET is_active = 1 WHERE id = ?",
            (strategy_id,),
        )
        return cursor.rowcount > 0


def get_active_strategy() -> Optional[dict]:
    """Get the currently active strategy."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM strategies WHERE is_active = 1 LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


# ── STRATEGY RULES ───────────────────────────────────────────────────────────


def add_strategy_rule(
    strategy_id: int,
    rule_type: str,
    rule_name: str,
    rule_config: dict | str,
    priority: int = 0,
    enabled: bool = True,
) -> int:
    """Add a rule to a strategy. Returns the rule ID."""
    # Serialize config dict to JSON string
    config_str = json.dumps(rule_config) if isinstance(rule_config, dict) else rule_config
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO strategy_rules (strategy_id, rule_type, rule_name, rule_config, priority, enabled)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (strategy_id, rule_type, rule_name, config_str, priority, 1 if enabled else 0),
        )
        return cursor.lastrowid


def get_strategy_rules(strategy_id: int) -> list[dict]:
    """Get all rules for a strategy, ordered by priority."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM strategy_rules WHERE strategy_id = ? ORDER BY priority DESC, id ASC",
            (strategy_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_strategy_rule(rule_id: int) -> Optional[dict]:
    """Get a single rule by ID."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
        return dict(row) if row else None


def update_strategy_rule(
    rule_id: int,
    rule_config: Optional[dict | str] = None,
    priority: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> bool:
    """Update a strategy rule."""
    updates = []
    params = []
    if rule_config is not None:
        updates.append("rule_config = ?")
        config_str = json.dumps(rule_config) if isinstance(rule_config, dict) else rule_config
        params.append(config_str)
    if priority is not None:
        updates.append("priority = ?")
        params.append(priority)
    if enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if enabled else 0)

    if not updates:
        return False

    params.append(rule_id)
    with get_conn() as conn:
        cursor = conn.execute(
            f"UPDATE strategy_rules SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        return cursor.rowcount > 0


def delete_strategy_rule(rule_id: int) -> bool:
    """Delete a strategy rule."""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM strategy_rules WHERE id = ?",
            (rule_id,),
        )
        return cursor.rowcount > 0


def get_strategy_with_rules(strategy_id: int) -> Optional[dict]:
    """Get a strategy with all its rules."""
    strategy = get_strategy(strategy_id)
    if not strategy:
        return None
    strategy["rules"] = get_strategy_rules(strategy_id)
    return strategy


# ── TRADER CACHE ─────────────────────────────────────────────────────────────


def upsert_trader_cache(
    wallet_address: str,
    username: str = "",
    pnl: float = 0,
    volume: float = 0,
    win_count: int = 0,
    loss_count: int = 0,
) -> None:
    """Insert or update cached trader data."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO trader_cache (wallet_address, username, pnl, volume, win_count, loss_count, last_fetched)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(wallet_address) DO UPDATE SET
                username = excluded.username,
                pnl = excluded.pnl,
                volume = excluded.volume,
                win_count = excluded.win_count,
                loss_count = excluded.loss_count,
                last_fetched = CURRENT_TIMESTAMP
            """,
            (wallet_address.lower(), username, pnl, volume, win_count, loss_count),
        )


def get_trader_cache(wallet_address: str) -> Optional[dict]:
    """Get cached trader data."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM trader_cache WHERE wallet_address = ?",
            (wallet_address.lower(),),
        ).fetchone()
        return dict(row) if row else None


def get_all_cached_traders() -> list[dict]:
    """Get all cached traders, sorted by PnL."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trader_cache ORDER BY pnl DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_trader_cache_age_seconds(wallet_address: str) -> Optional[float]:
    """Get how many seconds since trader cache was last updated."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT (julianday('now') - julianday(last_fetched)) * 86400 as age_seconds FROM trader_cache WHERE wallet_address = ?",
            (wallet_address.lower(),),
        ).fetchone()
        return row["age_seconds"] if row else None


def clear_trader_cache() -> int:
    """Clear all cached trader data. Returns count deleted."""
    with get_conn() as conn:
        cursor = conn.execute("DELETE FROM trader_cache")
        return cursor.rowcount


# ── BACKTEST RESULTS ─────────────────────────────────────────────────────────


def save_backtest_result(
    strategy_id: Optional[int],
    trader_wallet: str,
    starting_balance: float,
    final_balance: float,
    total_pnl: float,
    win_rate: float,
    max_drawdown: float,
    result_data: dict | str,
) -> int:
    """Save a backtest result. Returns the result ID."""
    # Serialize result_data dict to JSON string
    data_str = json.dumps(result_data) if isinstance(result_data, dict) else result_data
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO backtest_results
            (strategy_id, trader_wallet, starting_balance, final_balance, total_pnl, win_rate, max_drawdown, result_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (strategy_id, trader_wallet.lower() if trader_wallet else None,
             starting_balance, final_balance, total_pnl, win_rate, max_drawdown, data_str),
        )
        return cursor.lastrowid


def get_backtest_result(result_id: int) -> Optional[dict]:
    """Get a backtest result by ID."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM backtest_results WHERE id = ?",
            (result_id,),
        ).fetchone()
        return dict(row) if row else None


def get_backtest_results(limit: int = 50, strategy_id: Optional[int] = None) -> list[dict]:
    """Get recent backtest results, optionally filtered by strategy."""
    with get_conn() as conn:
        if strategy_id is not None:
            rows = conn.execute(
                "SELECT * FROM backtest_results WHERE strategy_id = ? ORDER BY created_at DESC LIMIT ?",
                (strategy_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM backtest_results ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def delete_backtest_result(result_id: int) -> bool:
    """Delete a backtest result."""
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM backtest_results WHERE id = ?",
            (result_id,),
        )
        return cursor.rowcount > 0


def get_best_backtest_for_strategy(strategy_id: int) -> Optional[dict]:
    """Get the best backtest result (highest PnL) for a strategy."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM backtest_results WHERE strategy_id = ? ORDER BY total_pnl DESC LIMIT 1",
            (strategy_id,),
        ).fetchone()
        return dict(row) if row else None
