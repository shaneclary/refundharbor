# approval.py — trade approval queue and operator controls
#
# When approval_mode is "manual", trades are queued in pending_trades
# for the operator to approve/reject. When "auto", trades execute immediately.

import logging
from typing import Optional

from db import get_conn

log = logging.getLogger(__name__)

APPROVAL_MODE_KEY = "approval_mode"
DEFAULT_MODE = "manual"


def init_approval_tables() -> None:
    """Create approval-related tables. Call after init_db()."""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id TEXT NOT NULL,
                trader_wallet TEXT NOT NULL,
                token_id TEXT NOT NULL,
                side TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT '',
                usdc_amount REAL NOT NULL DEFAULT 0,
                shares_to_sell REAL NOT NULL DEFAULT 0,
                fund_id TEXT NOT NULL DEFAULT 'main',
                signal_price REAL,
                signal_shares REAL,
                signal_usdc_value REAL,
                signal_title TEXT DEFAULT '',
                signal_timestamp INTEGER DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                decided_at TIMESTAMP,
                executed_at TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_trades_status
            ON pending_trades (status, created_at)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS operator_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                detail TEXT DEFAULT '',
                ip_address TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_activity_log_created
            ON activity_log (created_at DESC)
        """)
        # Initialize default approval mode
        conn.execute(
            "INSERT OR IGNORE INTO operator_settings (key, value) VALUES (?, ?)",
            (APPROVAL_MODE_KEY, DEFAULT_MODE),
        )


def get_approval_mode() -> str:
    """Get current approval mode: 'manual' or 'auto'."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM operator_settings WHERE key = ?",
            (APPROVAL_MODE_KEY,),
        ).fetchone()
        return row["value"] if row else DEFAULT_MODE


def set_approval_mode(mode: str) -> str:
    """Set approval mode. Returns the new mode."""
    if mode not in ("manual", "auto"):
        raise ValueError(f"Invalid approval mode: {mode}")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO operator_settings (key, value, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (APPROVAL_MODE_KEY, mode),
        )
    log.info("Approval mode changed to: %s", mode.upper())
    return mode


def enqueue_trade(intent, signal) -> int:
    """Insert an OrderIntent into the pending queue. Returns the pending trade ID."""
    with get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO pending_trades
               (market_id, trader_wallet, token_id, side, outcome,
                usdc_amount, shares_to_sell, fund_id,
                signal_price, signal_shares, signal_usdc_value, signal_title, signal_timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                intent.market_id, intent.trader_wallet, intent.token_id,
                intent.side, intent.outcome,
                intent.usdc_amount, intent.shares_to_sell, intent.fund_id,
                signal.price if signal else None,
                signal.shares if signal else None,
                signal.usdc_value if signal else None,
                signal.title if signal else "",
                signal.timestamp if signal else 0,
            ),
        )
        trade_id = cursor.lastrowid
    log.info(
        "Trade queued for approval: #%d %s %s $%.2f",
        trade_id, intent.side, intent.market_id[:12], intent.usdc_amount,
    )
    return trade_id


def get_pending_trades() -> list[dict]:
    """Get all trades with status='pending', ordered by creation time."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_trades WHERE status = 'pending' ORDER BY created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def approve_trade(trade_id: int) -> Optional[dict]:
    """Mark a pending trade as approved. Returns the trade dict or None."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_trades SET status = 'approved', decided_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (trade_id,),
        )
        row = conn.execute("SELECT * FROM pending_trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None


def reject_trade(trade_id: int) -> Optional[dict]:
    """Mark a pending trade as rejected."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_trades SET status = 'rejected', decided_at = CURRENT_TIMESTAMP WHERE id = ? AND status = 'pending'",
            (trade_id,),
        )
        row = conn.execute("SELECT * FROM pending_trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None


def expire_old_trades(timeout_minutes: int = 10) -> int:
    """Mark pending trades older than timeout as expired. Returns count expired."""
    with get_conn() as conn:
        cursor = conn.execute(
            """UPDATE pending_trades SET status = 'expired', decided_at = CURRENT_TIMESTAMP
               WHERE status = 'pending' AND created_at < datetime('now', ?)""",
            (f"-{timeout_minutes} minutes",),
        )
        count = cursor.rowcount
    if count > 0:
        log.info("Expired %d pending trade(s) (older than %d minutes)", count, timeout_minutes)
    return count


def mark_executed(trade_id: int) -> None:
    """Mark an approved trade as executed."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE pending_trades SET status = 'executed', executed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (trade_id,),
        )


def get_approved_trades() -> list[dict]:
    """Get trades approved but not yet executed."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pending_trades WHERE status = 'approved' ORDER BY decided_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def log_activity(event_type: str, detail: str = "", ip_address: str = "") -> None:
    """Log an operator activity event."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (event_type, detail, ip_address) VALUES (?, ?, ?)",
            (event_type, detail, ip_address),
        )


def get_recent_activity(limit: int = 50) -> list[dict]:
    """Get recent activity log entries."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
