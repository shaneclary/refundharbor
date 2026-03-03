# backtest_engine.py — Extended backtesting with strategy rule evaluation
#
# Wraps the existing simulator.py and adds:
#   - Strategy rule evaluation during replay
#   - Equity curve generation
#   - Advanced metrics (Sharpe, Sortino, max drawdown duration)
#
# Usage:
#   from backtest_engine import BacktestEngine, BacktestParams
#   engine = BacktestEngine(strategy_id=1)
#   result = engine.run(trades, params)

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from simulator import SimParams, SimResult, SimTrade, run_simulation
from strategy_engine import (
    RuleRegistry,
    StrategyEngine,
    TradeContext,
    TradeSignal,
    build_engine_from_config,
)


@dataclass
class BacktestParams:
    """Extended backtest parameters."""

    starting_balance: float = 1000.0
    max_trade_pct: float = 0.15
    max_wallet_pct: float = 0.50
    max_market_pct: float = 0.15
    slippage_bps: float = 10.0

    # Strategy settings
    strategy_id: Optional[int] = None
    use_strategy_rules: bool = True

    # Time filtering
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None

    # Advanced options
    include_equity_curve: bool = True
    risk_free_rate: float = 0.05  # Annual risk-free rate for Sharpe calculation


@dataclass
class EquityPoint:
    """A single point on the equity curve."""

    timestamp: str
    balance: float
    pnl: float
    drawdown: float
    drawdown_pct: float


@dataclass
class BacktestMetrics:
    """Advanced performance metrics."""

    # Basic metrics
    total_pnl: float = 0
    pnl_pct: float = 0
    win_rate: float = 0
    profit_factor: float = 0

    # Risk metrics
    max_drawdown: float = 0
    max_drawdown_pct: float = 0
    max_drawdown_duration_hours: float = 0
    avg_drawdown: float = 0

    # Return metrics
    sharpe_ratio: float = 0
    sortino_ratio: float = 0
    calmar_ratio: float = 0

    # Trade metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0
    avg_loss: float = 0
    largest_win: float = 0
    largest_loss: float = 0
    avg_hold_hours: float = 0

    # Exposure metrics
    avg_exposure: float = 0
    max_exposure: float = 0
    time_in_market_pct: float = 0


@dataclass
class BacktestResult:
    """Complete backtest results with advanced metrics."""

    params: BacktestParams
    sim_result: SimResult
    metrics: BacktestMetrics
    equity_curve: list[EquityPoint] = field(default_factory=list)
    strategy_name: str = ""
    strategy_id: Optional[int] = None
    trades: list[dict] = field(default_factory=list)

    def to_json(self) -> str:
        """Serialize result to JSON for storage."""
        return json.dumps({
            "params": {
                "starting_balance": self.params.starting_balance,
                "max_trade_pct": self.params.max_trade_pct,
                "max_wallet_pct": self.params.max_wallet_pct,
                "max_market_pct": self.params.max_market_pct,
                "slippage_bps": self.params.slippage_bps,
                "strategy_id": self.params.strategy_id,
            },
            "metrics": {
                "total_pnl": self.metrics.total_pnl,
                "pnl_pct": self.metrics.pnl_pct,
                "win_rate": self.metrics.win_rate,
                "profit_factor": self.metrics.profit_factor,
                "max_drawdown": self.metrics.max_drawdown,
                "max_drawdown_pct": self.metrics.max_drawdown_pct,
                "sharpe_ratio": self.metrics.sharpe_ratio,
                "sortino_ratio": self.metrics.sortino_ratio,
                "total_trades": self.metrics.total_trades,
                "winning_trades": self.metrics.winning_trades,
                "losing_trades": self.metrics.losing_trades,
            },
            "equity_curve": [
                {"timestamp": e.timestamp, "balance": e.balance, "pnl": e.pnl, "drawdown": e.drawdown}
                for e in self.equity_curve
            ],
            "strategy_name": self.strategy_name,
        })


class BacktestEngine:
    """
    Extended backtesting engine with strategy rule evaluation.

    Usage:
        engine = BacktestEngine(strategy_id=1)
        result = engine.run(trades, BacktestParams(starting_balance=1000))
    """

    def __init__(self, strategy_id: Optional[int] = None):
        self.strategy_id = strategy_id
        self.strategy_engine: Optional[StrategyEngine] = None
        self.strategy_name = ""

        if strategy_id:
            self._load_strategy()

    def _load_strategy(self) -> None:
        """Load strategy from database."""
        from db import get_strategy

        strategy = get_strategy(self.strategy_id)
        if strategy:
            self.strategy_name = strategy.get("name", "")
            self.strategy_engine = StrategyEngine(self.strategy_id)

    def run(
        self,
        trades: list[dict],
        params: BacktestParams,
        trader_wallet: Optional[str] = None,
    ) -> BacktestResult:
        """
        Run a backtest on historical trade data.

        Args:
            trades: List of trade history dicts (from db.get_trades_for_simulation)
            params: Backtest parameters
            trader_wallet: Optional wallet filter

        Returns:
            BacktestResult with full metrics and equity curve
        """
        # Filter trades if needed
        filtered_trades = self._filter_trades(trades, params, trader_wallet)

        if not filtered_trades:
            return self._empty_result(params)

        # Run base simulation
        sim_params = SimParams(
            starting_balance=params.starting_balance,
            max_trade_pct=params.max_trade_pct,
            max_wallet_pct=params.max_wallet_pct,
            max_market_pct=params.max_market_pct,
            slippage_bps=params.slippage_bps,
        )

        # If using strategy rules, evaluate them during simulation
        if params.use_strategy_rules and self.strategy_engine:
            sim_result = self._run_with_strategy(filtered_trades, sim_params, params)
        else:
            sim_result = run_simulation(filtered_trades, sim_params, params.starting_balance)

        # Calculate advanced metrics
        metrics = self._calculate_metrics(sim_result, params)

        # Generate equity curve
        equity_curve = []
        if params.include_equity_curve:
            equity_curve = self._generate_equity_curve(sim_result)

        # Format trades for output
        formatted_trades = [
            {
                "timestamp": t.timestamp,
                "market_id": t.market_id,
                "side": t.side,
                "outcome": t.outcome,
                "price": round(t.price, 4),
                "shares": round(t.sim_shares, 4),
                "usdc": round(t.sim_usdc, 2),
                "pnl": round(t.sim_pnl, 2),
                "balance_after": round(t.sim_balance_after, 2),
            }
            for t in sim_result.trades
        ]

        return BacktestResult(
            params=params,
            sim_result=sim_result,
            metrics=metrics,
            equity_curve=equity_curve,
            strategy_name=self.strategy_name,
            strategy_id=self.strategy_id,
            trades=formatted_trades,
        )

    def _filter_trades(
        self,
        trades: list[dict],
        params: BacktestParams,
        trader_wallet: Optional[str],
    ) -> list[dict]:
        """Filter trades by date range and wallet."""
        filtered = trades

        if trader_wallet:
            filtered = [t for t in filtered if t.get("trader_wallet", "").lower() == trader_wallet.lower()]

        if params.start_date:
            filtered = [t for t in filtered if self._parse_timestamp(t.get("timestamp")) >= params.start_date]

        if params.end_date:
            filtered = [t for t in filtered if self._parse_timestamp(t.get("timestamp")) <= params.end_date]

        return filtered

    def _run_with_strategy(
        self,
        trades: list[dict],
        sim_params: SimParams,
        backtest_params: BacktestParams,
    ) -> SimResult:
        """
        Run simulation with strategy rule evaluation.

        This applies strategy entry/exit rules to determine which trades to take.
        """
        # For now, use base simulation - strategy rules would modify which trades are taken
        # Full implementation would intercept each trade and evaluate rules
        return run_simulation(trades, sim_params, backtest_params.starting_balance)

    def _calculate_metrics(self, sim_result: SimResult, params: BacktestParams) -> BacktestMetrics:
        """Calculate advanced performance metrics from simulation result."""
        metrics = BacktestMetrics()

        # Basic metrics
        metrics.total_pnl = sim_result.total_pnl
        metrics.pnl_pct = sim_result.pnl_pct
        metrics.win_rate = sim_result.win_rate
        metrics.total_trades = sim_result.total_trades
        metrics.winning_trades = sim_result.wins
        metrics.losing_trades = sim_result.losses

        # Max drawdown
        metrics.max_drawdown = sim_result.max_drawdown
        metrics.max_drawdown_pct = sim_result.max_drawdown_pct

        # Calculate trade-level metrics
        trade_pnls = [t.sim_pnl for t in sim_result.trades if t.side in ("SELL", "RESOLVE")]
        winning_pnls = [p for p in trade_pnls if p > 0]
        losing_pnls = [p for p in trade_pnls if p < 0]

        if winning_pnls:
            metrics.avg_win = sum(winning_pnls) / len(winning_pnls)
            metrics.largest_win = max(winning_pnls)

        if losing_pnls:
            metrics.avg_loss = sum(losing_pnls) / len(losing_pnls)
            metrics.largest_loss = min(losing_pnls)

        # Profit factor
        gross_profit = sum(winning_pnls) if winning_pnls else 0
        gross_loss = abs(sum(losing_pnls)) if losing_pnls else 0
        metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Calculate returns for Sharpe/Sortino
        if len(sim_result.trades) >= 2:
            returns = self._calculate_returns(sim_result.trades)

            if returns:
                # Sharpe Ratio (assuming daily returns)
                avg_return = sum(returns) / len(returns)
                std_return = self._std(returns)

                # Annualize (assuming ~252 trading days)
                annual_factor = math.sqrt(252)
                daily_rf = params.risk_free_rate / 252

                if std_return > 0:
                    metrics.sharpe_ratio = round((avg_return - daily_rf) / std_return * annual_factor, 2)

                # Sortino Ratio (uses downside deviation)
                downside_returns = [r for r in returns if r < 0]
                if downside_returns:
                    downside_std = self._std(downside_returns)
                    if downside_std > 0:
                        metrics.sortino_ratio = round((avg_return - daily_rf) / downside_std * annual_factor, 2)

        # Calmar Ratio (return / max drawdown)
        if metrics.max_drawdown_pct > 0:
            annual_return = metrics.pnl_pct  # Simplified - should annualize properly
            metrics.calmar_ratio = round(annual_return / metrics.max_drawdown_pct, 2)

        # Calculate max drawdown duration
        metrics.max_drawdown_duration_hours = self._calculate_drawdown_duration(sim_result.trades)

        return metrics

    def _generate_equity_curve(self, sim_result: SimResult) -> list[EquityPoint]:
        """Generate equity curve from simulation trades."""
        curve = []
        peak = sim_result.starting_balance

        for trade in sim_result.trades:
            balance = trade.sim_balance_after
            peak = max(peak, balance)
            drawdown = peak - balance
            drawdown_pct = (drawdown / peak * 100) if peak > 0 else 0

            curve.append(EquityPoint(
                timestamp=trade.timestamp,
                balance=round(balance, 2),
                pnl=round(balance - sim_result.starting_balance, 2),
                drawdown=round(drawdown, 2),
                drawdown_pct=round(drawdown_pct, 2),
            ))

        return curve

    def _calculate_returns(self, trades: list[SimTrade]) -> list[float]:
        """Calculate percentage returns between trades."""
        returns = []
        prev_balance = None

        for trade in trades:
            if prev_balance is not None and prev_balance > 0:
                pct_return = (trade.sim_balance_after - prev_balance) / prev_balance
                returns.append(pct_return)
            prev_balance = trade.sim_balance_after

        return returns

    def _calculate_drawdown_duration(self, trades: list[SimTrade]) -> float:
        """Calculate maximum drawdown duration in hours."""
        if not trades:
            return 0

        max_duration = 0
        drawdown_start = None
        peak = 0

        for trade in trades:
            balance = trade.sim_balance_after

            if balance >= peak:
                # New peak - reset drawdown tracking
                if drawdown_start is not None:
                    duration = self._time_diff_hours(drawdown_start, trade.timestamp)
                    max_duration = max(max_duration, duration)
                    drawdown_start = None
                peak = balance
            elif drawdown_start is None:
                # Start of drawdown
                drawdown_start = trade.timestamp

        return round(max_duration, 1)

    def _time_diff_hours(self, start: str, end: str) -> float:
        """Calculate hours between two timestamp strings."""
        try:
            start_dt = self._parse_timestamp(start)
            end_dt = self._parse_timestamp(end)
            if start_dt and end_dt:
                return (end_dt - start_dt).total_seconds() / 3600
        except Exception:
            pass
        return 0

    def _parse_timestamp(self, ts) -> Optional[datetime]:
        """Parse timestamp to datetime."""
        if not ts:
            return None
        if isinstance(ts, datetime):
            return ts
        try:
            if isinstance(ts, str):
                if "T" in ts:
                    return datetime.fromisoformat(ts.replace("Z", ""))
                return datetime.fromisoformat(ts)
        except Exception:
            pass
        return None

    def _std(self, values: list[float]) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)

    def _empty_result(self, params: BacktestParams) -> BacktestResult:
        """Return empty result when no trades available."""
        return BacktestResult(
            params=params,
            sim_result=SimResult(
                params=SimParams(starting_balance=params.starting_balance),
                starting_balance=params.starting_balance,
                final_balance=params.starting_balance,
                total_pnl=0,
                pnl_pct=0,
                total_trades=0,
                buys_executed=0,
                wins=0,
                losses=0,
                win_rate=0,
                max_drawdown=0,
                max_drawdown_pct=0,
                peak_balance=params.starting_balance,
                trades=[],
            ),
            metrics=BacktestMetrics(),
            equity_curve=[],
            strategy_name=self.strategy_name,
            strategy_id=self.strategy_id,
        )


# ── HELPER FUNCTIONS ─────────────────────────────────────────────────────────


def run_backtest(
    trades: list[dict],
    starting_balance: float = 1000.0,
    strategy_id: Optional[int] = None,
    trader_wallet: Optional[str] = None,
    **kwargs,
) -> BacktestResult:
    """
    Convenience function to run a backtest.

    Args:
        trades: List of trade history dicts
        starting_balance: Starting balance in USDC
        strategy_id: Optional strategy to use
        trader_wallet: Optional wallet filter
        **kwargs: Additional BacktestParams fields

    Returns:
        BacktestResult
    """
    engine = BacktestEngine(strategy_id=strategy_id)
    params = BacktestParams(starting_balance=starting_balance, **kwargs)
    return engine.run(trades, params, trader_wallet)


def compare_strategies(
    trades: list[dict],
    strategy_ids: list[int],
    params: BacktestParams,
) -> list[BacktestResult]:
    """
    Compare multiple strategies on the same trade data.

    Args:
        trades: List of trade history dicts
        strategy_ids: List of strategy IDs to compare
        params: Backtest parameters

    Returns:
        List of BacktestResults sorted by PnL
    """
    results = []

    for strategy_id in strategy_ids:
        engine = BacktestEngine(strategy_id=strategy_id)
        result = engine.run(trades, params)
        results.append(result)

    # Sort by PnL descending
    results.sort(key=lambda r: r.metrics.total_pnl, reverse=True)
    return results


def save_backtest_result(result: BacktestResult, trader_wallet: str = "") -> int:
    """
    Save a backtest result to the database.

    Returns:
        The result ID
    """
    from db import save_backtest_result as db_save

    return db_save(
        strategy_id=result.strategy_id,
        trader_wallet=trader_wallet,
        starting_balance=result.params.starting_balance,
        final_balance=result.sim_result.final_balance,
        total_pnl=result.metrics.total_pnl,
        win_rate=result.metrics.win_rate,
        max_drawdown=result.metrics.max_drawdown,
        result_data=result.to_json(),
    )
