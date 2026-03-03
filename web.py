#!/usr/bin/env python3
# web.py — DenseWealth web dashboard (FastAPI)
#
# Usage:
#   python web.py              → starts on http://localhost:8050
#   python web.py --port 9000  → custom port

import argparse
import os
import sys
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from config import (
    ALLOCATION_FUNDS,
    COPY_STRATEGY,
    FUND_CONFIGS,
    MAX_MARKET_PCT,
    MAX_TRADE_PCT,
    MAX_WALLET_PCT,
    PAPER_STARTING_BALANCE,
    POLL_INTERVAL,
    get_active_wallets,
)
from db import (
    add_tracked_wallet,
    get_all_fund_stats,
    get_all_positions,
    get_all_tracked_wallets,
    get_allocation_summary,
    get_distribution_history,
    get_fund_balance,
    get_last_distribution,
    get_paper_balance,
    get_paper_stats,
    get_pnl_by_period,
    get_tracked_wallets,
    get_trade_history,
    get_trades_for_simulation,
    get_trader_performance,
    get_wallet_exposure,
    get_wallet_label,
    remove_tracked_wallet,
    seed_wallets,
    update_wallet_label,
    # Futures imports
    add_futures_tracked_wallet,
    get_all_futures_positions,
    get_all_futures_tracked_wallets,
    get_futures_account,
    get_futures_trade_history,
    get_futures_tracked_wallets,
    remove_futures_tracked_wallet,
    reset_futures_account,
    toggle_futures_wallet,
)
from auth import (
    authenticate,
    clear_session,
    create_session,
    get_current_user,
    require_auth,
    require_operator,
)
from approval import (
    approve_trade,
    get_approval_mode,
    get_pending_trades,
    get_recent_activity,
    log_activity,
    reject_trade,
    set_approval_mode,
)
from simulator import SimParams, optimize, run_simulation
from portfolio import get_trader_portfolio, refresh_all_portfolios
from wallet import get_wallet_status
import mode as mode_module
from position_manager import is_realloc_enabled, set_realloc_enabled

app = FastAPI(title="DenseWealth Dashboard")


# ── Security middleware ───────────────────────────────────────────────────────

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.middleware.cors import CORSMiddleware
import time as _time
import collections

# Security headers on every response
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        return response

app.add_middleware(SecurityHeadersMiddleware)

# CORS — only allow configured origins
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

# ── Login rate limiter (in-memory) ────────────────────────────────────────────

_LOGIN_WINDOW = 300  # 5 minutes
_LOGIN_MAX_ATTEMPTS = 5
_login_attempts: dict[str, list[float]] = collections.defaultdict(list)

def _check_rate_limit(ip: str) -> bool:
    """Returns True if the IP is rate-limited (too many failed attempts)."""
    now = _time.time()
    attempts = _login_attempts[ip]
    # Prune old attempts outside the window
    _login_attempts[ip] = [t for t in attempts if now - t < _LOGIN_WINDOW]
    return len(_login_attempts[ip]) >= _LOGIN_MAX_ATTEMPTS

def _record_failed_login(ip: str) -> None:
    _login_attempts[ip].append(_time.time())


# ── Request models ────────────────────────────────────────────────────────────


class ModeRequest(BaseModel):
    mode: str = Field(..., max_length=20)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=512)


class ApprovalModeRequest(BaseModel):
    mode: str = Field(..., max_length=20)


class ReallocToggleRequest(BaseModel):
    enabled: bool


class TradeDecisionRequest(BaseModel):
    trade_id: int
    action: str = Field(..., max_length=20)


class SimulateRequest(BaseModel):
    hours: int = Field(24, ge=1, le=8760)
    trader_wallet: str | None = Field(None, max_length=100)
    starting_balance: float = Field(1000.0, gt=0, le=10_000_000)
    max_trade_pct: float = Field(0.05, gt=0, le=1.0)
    max_wallet_pct: float = Field(0.30, gt=0, le=1.0)
    max_market_pct: float = Field(0.10, gt=0, le=1.0)
    slippage_bps: float = Field(10.0, ge=0, le=1000)
    copy_strategy: str = Field("proportional", max_length=50)
    copy_amount_usdc: float = Field(10.0, gt=0, le=1_000_000)


class OptimizeRequest(BaseModel):
    hours: int = Field(24, ge=1, le=8760)
    trader_wallet: str | None = Field(None, max_length=100)
    starting_balance: float = Field(1000.0, gt=0, le=10_000_000)
    top_n: int = Field(20, ge=1, le=100)


class AddWalletRequest(BaseModel):
    address: str = Field(..., min_length=1, max_length=100)
    label: str = Field("", max_length=100)


class UpdateWalletRequest(BaseModel):
    label: str = Field(..., max_length=100)


class AddFuturesWalletRequest(BaseModel):
    address: str = Field(..., min_length=1, max_length=100)
    label: str = Field("", max_length=100)


class ResetFuturesBalanceRequest(BaseModel):
    balance: float = Field(1000.0, gt=0, le=10_000_000)


class ResetBalanceRequest(BaseModel):
    balance: float = Field(..., gt=0, le=10_000_000)


TEMPLATE_PATH = Path(__file__).parent / "templates" / "index.html"
LEGACY_TEMPLATE_PATH = Path(__file__).parent / "templates" / "dashboard.html"
SIMULATE_TEMPLATE_PATH = Path(__file__).parent / "templates" / "simulate.html"
LOGIN_TEMPLATE_PATH = Path(__file__).parent / "templates" / "login.html"
STATIC_PATH = Path(__file__).parent / "static"

# Mount static files
if STATIC_PATH.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")


def _wallet_label(addr: str) -> str:
    return get_wallet_label(addr)


# ── AUTH ROUTES ───────────────────────────────────────────────────────────────


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse("/", status_code=302)
    return LOGIN_TEMPLATE_PATH.read_text(encoding="utf-8")


@app.post("/api/login")
async def api_login(req: LoginRequest, request: Request, response: Response):
    ip = request.client.host if request.client else ""

    # Rate limit check
    if _check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again in 5 minutes.")

    role = authenticate(req.username, req.password)
    if not role:
        _record_failed_login(ip)
        log_activity("login_failed", f"username={req.username}", ip)
        return {"success": False, "error": "Invalid username or password"}

    create_session(response, req.username, role)
    log_activity("login", f"username={req.username} role={role}", ip)
    return {"success": True, "role": role}


@app.post("/api/logout")
async def api_logout(request: Request, response: Response):
    log_activity("logout", "", request.client.host if request.client else "")
    clear_session(response)
    return {"success": True}


@app.get("/api/me")
async def api_me(user: dict = Depends(require_auth)):
    return {"username": user["username"], "role": user["role"]}


# ── PROTECTED PAGE ROUTES ─────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/legacy", response_class=HTMLResponse)
async def legacy_dashboard(request: Request):
    """Legacy dashboard fallback."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return LEGACY_TEMPLATE_PATH.read_text(encoding="utf-8")


@app.get("/simulate", response_class=HTMLResponse)
async def simulate_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    return SIMULATE_TEMPLATE_PATH.read_text(encoding="utf-8")


# ── APPROVAL ROUTES ───────────────────────────────────────────────────────────


@app.get("/api/approval-mode")
async def api_get_approval_mode(user: dict = Depends(require_auth)):
    return {"mode": get_approval_mode()}


@app.post("/api/approval-mode")
async def api_set_approval_mode(req: ApprovalModeRequest, request: Request, user: dict = Depends(require_auth)):
    try:
        new_mode = set_approval_mode(req.mode)
        log_activity("toggle_approval", f"mode={new_mode}", request.client.host if request.client else "")
        return {"mode": new_mode}
    except ValueError as e:
        return {"error": str(e)}


@app.get("/api/realloc")
async def api_realloc_get(user: dict = Depends(require_auth)):
    """Check if inactive wallet reallocation is enabled."""
    return {"enabled": is_realloc_enabled()}


@app.post("/api/realloc")
async def api_realloc_set(req: ReallocToggleRequest, request: Request, user: dict = Depends(require_auth)):
    """Toggle inactive wallet budget reallocation (7min timeout, 30% redistribution)."""
    new_state = set_realloc_enabled(req.enabled)
    log_activity("toggle_realloc", f"enabled={new_state}", request.client.host if request.client else "")
    return {"enabled": new_state}


@app.get("/api/pending-trades")
async def api_pending_trades(user: dict = Depends(require_auth)):
    trades = get_pending_trades()
    return {"trades": trades, "count": len(trades)}


@app.post("/api/trade-decision")
async def api_trade_decision(req: TradeDecisionRequest, request: Request, user: dict = Depends(require_auth)):
    ip = request.client.host if request.client else ""
    if req.action == "approve":
        trade = approve_trade(req.trade_id)
        log_activity("approve", f"trade_id={req.trade_id}", ip)
    elif req.action == "reject":
        trade = reject_trade(req.trade_id)
        log_activity("reject", f"trade_id={req.trade_id}", ip)
    else:
        return {"error": f"Unknown action: {req.action}"}

    if not trade:
        return {"error": "Trade not found or already decided"}
    return {"success": True, "trade": trade}


@app.post("/api/approve-all")
async def api_approve_all(request: Request, user: dict = Depends(require_auth)):
    pending = get_pending_trades()
    approved_count = 0
    for t in pending:
        approve_trade(t["id"])
        approved_count += 1
    if approved_count > 0:
        log_activity("approve_all", f"count={approved_count}", request.client.host if request.client else "")
    return {"approved": approved_count}


@app.post("/api/reject-all")
async def api_reject_all(request: Request, user: dict = Depends(require_auth)):
    pending = get_pending_trades()
    rejected_count = 0
    for t in pending:
        reject_trade(t["id"])
        rejected_count += 1
    if rejected_count > 0:
        log_activity("reject_all", f"count={rejected_count}", request.client.host if request.client else "")
    return {"rejected": rejected_count}


@app.get("/api/activity")
async def api_activity(user: dict = Depends(require_auth)):
    return get_recent_activity(50)


# ── PAPER BALANCE RESET ──────────────────────────────────────────────────────


@app.post("/api/reset-balance")
async def api_reset_balance(req: ResetBalanceRequest, request: Request, user: dict = Depends(require_auth)):
    """Reset paper trading balance. Clears all positions and trade history."""
    import mode as _mode
    if _mode.get_mode() != "paper":
        return {"error": "Can only reset balance in paper mode."}

    if req.balance < 1:
        return {"error": "Balance must be at least $1."}

    from db import get_conn
    with get_conn() as conn:
        # Reset all fund balances
        conn.execute("DELETE FROM fund_accounts")
        # Clear positions and trades
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM trade_history")
        conn.execute("DELETE FROM watched_trades")
        conn.execute("DELETE FROM pending_trades")
        # Clear paper_account legacy table
        conn.execute("DELETE FROM paper_account")

    # Re-initialize with new balance
    from db import init_paper_account
    init_paper_account(req.balance)

    log_activity("reset_balance", f"new_balance=${req.balance:.2f} by={user['username']}", request.client.host if request.client else "")
    return {"success": True, "balance": req.balance}


# ── EXISTING DATA ROUTES (now protected) ──────────────────────────────────────


@app.get("/api/stats")
async def api_stats(user: dict = Depends(require_auth)):
    # Aggregate across all funds
    all_funds = get_all_fund_stats()
    total_balance = sum(f.get("balance_usdc", 0) for f in all_funds)
    total_pnl = sum(f.get("total_pnl", 0) for f in all_funds)
    total_trades = sum(f.get("total_trades", 0) for f in all_funds)
    pnl_pct = (total_pnl / PAPER_STARTING_BALANCE * 100) if PAPER_STARTING_BALANCE > 0 else 0

    return {
        "balance": round(total_balance, 2),
        "total_pnl": round(total_pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "total_trades": total_trades,
        "strategy": COPY_STRATEGY,
        "starting_balance": PAPER_STARTING_BALANCE,
    }


@app.get("/api/pnl")
async def api_pnl(period: str = "all", user: dict = Depends(require_auth)):
    """
    P&L breakdown by time period with APR projections.
    period: 1h, 6h, 12h, 24h, 7d, 30d, all
    """
    period_map = {
        "1h": 60,
        "6h": 360,
        "12h": 720,
        "24h": 1440,
        "7d": 10080,
        "30d": 43200,
        "all": None,
    }
    minutes = period_map.get(period)
    if period != "all" and minutes is None:
        minutes = 1440  # default 24h

    data = get_pnl_by_period(minutes)
    elapsed = data["elapsed_min"]
    pnl = data["pnl"]
    starting = PAPER_STARTING_BALANCE

    # APR projections (annualize the rate from the observed window)
    rates = {}
    if elapsed > 0 and starting > 0:
        rate_per_min = pnl / starting / elapsed
        rates = {
            "per_min": round(rate_per_min * 100, 4),
            "per_hr": round(rate_per_min * 60 * 100, 2),
            "per_day": round(rate_per_min * 60 * 24 * 100, 2),
            "per_mo": round(rate_per_min * 60 * 24 * 30 * 100, 2),
            "per_yr": round(rate_per_min * 60 * 24 * 365 * 100, 2),
        }

    wins = data["wins"]
    losses = data["losses"]
    resolved = wins + losses

    return {
        "period": period,
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl / starting * 100, 2) if starting > 0 else 0,
        "buys": data["buys"],
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / resolved * 100, 1) if resolved > 0 else 0,
        "elapsed_min": round(elapsed, 1),
        "elapsed_hrs": round(elapsed / 60, 1),
        "first_trade": data["first_trade"],
        "last_trade": data["last_trade"],
        "rates": rates,
    }


@app.get("/api/positions")
async def api_positions(user: dict = Depends(require_auth)):
    positions = get_all_positions()
    result = []
    for pos in positions:
        avg_cost = pos["usdc_spent"] / pos["shares"] if pos["shares"] > 0 else 0
        result.append({
            "market_id": pos["market_id"],
            "market_short": pos["market_id"][:12] + "...",
            "wallet": _wallet_label(pos["trader_wallet"]),
            "wallet_addr": pos["trader_wallet"],
            "side": pos["side"],
            "outcome": pos.get("outcome", ""),
            "shares": round(pos["shares"], 4),
            "usdc_spent": round(pos["usdc_spent"], 2),
            "avg_cost": round(avg_cost, 4),
            "fund_id": pos.get("fund_id", "main"),
        })
    return result


@app.get("/api/trades")
async def api_trades(user: dict = Depends(require_auth)):
    trades = get_trade_history(30)
    result = []
    for t in trades:
        ts = t["timestamp"]
        if isinstance(ts, str) and len(ts) > 10:
            ts = ts[11:19]
        result.append({
            "time": str(ts),
            "side": t["side"],
            "outcome": t.get("outcome", ""),
            "shares": round(t["shares"], 4),
            "price": round(t["price"] or 0, 4),
            "usdc_amount": round(t["usdc_amount"], 2),
            "market_id": t["market_id"][:12] + "...",
            "success": bool(t["success"]),
            "fund_id": t.get("fund_id", "main"),
        })
    return result


@app.get("/api/wallets")
async def api_wallets(user: dict = Depends(require_auth)):
    balance = get_paper_balance()
    wallet_budget = balance * MAX_WALLET_PCT
    wallets = get_active_wallets()
    result = []
    for wallet in wallets:
        exposure = get_wallet_exposure(wallet)
        remaining = wallet_budget - exposure
        usage_pct = (exposure / wallet_budget * 100) if wallet_budget > 0 else 0
        portfolio = get_trader_portfolio(wallet)

        result.append({
            "address": wallet,
            "label": _wallet_label(wallet),
            "portfolio": round(portfolio, 0),
            "exposure": round(exposure, 2),
            "budget": round(wallet_budget, 2),
            "remaining": round(remaining, 2),
            "usage_pct": round(usage_pct, 1),
        })
    return result


@app.get("/api/wallets/managed")
async def api_wallets_managed(user: dict = Depends(require_auth)):
    """Get all tracked wallets (for management UI)."""
    return get_all_tracked_wallets()


@app.post("/api/wallets")
async def api_add_wallet(req: AddWalletRequest, request: Request, user: dict = Depends(require_auth)):
    """Add a new wallet to track."""
    import re
    addr = req.address.strip().lower()
    if not re.match(r"^0x[0-9a-f]{40}$", addr):
        return {"error": "Invalid Ethereum address. Must be 0x followed by 40 hex characters."}

    added = add_tracked_wallet(addr, req.label.strip())
    if not added:
        return {"error": "Wallet already exists."}

    log_activity("add_wallet", f"address={addr} label={req.label.strip()}", request.client.host if request.client else "")
    return {"success": True, "address": addr, "label": req.label.strip()}


@app.delete("/api/wallets/{address}")
async def api_remove_wallet(address: str, request: Request, user: dict = Depends(require_auth)):
    """Remove a tracked wallet."""
    removed = remove_tracked_wallet(address)
    if not removed:
        return {"error": "Wallet not found."}

    log_activity("remove_wallet", f"address={address}", request.client.host if request.client else "")
    return {"success": True}


@app.patch("/api/wallets/{address}")
async def api_update_wallet(address: str, req: UpdateWalletRequest, request: Request, user: dict = Depends(require_auth)):
    """Update a wallet's label."""
    updated = update_wallet_label(address, req.label.strip())
    if not updated:
        return {"error": "Wallet not found."}

    log_activity("update_wallet_label", f"address={address} label={req.label.strip()}", request.client.host if request.client else "")
    return {"success": True}


@app.get("/api/config")
async def api_config(user: dict = Depends(require_auth)):
    return {
        "mode": mode_module.get_mode(),
        "strategy": COPY_STRATEGY,
        "max_trade_pct": MAX_TRADE_PCT,
        "max_wallet_pct": MAX_WALLET_PCT,
        "max_market_pct": MAX_MARKET_PCT,
        "poll_interval": POLL_INTERVAL,
        "starting_balance": PAPER_STARTING_BALANCE,
        "wallets": len(get_active_wallets()),
        "realloc_enabled": is_realloc_enabled(),
    }


@app.get("/api/mode")
async def api_mode_get(user: dict = Depends(require_auth)):
    current = mode_module.get_mode()
    ready = mode_module.can_go_live()
    return {"mode": current, "ready": ready}


@app.post("/api/mode")
async def api_mode_set(req: ModeRequest, user: dict = Depends(require_operator)):
    ready = mode_module.can_go_live()
    target = req.mode.lower().strip()

    if target not in ("paper", "global", "us"):
        return {"error": f"Unknown mode: '{target}'. Use: paper | global | us", "mode": mode_module.get_mode()}

    if not ready.get(target, False):
        missing = []
        if target == "global":
            missing = [k for k in ["POLY_PRIVATE_KEY"] if not os.getenv(k)]
        elif target == "us":
            missing = [k for k in ["POLY_US_API_KEY", "POLY_US_API_SECRET", "POLY_US_API_PASSPHRASE"] if not os.getenv(k)]
        return {"error": f"Missing credentials for {target}: {missing}", "mode": mode_module.get_mode(), "ready": ready}

    new_mode = mode_module.set_mode(target)
    return {"mode": new_mode, "ready": ready}


@app.get("/api/trader-stats")
async def api_trader_stats(user: dict = Depends(require_auth)):
    rows = get_trader_performance()
    result = []
    for r in rows:
        wins = r["wins"] or 0
        losses = r["losses"] or 0
        resolved = wins + losses
        win_rate = (wins / resolved * 100) if resolved > 0 else 0
        total_deployed = r["total_deployed"] or 0
        total_payouts = r["total_payouts"] or 0
        total_buys = r["total_buys"] or 0
        if total_buys > 0 and resolved > 0:
            est_resolved_cost = total_deployed * (resolved / total_buys)
            realized_pnl = total_payouts - est_resolved_cost
        else:
            realized_pnl = 0

        result.append({
            "wallet": r["trader_wallet"],
            "label": _wallet_label(r["trader_wallet"]),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "total_buys": total_buys,
            "total_deployed": round(total_deployed, 2),
            "total_payouts": round(total_payouts, 2),
            "realized_pnl": round(realized_pnl, 2),
        })
    return result


@app.get("/api/wallet-status")
async def api_wallet_status(user: dict = Depends(require_auth)):
    current_mode = mode_module.get_mode()
    wallet = get_wallet_status()
    return {
        "mode": current_mode,
        **wallet,
    }


@app.get("/api/funds")
async def api_funds(user: dict = Depends(require_auth)):
    """Per-fund stats: balance, P&L, trade count, config."""
    from config import ALLOCATION_THRESHOLD

    all_stats = get_all_fund_stats()
    stat_map = {s["fund_id"]: s for s in all_stats}

    fund_colors = {"main": "blue", "charity": "green", "savings": "yellow", "family": "purple"}
    result = []
    for fund_id, cfg in FUND_CONFIGS.items():
        stats = stat_map.get(fund_id, {})
        starting = PAPER_STARTING_BALANCE * cfg["pct"]
        balance = stats.get("balance_usdc", starting)
        pnl = stats.get("total_pnl", 0)
        pnl_pct = (pnl / starting * 100) if starting > 0 else 0

        # For allocation funds (starting=$0), show status relative to threshold
        status = "active"
        if fund_id != "main" and starting == 0:
            main_balance = get_fund_balance("main")
            if balance == 0 and main_balance < ALLOCATION_THRESHOLD:
                status = "waiting"  # waiting for main to reach threshold

        # Last monthly distribution info
        last_dist = get_last_distribution(fund_id) if fund_id != "main" else None

        result.append({
            "fund_id": fund_id,
            "name": fund_id.capitalize(),
            "color": fund_colors.get(fund_id, "gray"),
            "balance": round(balance, 2),
            "starting_balance": round(starting, 2),
            "total_pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "total_trades": stats.get("total_trades", 0),
            "max_trade_pct": cfg["max_trade_pct"],
            "max_wallet_pct": cfg["max_wallet_pct"],
            "max_market_pct": cfg["max_market_pct"],
            "copy_strategy": cfg["copy_strategy"],
            "status": status,
            "last_distribution": {
                "month": last_dist["month"],
                "amount": round(last_dist["amount"], 2),
                "status": last_dist["status"],
            } if last_dist and last_dist["amount"] > 0 else None,
        })
    return result


@app.get("/api/distributions")
async def api_distributions(user: dict = Depends(require_auth)):
    """Monthly distribution history."""
    history = get_distribution_history(50)
    return [
        {
            "month": d["month"],
            "fund_id": d["fund_id"],
            "fund_name": d["fund_id"].capitalize(),
            "amount": round(d["amount"], 2),
            "wallet": d["wallet_address"][:12] + "..." if d["wallet_address"] else "",
            "status": d["status"],
            "date": d["created_at"],
        }
        for d in history
        if d["amount"] > 0
    ]


@app.get("/api/allocations")
async def api_allocations(user: dict = Depends(require_auth)):
    summary = get_allocation_summary()
    fund_map = {s["fund_name"]: s for s in summary}
    result = []
    for fund in ALLOCATION_FUNDS:
        data = fund_map.get(fund["name"], {})
        result.append({
            "name": fund["name"],
            "pct": round(fund["pct"] * 100),
            "wallet": fund["wallet"] or None,
            "total_allocated": round(data.get("total_allocated", 0), 2),
            "pending": round(data.get("pending", 0), 2),
            "transferred": round(data.get("transferred", 0), 2),
            "num_allocations": data.get("num_allocations", 0),
        })

    alloc_total_pct = sum(f["pct"] for f in ALLOCATION_FUNDS)
    trading_pct = round((1 - alloc_total_pct) * 100)

    return {
        "funds": result,
        "trading_pct": trading_pct,
    }


# ── STRATEGY SIMULATOR ─────────────────────────────────────────────────────


class ActualResultsRequest(BaseModel):
    hours: int = 24
    trader_wallet: str | None = None


@app.post("/api/actual-results")
async def api_actual_results(req: ActualResultsRequest, user: dict = Depends(require_auth)):
    """Compute actual performance from recorded trades."""
    trades = get_trades_for_simulation(
        hours=req.hours,
        trader_wallet=req.trader_wallet,
    )

    if not trades:
        return {"error": "No trades found for the specified time range and trader."}

    buys = [t for t in trades if t["side"] == "BUY" and t["success"]]
    resolves = [t for t in trades if t["side"] == "RESOLVE"]
    sells = [t for t in trades if t["side"] == "SELL"]

    total_deployed = sum(t["usdc_amount"] for t in buys)
    total_payout = sum(t["usdc_amount"] for t in resolves)
    total_sell_revenue = sum(t["usdc_amount"] for t in sells)

    wins = sum(1 for t in resolves if (t["price"] or 0) > 0)
    losses = sum(1 for t in resolves if (t["price"] or 0) == 0)
    resolved = wins + losses

    resolved_pnl = total_payout - sum(
        t["usdc_amount"] for t in buys
        if any(r["market_id"] == t["market_id"] and r["trader_wallet"] == t["trader_wallet"] for r in resolves)
    )

    total_pnl = total_payout + total_sell_revenue - total_deployed

    balance_curve = []
    running_balance = PAPER_STARTING_BALANCE
    peak = running_balance
    max_dd = 0.0

    for t in trades:
        if t["side"] == "BUY":
            running_balance -= t["usdc_amount"]
        elif t["side"] in ("SELL", "RESOLVE"):
            running_balance += t["usdc_amount"]

        peak = max(peak, running_balance)
        dd = peak - running_balance
        max_dd = max(max_dd, dd)

        ts = t["timestamp"]
        if isinstance(ts, str) and len(ts) > 10:
            ts = ts[11:19]
        balance_curve.append({"time": str(ts), "balance": round(running_balance, 2)})

    win_rate = (wins / resolved * 100) if resolved > 0 else 0
    max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0

    trade_list = []
    for t in trades:
        ts = t["timestamp"]
        if isinstance(ts, str) and len(ts) > 10:
            ts = ts[11:19]

        pnl = 0.0
        if t["side"] == "RESOLVE":
            pnl = t["usdc_amount"] - (t["shares"] * 1.0 if (t["price"] or 0) > 0 else 0)

        trade_list.append({
            "time": str(ts),
            "side": t["side"],
            "outcome": t.get("outcome", ""),
            "shares": round(t["shares"], 4),
            "price": round(t["price"] or 0, 4),
            "usdc_amount": round(t["usdc_amount"], 2),
            "market_id": t["market_id"][:12] + "...",
            "fund_id": t.get("fund_id", "main"),
            "balance_after": round(running_balance, 2),
        })

    return {
        "summary": {
            "starting_balance": PAPER_STARTING_BALANCE,
            "final_balance": round(running_balance, 2),
            "total_pnl": round(total_pnl, 2),
            "pnl_pct": round(total_pnl / PAPER_STARTING_BALANCE * 100, 2) if PAPER_STARTING_BALANCE > 0 else 0,
            "total_trades": len(trades),
            "buys_executed": len(buys),
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 1),
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 1),
            "total_deployed": round(total_deployed, 2),
            "total_payout": round(total_payout, 2),
        },
        "trades": trade_list,
        "trade_count": len(trades),
    }


@app.post("/api/simulate")
async def api_simulate(req: SimulateRequest, user: dict = Depends(require_auth)):
    trades = get_trades_for_simulation(
        hours=req.hours,
        trader_wallet=req.trader_wallet,
    )

    if not trades:
        return {"error": "No trades found for the specified time range and trader."}

    params = SimParams(
        starting_balance=req.starting_balance,
        max_trade_pct=req.max_trade_pct,
        max_wallet_pct=req.max_wallet_pct,
        max_market_pct=req.max_market_pct,
        slippage_bps=req.slippage_bps,
        copy_strategy=req.copy_strategy,
        copy_amount_usdc=req.copy_amount_usdc,
    )

    result = run_simulation(trades, params, PAPER_STARTING_BALANCE)

    return {
        "summary": {
            "starting_balance": result.starting_balance,
            "final_balance": round(result.final_balance, 2),
            "total_pnl": round(result.total_pnl, 2),
            "pnl_pct": round(result.pnl_pct, 2),
            "total_trades": result.total_trades,
            "buys_executed": result.buys_executed,
            "wins": result.wins,
            "losses": result.losses,
            "win_rate": round(result.win_rate, 1),
            "max_drawdown": round(result.max_drawdown, 2),
            "max_drawdown_pct": round(result.max_drawdown_pct, 1),
            "peak_balance": round(result.peak_balance, 2),
        },
        "trades": [
            {
                "timestamp": t.timestamp[11:19] if len(t.timestamp) > 10 else t.timestamp,
                "market_id": t.market_id[:12] + "...",
                "side": t.side,
                "outcome": t.outcome,
                "original_usdc": round(t.original_usdc, 2),
                "sim_usdc": round(t.sim_usdc, 2),
                "sim_shares": round(t.sim_shares, 4),
                "price": round(t.price, 4),
                "sim_balance_after": round(t.sim_balance_after, 2),
                "sim_pnl": round(t.sim_pnl, 2),
            }
            for t in result.trades
        ],
        "trade_count": len(trades),
    }


@app.post("/api/optimize")
async def api_optimize(req: OptimizeRequest, user: dict = Depends(require_auth)):
    trades = get_trades_for_simulation(
        hours=req.hours,
        trader_wallet=req.trader_wallet,
    )

    if not trades:
        return {"error": "No trades found for the specified time range and trader."}

    opt = optimize(trades, actual_starting_balance=req.starting_balance, top_n=req.top_n)

    return {
        "best": {
            "strategy": opt.best.params.copy_strategy,
            "max_trade_pct": opt.best.params.max_trade_pct,
            "max_wallet_pct": opt.best.params.max_wallet_pct,
            "max_market_pct": opt.best.params.max_market_pct,
            "slippage_bps": opt.best.params.slippage_bps,
            "copy_amount_usdc": opt.best.params.copy_amount_usdc,
            "final_balance": round(opt.best.final_balance, 2),
            "total_pnl": round(opt.best.total_pnl, 2),
            "pnl_pct": round(opt.best.pnl_pct, 2),
            "win_rate": round(opt.best.win_rate, 1),
            "max_drawdown_pct": round(opt.best.max_drawdown_pct, 1),
            "buys_executed": opt.best.buys_executed,
            "wins": opt.best.wins,
            "losses": opt.best.losses,
        },
        "top_results": opt.all_results,
        "total_combos_tested": opt.total_combos,
        "trade_count": len(trades),
    }


# ── MULTI-ACCOUNT API ────────────────────────────────────────────────────────

from accounts import AccountManager, get_active_account, get_active_account_id, set_active_account
from credentials import (
    CredentialError,
    delete_credential,
    get_account_credentials_summary,
    is_master_key_set,
    mask_credential,
    store_credential,
    validate_polymarket_credentials,
)
from reserve import ReserveManager, get_all_reserve_statuses


class CreateAccountRequest(BaseModel):
    name: str
    description: str = ""
    account_type: str = "trading"
    starting_balance: float = 0
    risk_level: str = "moderate"


class UpdateAccountRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class UpdateProfileRequest(BaseModel):
    auto_trade_enabled: bool | None = None
    copy_strategy: str | None = None
    max_trade_pct: float | None = None
    max_wallet_pct: float | None = None
    max_market_pct: float | None = None
    risk_level: str | None = None


class UpdateReserveRequest(BaseModel):
    reserve_pct: float | None = None
    cycling_enabled: bool | None = None
    cycle_schedule: str | None = None
    cycle_pct: float | None = None


class StoreCredentialRequest(BaseModel):
    platform: str
    credential_type: str
    value: str


class SetWalletAllocationRequest(BaseModel):
    allocation_pct: float | None = None


class AddFundsRequest(BaseModel):
    amount: float


@app.get("/api/accounts")
async def api_list_accounts(include_inactive: bool = False, user: dict = Depends(require_auth)):
    """List all trading accounts."""
    accounts = AccountManager.list_accounts(include_inactive)
    active_id = get_active_account_id()
    return {
        "accounts": accounts,
        "active_account_id": active_id,
    }


@app.post("/api/accounts")
async def api_create_account(req: CreateAccountRequest, request: Request, user: dict = Depends(require_operator)):
    """Create a new trading account."""
    try:
        account = AccountManager.create(
            name=req.name,
            description=req.description,
            account_type=req.account_type,
            starting_balance=req.starting_balance,
            risk_level=req.risk_level,
        )
        log_activity("create_account", f"id={account['id']} name={req.name}", request.client.host if request.client else "")
        return {"success": True, "account": account}
    except ValueError as e:
        return {"error": str(e)}


@app.get("/api/accounts/{account_id}")
async def api_get_account(account_id: int, user: dict = Depends(require_auth)):
    """Get a single account with full details."""
    account = AccountManager.get_full_account(account_id)
    if not account:
        return {"error": "Account not found"}
    return account


@app.patch("/api/accounts/{account_id}")
async def api_update_account(account_id: int, req: UpdateAccountRequest, request: Request, user: dict = Depends(require_operator)):
    """Update account details."""
    account = AccountManager.update(
        account_id,
        name=req.name,
        description=req.description,
        status=req.status,
    )
    if not account:
        return {"error": "Account not found"}
    log_activity("update_account", f"id={account_id}", request.client.host if request.client else "")
    return {"success": True, "account": account}


@app.delete("/api/accounts/{account_id}")
async def api_delete_account(account_id: int, request: Request, user: dict = Depends(require_operator)):
    """Delete an account."""
    try:
        deleted = AccountManager.delete(account_id)
        if deleted:
            log_activity("delete_account", f"id={account_id}", request.client.host if request.client else "")
            return {"success": True}
        return {"error": "Account not found"}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/api/accounts/{account_id}/activate")
async def api_activate_account(account_id: int, request: Request, user: dict = Depends(require_auth)):
    """Set the active account for trading."""
    if set_active_account(account_id):
        log_activity("switch_account", f"id={account_id}", request.client.host if request.client else "")
        return {"success": True, "active_account_id": account_id}
    return {"error": "Failed to activate account"}


@app.get("/api/accounts/active")
async def api_get_active_account(user: dict = Depends(require_auth)):
    """Get the currently active account."""
    account = get_active_account()
    return AccountManager.get_full_account(account["id"])


# ── Account Credentials ─────────────────────────────────────────────────────


@app.get("/api/accounts/{account_id}/credentials")
async def api_list_credentials(account_id: int, user: dict = Depends(require_operator)):
    """List credentials for an account (metadata only, no values)."""
    if not is_master_key_set():
        return {"error": "DENSEWEALTH_MASTER_KEY not configured", "credentials": []}

    creds = get_account_credentials_summary(account_id)
    validation = validate_polymarket_credentials(account_id, "polymarket_global")
    validation_us = validate_polymarket_credentials(account_id, "polymarket_us")

    # Calculate rotation status (90 days = recommended rotation period)
    from datetime import datetime, timedelta
    rotation_days = 90
    warning_days = 30  # Warn 30 days before rotation is needed
    now = datetime.now()

    needs_rotation = []
    for cred in creds:
        created = cred.get("created_at")
        if created:
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00")) if isinstance(created, str) else created
                age_days = (now - created_dt.replace(tzinfo=None)).days
                cred["age_days"] = age_days
                cred["needs_rotation"] = age_days >= rotation_days
                cred["rotation_warning"] = age_days >= (rotation_days - warning_days)
                if cred["needs_rotation"]:
                    needs_rotation.append(cred["credential_type"])
            except:
                cred["age_days"] = 0
                cred["needs_rotation"] = False
                cred["rotation_warning"] = False

    return {
        "credentials": creds,
        "polymarket_global": validation,
        "polymarket_us": validation_us,
        "master_key_set": True,
        "rotation_days": rotation_days,
        "needs_rotation": needs_rotation,
    }


@app.post("/api/accounts/{account_id}/credentials")
async def api_store_credential(account_id: int, req: StoreCredentialRequest, request: Request, user: dict = Depends(require_operator)):
    """Store an encrypted credential."""
    if not is_master_key_set():
        return {"error": "DENSEWEALTH_MASTER_KEY not configured"}

    try:
        store_credential(
            account_id=account_id,
            platform=req.platform,
            credential_type=req.credential_type,
            value=req.value,
        )
        log_activity(
            "store_credential",
            f"account={account_id} platform={req.platform} type={req.credential_type}",
            request.client.host if request.client else ""
        )
        return {
            "success": True,
            "masked_value": mask_credential(req.value),
        }
    except CredentialError as e:
        return {"error": str(e)}


@app.delete("/api/accounts/{account_id}/credentials/{platform}/{credential_type}")
async def api_delete_credential(
    account_id: int,
    platform: str,
    credential_type: str,
    request: Request,
    user: dict = Depends(require_operator)
):
    """Delete a stored credential."""
    deleted = delete_credential(account_id, platform, credential_type)
    if deleted:
        log_activity(
            "delete_credential",
            f"account={account_id} platform={platform} type={credential_type}",
            request.client.host if request.client else ""
        )
        return {"success": True}
    return {"error": "Credential not found"}


# ── Account Trading Profile ─────────────────────────────────────────────────


@app.get("/api/accounts/{account_id}/profile")
async def api_get_profile(account_id: int, user: dict = Depends(require_auth)):
    """Get trading profile for an account."""
    from db import get_trading_profile
    profile = get_trading_profile(account_id)
    return profile


@app.patch("/api/accounts/{account_id}/profile")
async def api_update_profile(account_id: int, req: UpdateProfileRequest, request: Request, user: dict = Depends(require_operator)):
    """Update trading profile settings."""
    profile = AccountManager.update_profile(
        account_id,
        auto_trade_enabled=req.auto_trade_enabled,
        copy_strategy=req.copy_strategy,
        max_trade_pct=req.max_trade_pct,
        max_wallet_pct=req.max_wallet_pct,
        max_market_pct=req.max_market_pct,
        risk_level=req.risk_level,
    )
    log_activity("update_profile", f"account={account_id}", request.client.host if request.client else "")
    return {"success": True, "profile": profile}


# ── Account Reserve ─────────────────────────────────────────────────────────


@app.get("/api/accounts/{account_id}/reserve")
async def api_get_reserve(account_id: int, user: dict = Depends(require_auth)):
    """Get reserve configuration and status."""
    return ReserveManager.get_status(account_id)


@app.patch("/api/accounts/{account_id}/reserve")
async def api_update_reserve(account_id: int, req: UpdateReserveRequest, request: Request, user: dict = Depends(require_operator)):
    """Update reserve configuration."""
    try:
        if req.reserve_pct is not None:
            ReserveManager.set_reserve_percentage(account_id, req.reserve_pct)

        if any([req.cycling_enabled is not None, req.cycle_schedule, req.cycle_pct is not None]):
            from db import get_reserve_config
            current = get_reserve_config(account_id)
            ReserveManager.configure_cycling(
                account_id,
                enabled=req.cycling_enabled if req.cycling_enabled is not None else current["cycling_enabled"],
                schedule=req.cycle_schedule or current["cycle_schedule"],
                cycle_pct=req.cycle_pct if req.cycle_pct is not None else current["cycle_pct"],
            )

        log_activity("update_reserve", f"account={account_id}", request.client.host if request.client else "")
        return {"success": True, **ReserveManager.get_status(account_id)}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/api/accounts/{account_id}/reserve/cycle")
async def api_trigger_cycle(account_id: int, request: Request, user: dict = Depends(require_operator)):
    """Manually trigger a reserve cycle."""
    result = ReserveManager.trigger_cycle(account_id)
    if result["cycled"]:
        log_activity("manual_cycle", f"account={account_id} amount=${result['cycle_amount']:.2f}", request.client.host if request.client else "")
    return result


@app.get("/api/reserve/all")
async def api_all_reserve_statuses(user: dict = Depends(require_auth)):
    """Get reserve status for all accounts."""
    return get_all_reserve_statuses()


# ── Account Balance Management ──────────────────────────────────────────────


@app.post("/api/accounts/{account_id}/funds/add")
async def api_add_funds(account_id: int, req: AddFundsRequest, request: Request, user: dict = Depends(require_operator)):
    """Add funds to an account."""
    if req.amount <= 0:
        return {"error": "Amount must be positive"}

    balance = AccountManager.add_funds(account_id, req.amount)
    log_activity("add_funds", f"account={account_id} amount=${req.amount:.2f}", request.client.host if request.client else "")
    return {"success": True, **balance}


@app.post("/api/accounts/{account_id}/funds/withdraw")
async def api_withdraw_funds(account_id: int, req: AddFundsRequest, request: Request, user: dict = Depends(require_operator)):
    """Withdraw funds from an account."""
    if req.amount <= 0:
        return {"error": "Amount must be positive"}

    try:
        balance = AccountManager.withdraw_funds(account_id, req.amount)
        log_activity("withdraw_funds", f"account={account_id} amount=${req.amount:.2f}", request.client.host if request.client else "")
        return {"success": True, **balance}
    except ValueError as e:
        return {"error": str(e)}


# ── Wallet Allocations ──────────────────────────────────────────────────────


@app.patch("/api/wallets/{address}/allocation")
async def api_set_wallet_allocation(address: str, req: SetWalletAllocationRequest, request: Request, user: dict = Depends(require_operator)):
    """Set allocation percentage for a wallet."""
    from db import update_wallet_allocation

    # Validate allocation is 0-100 or None
    if req.allocation_pct is not None:
        if req.allocation_pct < 0 or req.allocation_pct > 100:
            return {"error": "Allocation must be between 0 and 100"}

    updated = update_wallet_allocation(address, req.allocation_pct)
    if not updated:
        return {"error": "Wallet not found"}

    log_activity("set_allocation", f"wallet={address[:10]} pct={req.allocation_pct}", request.client.host if request.client else "")
    return {"success": True, "allocation_pct": req.allocation_pct}


@app.get("/api/wallets/allocations")
async def api_get_wallet_allocations(user: dict = Depends(require_auth)):
    """Get all wallet allocations."""
    from db import get_wallet_allocations
    return get_wallet_allocations()


@app.post("/api/wallets/allocations/normalize")
async def api_normalize_allocations(request: Request, user: dict = Depends(require_operator)):
    """Normalize wallet allocations to sum to 100%."""
    from db import get_wallet_allocations, normalize_wallet_allocations

    normalize_wallet_allocations()
    log_activity("normalize_allocations", "", request.client.host if request.client else "")
    return {"success": True, "allocations": get_wallet_allocations()}


# ── SETTINGS REQUESTS (Viewer submits, Operator approves) ───────────────────

from db import (
    approve_settings_request,
    create_settings_request,
    deny_settings_request,
    get_all_settings_requests,
    get_pending_request_count,
    get_pending_settings_requests,
    get_settings_request,
    get_user_settings_requests,
)
import json


class SettingsRequestCreate(BaseModel):
    request_type: str  # e.g., "reserve_pct", "risk_level", "wallet_allocation"
    category: str  # e.g., "reserve", "trading", "wallet"
    requested_value: str  # JSON string of the new value
    current_value: str | None = None
    reason: str = ""
    account_id: int | None = None


class SettingsRequestReview(BaseModel):
    action: str  # "approve" or "deny"
    note: str = ""


@app.get("/api/settings-requests")
async def api_list_settings_requests(
    pending_only: bool = False,
    my_requests: bool = False,
    user: dict = Depends(require_auth)
):
    """
    List settings requests.
    - Operators see all requests
    - Viewers see only their own requests
    """
    if user["role"] == "operator" and not my_requests:
        requests = get_all_settings_requests(include_resolved=not pending_only)
    else:
        requests = get_user_settings_requests(user["username"])

    pending_count = get_pending_request_count()

    return {
        "requests": requests,
        "pending_count": pending_count,
    }


@app.get("/api/settings-requests/pending")
async def api_pending_settings_requests(user: dict = Depends(require_operator)):
    """Get all pending settings requests (operator only)."""
    requests = get_pending_settings_requests()
    return {
        "requests": requests,
        "count": len(requests),
    }


@app.get("/api/settings-requests/count")
async def api_pending_request_count(user: dict = Depends(require_auth)):
    """Get count of pending settings requests."""
    return {"count": get_pending_request_count()}


@app.post("/api/settings-requests")
async def api_create_settings_request(req: SettingsRequestCreate, request: Request, user: dict = Depends(require_auth)):
    """
    Submit a settings change request.
    Viewers must submit requests; operators can apply changes directly.
    """
    # Operators can skip the request process
    if user["role"] == "operator":
        return {
            "success": False,
            "message": "Operators can apply changes directly without requests",
            "is_operator": True,
        }

    request_id = create_settings_request(
        request_type=req.request_type,
        category=req.category,
        requested_value=req.requested_value,
        submitted_by=user["username"],
        account_id=req.account_id,
        current_value=req.current_value,
        reason=req.reason,
    )

    log_activity(
        "submit_request",
        f"id={request_id} type={req.request_type} category={req.category}",
        request.client.host if request.client else ""
    )

    return {
        "success": True,
        "request_id": request_id,
        "message": "Request submitted for admin review",
    }


@app.get("/api/settings-requests/{request_id}")
async def api_get_settings_request(request_id: int, user: dict = Depends(require_auth)):
    """Get a single settings request."""
    req = get_settings_request(request_id)
    if not req:
        return {"error": "Request not found"}

    # Viewers can only see their own requests
    if user["role"] != "operator" and req["submitted_by"] != user["username"]:
        return {"error": "Access denied"}

    return req


@app.post("/api/settings-requests/{request_id}/review")
async def api_review_settings_request(
    request_id: int,
    req: SettingsRequestReview,
    request: Request,
    user: dict = Depends(require_operator)
):
    """
    Approve or deny a settings request (operator only).
    If approved, the setting change is applied automatically.
    """
    settings_req = get_settings_request(request_id)
    if not settings_req:
        return {"error": "Request not found"}

    if settings_req["status"] != "pending":
        return {"error": f"Request already {settings_req['status']}"}

    if req.action == "approve":
        result = approve_settings_request(request_id, user["username"], req.note)
        if result:
            # Apply the approved change
            applied = await _apply_settings_change(settings_req)
            log_activity(
                "approve_request",
                f"id={request_id} type={settings_req['request_type']} applied={applied}",
                request.client.host if request.client else ""
            )
            return {
                "success": True,
                "request": result,
                "applied": applied,
                "message": "Request approved and applied" if applied else "Request approved",
            }
    elif req.action == "deny":
        result = deny_settings_request(request_id, user["username"], req.note)
        if result:
            log_activity(
                "deny_request",
                f"id={request_id} type={settings_req['request_type']}",
                request.client.host if request.client else ""
            )
            return {
                "success": True,
                "request": result,
                "message": "Request denied",
            }

    return {"error": "Failed to process request"}


async def _apply_settings_change(settings_req: dict) -> bool:
    """
    Apply an approved settings change.
    Returns True if successfully applied.
    """
    try:
        request_type = settings_req["request_type"]
        account_id = settings_req["account_id"]
        value = json.loads(settings_req["requested_value"])

        # Reserve settings
        if request_type == "reserve_pct":
            ReserveManager.set_reserve_percentage(account_id, value)
            return True

        elif request_type == "reserve_cycling":
            ReserveManager.configure_cycling(
                account_id,
                enabled=value.get("enabled", False),
                schedule=value.get("schedule", "daily"),
                cycle_pct=value.get("cycle_pct", 10),
            )
            return True

        # Trading profile settings
        elif request_type == "risk_level":
            from db import apply_risk_preset
            apply_risk_preset(account_id, value)
            return True

        elif request_type == "trading_profile":
            from db import update_trading_profile
            update_trading_profile(
                account_id,
                auto_trade_enabled=value.get("auto_trade_enabled"),
                copy_strategy=value.get("copy_strategy"),
                max_trade_pct=value.get("max_trade_pct"),
                max_wallet_pct=value.get("max_wallet_pct"),
                max_market_pct=value.get("max_market_pct"),
            )
            return True

        # Wallet allocation
        elif request_type == "wallet_allocation":
            from db import update_wallet_allocation
            update_wallet_allocation(value["address"], value["allocation_pct"])
            return True

        # Mode changes (require operator to apply manually for safety)
        elif request_type == "mode_change":
            # Don't auto-apply mode changes - require manual action
            return False

        return False

    except Exception as e:
        log.error("Failed to apply settings change: %s", e)
        return False


# ── ADMIN DASHBOARD DATA ────────────────────────────────────────────────────


@app.get("/api/admin/overview")
async def api_admin_overview(user: dict = Depends(require_operator)):
    """
    Get admin overview data for the operator dashboard.
    """
    from db import get_all_fund_stats

    pending_trades = get_pending_trades()
    pending_requests = get_pending_settings_requests()
    all_accounts = AccountManager.list_accounts()
    fund_stats = get_all_fund_stats()
    reserve_statuses = get_all_reserve_statuses()

    # Calculate totals
    total_balance = sum(a["balance_usdc"] for a in all_accounts)
    total_pnl = sum(a["total_pnl"] for a in all_accounts)
    total_reserve = sum(a["reserve_balance"] for a in all_accounts)

    return {
        "pending_trades": {
            "count": len(pending_trades),
            "trades": pending_trades[:5],  # Show first 5
        },
        "pending_requests": {
            "count": len(pending_requests),
            "requests": pending_requests[:5],  # Show first 5
        },
        "accounts": {
            "count": len(all_accounts),
            "total_balance": round(total_balance, 2),
            "total_pnl": round(total_pnl, 2),
            "total_reserve": round(total_reserve, 2),
            "list": all_accounts,
        },
        "mode": mode_module.get_mode(),
        "approval_mode": get_approval_mode(),
        "realloc_enabled": is_realloc_enabled(),
    }


@app.get("/api/admin/activity")
async def api_admin_activity(limit: int = 50, user: dict = Depends(require_operator)):
    """Get recent activity log for admins."""
    return get_recent_activity(limit)


# ── ACCOUNT ACTIVATION ──────────────────────────────────────────────────────


@app.get("/api/activation/status")
async def api_activation_status(user: dict = Depends(require_auth)):
    """
    Get activation status for the active account.
    Returns what's needed to go live and current state.
    """
    from db import get_activation_status
    account_id = get_active_account_id()
    return get_activation_status(account_id)


@app.get("/api/activation/prompt")
async def api_activation_prompt(user: dict = Depends(require_operator)):
    """
    Check if activation prompt should be shown.
    Only shows for operators when account is ready but not yet activated.
    """
    from db import needs_activation_prompt, get_activation_status
    account_id = get_active_account_id()
    needs_prompt = needs_activation_prompt(account_id)

    if needs_prompt:
        status = get_activation_status(account_id)
        return {
            "show_prompt": True,
            "account_id": account_id,
            "account_name": status.get("account_name", "Main"),
            "credentials": status.get("credentials", {}),
            "balance": status.get("balance", 0),
            "available_modes": [
                m for m in ["global", "us"]
                if status.get("credentials", {}).get(f"{m}_ready")
            ],
        }

    return {"show_prompt": False}


class ActivationRequest(BaseModel):
    mode: str = "auto"  # "global", "us", or "auto"
    confirm: bool = False


@app.post("/api/activation/activate")
async def api_activate_account(
    req: ActivationRequest,
    request: Request,
    user: dict = Depends(require_operator),
):
    """
    Activate account for live trading.
    Operator only. Switches mode and enables auto-trading.
    """
    from db import activate_account

    if not req.confirm:
        return {"error": "Must confirm activation", "success": False}

    account_id = get_active_account_id()
    result = activate_account(account_id, req.mode)

    if result.get("success"):
        log_activity(
            "account_activation",
            f"Activated account {account_id} for {result.get('mode', 'unknown').upper()} trading",
            request.client.host if request.client else "",
        )

    return result


@app.post("/api/activation/deactivate")
async def api_deactivate_account(
    request: Request,
    user: dict = Depends(require_operator),
):
    """
    Deactivate account (switch back to paper mode).
    Operator only.
    """
    from db import deactivate_account

    account_id = get_active_account_id()
    result = deactivate_account(account_id)

    if result.get("success"):
        log_activity(
            "account_deactivation",
            f"Deactivated account {account_id}, switched to PAPER",
            request.client.host if request.client else "",
        )

    return result


# ── FUTURES COPY-TRADING ROUTES ──────────────────────────────────────────────


@app.get("/api/futures/stats")
async def api_futures_stats():
    """Get futures account stats (balance, margin, P&L)."""
    account = get_futures_account(1)
    positions = get_all_futures_positions(1)

    # Calculate total unrealized P&L
    unrealized_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)

    return {
        "balance": account.get("balance_usdc", 0),
        "margin_used": account.get("margin_used", 0),
        "margin_available": account.get("balance_usdc", 0) - account.get("margin_used", 0),
        "total_pnl": account.get("total_pnl", 0),
        "unrealized_pnl": unrealized_pnl,
        "total_trades": account.get("total_trades", 0),
        "open_positions": len(positions),
    }


@app.get("/api/futures/positions")
async def api_futures_positions():
    """Get all open futures positions."""
    positions = get_all_futures_positions(1)

    # Format for UI
    result = []
    for p in positions:
        result.append({
            "id": p.get("id"),
            "symbol": p.get("symbol"),
            "trader_wallet": p.get("trader_wallet"),
            "trader_short": p.get("trader_wallet", "")[:8] + "...",
            "side": p.get("side"),
            "entry_price": p.get("entry_price"),
            "size": p.get("size"),
            "size_usd": p.get("size", 0) * p.get("entry_price", 0),
            "leverage": p.get("leverage"),
            "margin_used": p.get("margin_used"),
            "unrealized_pnl": p.get("unrealized_pnl", 0),
            "pnl_pct": (p.get("unrealized_pnl", 0) / p.get("margin_used", 1) * 100) if p.get("margin_used", 0) > 0 else 0,
            "liquidation_price": p.get("liquidation_price"),
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at"),
        })

    return result


@app.get("/api/futures/trades")
async def api_futures_trades(limit: int = 50):
    """Get futures trade history."""
    trades = get_futures_trade_history(1, limit)

    # Format timestamps and add short wallet
    for t in trades:
        t["trader_short"] = t.get("trader_wallet", "")[:8] + "..."
        # Format timestamp if available
        if t.get("timestamp"):
            from datetime import datetime
            try:
                ts = t["timestamp"]
                if isinstance(ts, str):
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    t["time"] = dt.strftime("%H:%M:%S")
                else:
                    t["time"] = str(ts)
            except:
                t["time"] = str(t["timestamp"])

    return trades


@app.get("/api/futures/wallets")
async def api_futures_wallets():
    """Get all tracked Hyperliquid wallets for futures."""
    wallets = get_all_futures_tracked_wallets()

    for w in wallets:
        w["short"] = w.get("address", "")[:8] + "..." + w.get("address", "")[-6:]

    return wallets


@app.post("/api/futures/wallets")
async def api_add_futures_wallet(req: AddFuturesWalletRequest, user: dict = Depends(require_auth)):
    """Add a Hyperliquid wallet to track for futures."""
    address = req.address.strip()

    # Validate address format (basic check)
    if not address.startswith("0x") or len(address) != 42:
        return {"error": "Invalid address format. Must be 0x followed by 40 hex characters."}

    success = add_futures_tracked_wallet(address, req.label)
    if success:
        return {"success": True, "address": address.lower(), "label": req.label}
    else:
        return {"error": "Wallet already tracked or invalid address."}


@app.delete("/api/futures/wallets/{address}")
async def api_remove_futures_wallet(address: str, user: dict = Depends(require_auth)):
    """Remove a Hyperliquid wallet from futures tracking."""
    success = remove_futures_tracked_wallet(address)
    if success:
        return {"success": True, "address": address}
    else:
        return {"error": "Wallet not found"}


@app.patch("/api/futures/wallets/{address}")
async def api_toggle_futures_wallet(address: str, enabled: bool = True, user: dict = Depends(require_auth)):
    """Enable or disable a futures tracked wallet."""
    success = toggle_futures_wallet(address, enabled)
    if success:
        return {"success": True, "address": address, "enabled": enabled}
    else:
        return {"error": "Wallet not found"}


@app.post("/api/futures/reset")
async def api_reset_futures(
    req: ResetFuturesBalanceRequest = ResetFuturesBalanceRequest(),
    user: dict = Depends(require_operator),
):
    """Reset futures paper trading account. Operator only."""
    reset_futures_account(req.balance)
    return {
        "success": True,
        "message": f"Futures account reset to ${req.balance:.2f}",
        "balance": req.balance,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DenseWealth Web Dashboard")
    parser.add_argument("--port", type=int, default=8050, help="Port (default: 8050)")
    parser.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    args = parser.parse_args()

    # Ensure DB tables exist and fund accounts are populated
    from db import init_db, init_paper_account
    from config import TARGET_WALLETS, WALLET_SEED_LABELS
    from approval import init_approval_tables
    init_db()
    init_approval_tables()
    seed_wallets(TARGET_WALLETS, WALLET_SEED_LABELS)
    init_paper_account(PAPER_STARTING_BALANCE)

    # Load portfolio data for wallet display
    refresh_all_portfolios()

    print(f"\n  DenseWealth Dashboard → http://localhost:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
