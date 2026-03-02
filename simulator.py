# simulator.py — strategy simulation engine
#
# Replays historical trades with different parameters to evaluate
# alternative sizing strategies. Pure computation — no DB writes.
#
# Usage:
#   from simulator import SimParams, run_simulation, optimize
#   result = run_simulation(trades, SimParams(max_trade_pct=0.08))
#   opt = optimize(trades)

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class SimParams:
    """Tunable parameters for a simulation run."""

    starting_balance: float = 1000.0
    max_trade_pct: float = 0.05
    max_wallet_pct: float = 0.30
    max_market_pct: float = 0.10
    slippage_bps: float = 10.0
    copy_strategy: str = "tiered_fixed"  # "tiered_fixed" | "proportional" | "fixed"
    copy_amount_usdc: float = 10.0
    min_copy_usdc: float = 0.50
    copy_amount_tiers: list = None  # [(min_balance, amount), ...] for tiered_fixed


@dataclass
class SimTrade:
    """One simulated trade in the replay."""

    timestamp: str
    market_id: str
    side: str
    outcome: str
    original_usdc: float
    sim_usdc: float
    original_shares: float
    sim_shares: float
    price: float
    sim_balance_after: float
    sim_pnl: float  # realized P&L for SELL/RESOLVE


@dataclass
class SimResult:
    """Complete results of a simulation run."""

    params: SimParams
    starting_balance: float
    final_balance: float
    total_pnl: float
    pnl_pct: float
    total_trades: int
    buys_executed: int
    wins: int
    losses: int
    win_rate: float
    max_drawdown: float
    max_drawdown_pct: float
    peak_balance: float
    trades: list[SimTrade] = field(default_factory=list)


@dataclass
class OptimizeResult:
    """Results from the grid search optimizer."""

    best: SimResult
    all_results: list[dict]
    total_combos: int


DEFAULT_TIERS = [
    (5000, 100),
    (2000, 75),
    (1000, 50),
    (500, 25),
    (250, 15),
    (0, 10),
]


def _get_tiered_amount(balance: float, params: SimParams) -> float:
    """Look up the fixed trade amount for the current balance tier."""
    tiers = params.copy_amount_tiers or DEFAULT_TIERS
    for threshold, amount in tiers:
        if balance >= threshold:
            return amount
    return params.copy_amount_usdc


# ── SIMULATION ENGINE ─────────────────────────────────────────────────────


def run_simulation(
    trades: list[dict],
    params: SimParams,
    actual_starting_balance: float = 1000.0,
) -> SimResult:
    """
    Replay a list of trade_history rows with the given parameters.

    Args:
        trades: trade_history dicts ordered by timestamp ASC
        params: simulation parameters
        actual_starting_balance: the real starting balance (for computing
            original trade fractions in proportional mode)
    """
    sim_balance = params.starting_balance
    actual_balance = actual_starting_balance

    # Simulated positions: {(market_id, trader_wallet): {shares, usdc_spent}}
    sim_positions: dict[tuple, dict] = {}
    # Actual positions (reconstructed)
    actual_positions: dict[tuple, dict] = {}

    # Exposure tracking
    sim_wallet_exposure: dict[str, float] = defaultdict(float)
    sim_market_exposure: dict[str, float] = defaultdict(float)

    peak_balance = sim_balance
    max_drawdown = 0.0
    sim_trades: list[SimTrade] = []
    wins = 0
    losses = 0
    buys_executed = 0

    for trade in trades:
        side = trade["side"]
        market_id = trade["market_id"]
        wallet = trade["trader_wallet"]
        price = trade["price"] or 0
        actual_shares = trade["shares"]
        actual_usdc = trade["usdc_amount"]
        outcome = trade.get("outcome", "")
        timestamp = trade.get("timestamp", "")

        key = (market_id, wallet)

        if side == "BUY":
            _sim_buy(
                params, sim_balance, actual_balance, actual_usdc, actual_shares,
                price, market_id, wallet, outcome, timestamp, key,
                sim_positions, actual_positions,
                sim_wallet_exposure, sim_market_exposure, sim_trades,
            )
            st = sim_trades[-1]
            sim_balance = st.sim_balance_after
            if st.sim_usdc > 0:
                buys_executed += 1

            # Reconstruct actual balance
            actual_balance -= actual_usdc
            apos = actual_positions.get(key, {"shares": 0, "usdc_spent": 0})
            apos["shares"] += actual_shares
            apos["usdc_spent"] += actual_usdc
            actual_positions[key] = apos

        elif side == "SELL":
            _sim_sell(
                params, sim_balance, actual_shares, price,
                market_id, wallet, outcome, timestamp, key,
                sim_positions, actual_positions,
                sim_wallet_exposure, sim_market_exposure, sim_trades,
            )
            st = sim_trades[-1]
            sim_balance = st.sim_balance_after

            # Reconstruct actual balance
            actual_balance += actual_usdc
            apos = actual_positions.get(key)
            if apos and apos["shares"] > 0:
                sell_frac = min(actual_shares / apos["shares"], 1.0)
                apos["shares"] -= actual_shares
                apos["usdc_spent"] *= (1 - sell_frac)
                if apos["shares"] <= 0.0001:
                    actual_positions.pop(key, None)

        elif side == "RESOLVE":
            payout_per_share = price
            sim_pos = sim_positions.get(key)

            sim_payout = 0.0
            sim_pnl = 0.0
            sim_shares_resolved = 0.0

            if sim_pos and sim_pos["shares"] > 0:
                sim_shares_resolved = sim_pos["shares"]
                sim_payout = sim_shares_resolved * payout_per_share
                sim_pnl = sim_payout - sim_pos["usdc_spent"]

                sim_balance += sim_payout
                sim_wallet_exposure[wallet] -= sim_pos["usdc_spent"]
                sim_market_exposure[market_id] -= sim_pos["usdc_spent"]
                del sim_positions[key]

                if payout_per_share > 0:
                    wins += 1
                else:
                    losses += 1

            sim_trades.append(SimTrade(
                timestamp=str(timestamp),
                market_id=market_id,
                side="RESOLVE",
                outcome=outcome,
                original_usdc=actual_usdc,
                sim_usdc=sim_payout,
                original_shares=actual_shares,
                sim_shares=sim_shares_resolved,
                price=payout_per_share,
                sim_balance_after=sim_balance,
                sim_pnl=sim_pnl,
            ))

            # Reconstruct actual balance
            actual_balance += actual_shares * payout_per_share
            actual_positions.pop(key, None)

        # Track drawdown
        peak_balance = max(peak_balance, sim_balance)
        drawdown = peak_balance - sim_balance
        max_drawdown = max(max_drawdown, drawdown)

    total_pnl = sim_balance - params.starting_balance
    resolved = wins + losses

    return SimResult(
        params=params,
        starting_balance=params.starting_balance,
        final_balance=sim_balance,
        total_pnl=total_pnl,
        pnl_pct=(total_pnl / params.starting_balance * 100)
        if params.starting_balance > 0
        else 0,
        total_trades=len(sim_trades),
        buys_executed=buys_executed,
        wins=wins,
        losses=losses,
        win_rate=(wins / resolved * 100) if resolved > 0 else 0,
        max_drawdown=max_drawdown,
        max_drawdown_pct=(max_drawdown / peak_balance * 100)
        if peak_balance > 0
        else 0,
        peak_balance=peak_balance,
        trades=sim_trades,
    )


def _sim_buy(
    params, sim_balance, actual_balance, actual_usdc, actual_shares,
    price, market_id, wallet, outcome, timestamp, key,
    sim_positions, actual_positions,
    sim_wallet_exposure, sim_market_exposure, sim_trades,
):
    """Simulate a BUY trade."""
    # Compute original trade fraction
    if actual_balance > 0:
        original_trade_frac = actual_usdc / actual_balance
    else:
        original_trade_frac = 0.05  # fallback

    # Determine simulation amount
    if params.copy_strategy == "tiered_fixed":
        sim_raw = _get_tiered_amount(sim_balance, params)
    elif params.copy_strategy == "proportional":
        sim_raw = original_trade_frac * sim_balance
    else:
        sim_raw = params.copy_amount_usdc

    # Apply caps
    cap_trade = params.max_trade_pct * sim_balance
    cap_wallet = params.max_wallet_pct * sim_balance - sim_wallet_exposure[wallet]
    cap_market = params.max_market_pct * sim_balance - sim_market_exposure[market_id]

    sim_amount = max(0, min(sim_raw, cap_trade, cap_wallet, cap_market))

    if sim_amount < params.min_copy_usdc:
        sim_amount = 0

    # Cannot exceed available balance
    sim_amount = min(sim_amount, sim_balance)

    # Apply slippage and calculate shares
    slippage_factor = 1 + (params.slippage_bps / 10000)
    fill_price = price * slippage_factor if price > 0 else 0
    sim_shares = sim_amount / fill_price if fill_price > 0 else 0

    # Update simulated state
    if sim_amount > 0:
        pos = sim_positions.get(key, {"shares": 0, "usdc_spent": 0})
        pos["shares"] += sim_shares
        pos["usdc_spent"] += sim_amount
        sim_positions[key] = pos
        new_balance = sim_balance - sim_amount
        sim_wallet_exposure[wallet] += sim_amount
        sim_market_exposure[market_id] += sim_amount
    else:
        new_balance = sim_balance

    sim_trades.append(SimTrade(
        timestamp=str(timestamp),
        market_id=market_id,
        side="BUY",
        outcome=outcome,
        original_usdc=actual_usdc,
        sim_usdc=sim_amount,
        original_shares=actual_shares,
        sim_shares=sim_shares,
        price=price,
        sim_balance_after=new_balance,
        sim_pnl=0,
    ))


def _sim_sell(
    params, sim_balance, actual_shares, price,
    market_id, wallet, outcome, timestamp, key,
    sim_positions, actual_positions,
    sim_wallet_exposure, sim_market_exposure, sim_trades,
):
    """Simulate a SELL trade."""
    sim_pos = sim_positions.get(key)
    sim_received = 0.0
    sim_pnl = 0.0
    sim_shares_sold = 0.0

    if sim_pos and sim_pos["shares"] > 0:
        # Determine sell fraction from actual positions
        actual_pos = actual_positions.get(key)
        if actual_pos and actual_pos["shares"] > 0:
            sell_frac = min(actual_shares / actual_pos["shares"], 1.0)
        else:
            sell_frac = 1.0

        sim_shares_sold = sim_pos["shares"] * sell_frac

        slippage_factor = 1 - (params.slippage_bps / 10000)
        fill_price = price * slippage_factor
        sim_received = sim_shares_sold * fill_price

        avg_cost = sim_pos["usdc_spent"] / sim_pos["shares"] if sim_pos["shares"] > 0 else 0
        sim_pnl = (fill_price - avg_cost) * sim_shares_sold

        # Update position
        cost_removed = sim_pos["usdc_spent"] * sell_frac
        sim_pos["shares"] -= sim_shares_sold
        sim_pos["usdc_spent"] -= cost_removed
        sim_wallet_exposure[wallet] -= cost_removed
        sim_market_exposure[market_id] -= cost_removed

        if sim_pos["shares"] <= 0.0001:
            sim_positions.pop(key, None)

    new_balance = sim_balance + sim_received

    sim_trades.append(SimTrade(
        timestamp=str(timestamp),
        market_id=market_id,
        side="SELL",
        outcome=outcome,
        original_usdc=actual_shares * price if price else 0,
        sim_usdc=sim_received,
        original_shares=actual_shares,
        sim_shares=sim_shares_sold,
        price=price,
        sim_balance_after=new_balance,
        sim_pnl=sim_pnl,
    ))


# ── OPTIMIZER (GRID SEARCH) ──────────────────────────────────────────────


def optimize(
    trades: list[dict],
    actual_starting_balance: float = 1000.0,
    top_n: int = 20,
) -> OptimizeResult:
    """
    Grid search over parameter space to find the best combination.
    Returns best result + top N ranked by P&L.
    """
    grid_trade = [0.03, 0.05, 0.08, 0.10, 0.15]
    grid_wallet = [0.20, 0.30, 0.40, 0.50, 0.60]
    grid_market = [0.05, 0.10, 0.15, 0.20, 0.25]
    grid_slippage = [5, 10, 20]

    results: list[dict] = []
    best_result: SimResult | None = None
    best_pnl = float("-inf")

    # Tiered fixed strategy (uses default tiers — only caps vary)
    for mtp in grid_trade:
        for mwp in grid_wallet:
            for mmp in grid_market:
                for slip in grid_slippage:
                    params = SimParams(
                        starting_balance=actual_starting_balance,
                        max_trade_pct=mtp,
                        max_wallet_pct=mwp,
                        max_market_pct=mmp,
                        slippage_bps=slip,
                        copy_strategy="tiered_fixed",
                    )
                    result = run_simulation(trades, params, actual_starting_balance)
                    summary = _result_summary(result, "tiered_fixed")
                    results.append(summary)

                    if result.total_pnl > best_pnl:
                        best_pnl = result.total_pnl
                        best_result = result

    # Proportional strategy grid
    for mtp in grid_trade:
        for mwp in grid_wallet:
            for mmp in grid_market:
                for slip in grid_slippage:
                    params = SimParams(
                        starting_balance=actual_starting_balance,
                        max_trade_pct=mtp,
                        max_wallet_pct=mwp,
                        max_market_pct=mmp,
                        slippage_bps=slip,
                        copy_strategy="proportional",
                    )
                    result = run_simulation(trades, params, actual_starting_balance)
                    summary = _result_summary(result, "proportional")
                    results.append(summary)

                    if result.total_pnl > best_pnl:
                        best_pnl = result.total_pnl
                        best_result = result

    # Fixed strategy grid
    grid_fixed_amt = [5, 10, 20, 50]
    for mtp in grid_trade:
        for mwp in grid_wallet:
            for mmp in grid_market:
                for slip in grid_slippage:
                    for amt in grid_fixed_amt:
                        params = SimParams(
                            starting_balance=actual_starting_balance,
                            max_trade_pct=mtp,
                            max_wallet_pct=mwp,
                            max_market_pct=mmp,
                            slippage_bps=slip,
                            copy_strategy="fixed",
                            copy_amount_usdc=amt,
                        )
                        result = run_simulation(trades, params, actual_starting_balance)
                        summary = _result_summary(result, "fixed")
                        results.append(summary)

                        if result.total_pnl > best_pnl:
                            best_pnl = result.total_pnl
                            best_result = result

    # Sort by P&L descending
    results.sort(key=lambda r: r["total_pnl"], reverse=True)

    return OptimizeResult(
        best=best_result,
        all_results=results[:top_n],
        total_combos=len(results),
    )


def _result_summary(result: SimResult, strategy: str) -> dict:
    return {
        "strategy": strategy,
        "max_trade_pct": result.params.max_trade_pct,
        "max_wallet_pct": result.params.max_wallet_pct,
        "max_market_pct": result.params.max_market_pct,
        "slippage_bps": result.params.slippage_bps,
        "copy_amount_usdc": result.params.copy_amount_usdc if strategy == "fixed" else None,
        "final_balance": round(result.final_balance, 2),
        "total_pnl": round(result.total_pnl, 2),
        "pnl_pct": round(result.pnl_pct, 2),
        "win_rate": round(result.win_rate, 1),
        "max_drawdown_pct": round(result.max_drawdown_pct, 1),
        "buys_executed": result.buys_executed,
        "wins": result.wins,
        "losses": result.losses,
    }
