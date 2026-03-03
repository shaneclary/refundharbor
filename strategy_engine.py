# strategy_engine.py — Configurable rule evaluation engine
#
# Provides a modular rule system for defining trading strategies.
# Rules are stored as JSON config in the database and evaluated at runtime.
#
# Rule Types:
#   - entry: Determines when to enter positions
#   - exit: Determines when to exit positions
#   - sizing: Determines position sizes
#   - risk: Determines risk constraints
#
# Usage:
#   from strategy_engine import StrategyEngine, RuleRegistry
#   engine = StrategyEngine(strategy_id=1)
#   signal = engine.evaluate_entry(trade_data, context)

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)


# ── RULE DEFINITIONS ─────────────────────────────────────────────────────────


@dataclass
class RuleResult:
    """Result of evaluating a rule."""
    passed: bool
    value: Any = None  # For sizing rules, the computed amount
    reason: str = ""


@dataclass
class TradeContext:
    """Context data available to rules during evaluation."""
    balance: float = 0
    wallet_exposure: float = 0
    market_exposure: float = 0
    daily_pnl: float = 0
    position_value: float = 0
    entry_price: float = 0
    current_price: float = 0
    peak_price: float = 0
    hold_hours: float = 0
    trader_win_rate: float = 0
    trader_pnl: float = 0


@dataclass
class TradeSignal:
    """A trade signal from a tracked trader."""
    wallet: str
    market_id: str
    side: str  # 'BUY' | 'SELL'
    price: float
    shares: float
    usdc_amount: float
    timestamp: datetime = field(default_factory=datetime.now)


class Rule(ABC):
    """Base class for all rules."""

    name: str = ""
    rule_type: str = ""  # 'entry' | 'exit' | 'sizing' | 'risk'
    description: str = ""
    config_schema: dict = {}  # JSON schema for config validation

    @abstractmethod
    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        """Evaluate the rule against the given signal and context."""
        pass


# ── ENTRY RULES ──────────────────────────────────────────────────────────────


class CopyTraderSignalRule(Rule):
    """Copy when a tracked trader buys."""

    name = "copy_trader_signal"
    rule_type = "entry"
    description = "Enter position when a tracked trader buys"
    config_schema = {
        "min_usdc": {"type": "number", "default": 1.0, "description": "Minimum trade size to copy"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        min_usdc = config.get("min_usdc", 1.0)
        if signal.side == "BUY" and signal.usdc_amount >= min_usdc:
            return RuleResult(passed=True, reason=f"Trader bought ${signal.usdc_amount:.2f}")
        return RuleResult(passed=False, reason="Not a qualifying buy signal")


class PriceDropRule(Rule):
    """Enter when price drops X% from recent high."""

    name = "price_drop"
    rule_type = "entry"
    description = "Enter when price drops a percentage from recent high"
    config_schema = {
        "drop_pct": {"type": "number", "default": 5.0, "description": "Required % drop from high"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        drop_pct = config.get("drop_pct", 5.0)
        if context.peak_price > 0:
            actual_drop = (context.peak_price - signal.price) / context.peak_price * 100
            if actual_drop >= drop_pct:
                return RuleResult(passed=True, reason=f"Price dropped {actual_drop:.1f}%")
        return RuleResult(passed=False, reason="Price drop threshold not met")


class PriceThresholdRule(Rule):
    """Enter when price is below a threshold."""

    name = "price_threshold"
    rule_type = "entry"
    description = "Enter when price is below a threshold"
    config_schema = {
        "max_price": {"type": "number", "default": 0.50, "description": "Maximum price to enter"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        max_price = config.get("max_price", 0.50)
        if signal.price <= max_price:
            return RuleResult(passed=True, reason=f"Price {signal.price:.4f} <= {max_price:.4f}")
        return RuleResult(passed=False, reason=f"Price {signal.price:.4f} > {max_price:.4f}")


class TraderWinRateRule(Rule):
    """Only copy traders with a minimum win rate."""

    name = "trader_win_rate"
    rule_type = "entry"
    description = "Only copy traders with minimum win rate"
    config_schema = {
        "min_win_rate": {"type": "number", "default": 55.0, "description": "Minimum win rate %"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        min_rate = config.get("min_win_rate", 55.0)
        if context.trader_win_rate >= min_rate:
            return RuleResult(passed=True, reason=f"Trader win rate {context.trader_win_rate:.1f}%")
        return RuleResult(passed=False, reason=f"Trader win rate {context.trader_win_rate:.1f}% < {min_rate}%")


# ── EXIT RULES ───────────────────────────────────────────────────────────────


class CopyTraderExitRule(Rule):
    """Exit when the tracked trader sells."""

    name = "copy_trader_exit"
    rule_type = "exit"
    description = "Exit position when the tracked trader sells"
    config_schema = {}

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        if signal.side == "SELL":
            return RuleResult(passed=True, reason="Trader exited position")
        return RuleResult(passed=False, reason="Trader has not exited")


class ProfitTargetRule(Rule):
    """Exit at X% profit."""

    name = "profit_target"
    rule_type = "exit"
    description = "Exit position at target profit percentage"
    config_schema = {
        "target_pct": {"type": "number", "default": 20.0, "description": "Target profit %"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        target_pct = config.get("target_pct", 20.0)
        if context.entry_price > 0:
            profit_pct = (context.current_price - context.entry_price) / context.entry_price * 100
            if profit_pct >= target_pct:
                return RuleResult(passed=True, reason=f"Profit target reached: {profit_pct:.1f}%")
        return RuleResult(passed=False, reason="Profit target not reached")


class StopLossRule(Rule):
    """Exit at X% loss."""

    name = "stop_loss"
    rule_type = "exit"
    description = "Exit position at stop loss percentage"
    config_schema = {
        "stop_pct": {"type": "number", "default": 10.0, "description": "Stop loss %"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        stop_pct = config.get("stop_pct", 10.0)
        if context.entry_price > 0:
            loss_pct = (context.entry_price - context.current_price) / context.entry_price * 100
            if loss_pct >= stop_pct:
                return RuleResult(passed=True, reason=f"Stop loss triggered: {loss_pct:.1f}%")
        return RuleResult(passed=False, reason="Stop loss not triggered")


class TimeExitRule(Rule):
    """Exit after X hours."""

    name = "time_exit"
    rule_type = "exit"
    description = "Exit position after holding for specified hours"
    config_schema = {
        "max_hours": {"type": "number", "default": 48.0, "description": "Maximum hold time in hours"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        max_hours = config.get("max_hours", 48.0)
        if context.hold_hours >= max_hours:
            return RuleResult(passed=True, reason=f"Time limit reached: {context.hold_hours:.1f}h")
        return RuleResult(passed=False, reason=f"Hold time: {context.hold_hours:.1f}h < {max_hours}h")


class TrailingStopRule(Rule):
    """Trailing stop X% below peak."""

    name = "trailing_stop"
    rule_type = "exit"
    description = "Exit when price drops X% from peak since entry"
    config_schema = {
        "trail_pct": {"type": "number", "default": 10.0, "description": "Trailing stop %"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        trail_pct = config.get("trail_pct", 10.0)
        if context.peak_price > 0:
            drop_from_peak = (context.peak_price - context.current_price) / context.peak_price * 100
            if drop_from_peak >= trail_pct:
                return RuleResult(passed=True, reason=f"Trailing stop triggered: {drop_from_peak:.1f}% from peak")
        return RuleResult(passed=False, reason="Trailing stop not triggered")


# ── SIZING RULES ─────────────────────────────────────────────────────────────


class FixedAmountRule(Rule):
    """Fixed USDC per trade."""

    name = "fixed_amount"
    rule_type = "sizing"
    description = "Use fixed USDC amount per trade"
    config_schema = {
        "amount": {"type": "number", "default": 10.0, "description": "Fixed amount in USDC"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        amount = config.get("amount", 10.0)
        return RuleResult(passed=True, value=amount, reason=f"Fixed amount: ${amount:.2f}")


class FixedPercentRule(Rule):
    """Fixed % of balance per trade."""

    name = "fixed_pct"
    rule_type = "sizing"
    description = "Use fixed percentage of balance per trade"
    config_schema = {
        "pct": {"type": "number", "default": 5.0, "description": "Percentage of balance"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        pct = config.get("pct", 5.0) / 100
        amount = context.balance * pct
        return RuleResult(passed=True, value=amount, reason=f"{pct*100:.1f}% of balance: ${amount:.2f}")


class TieredFixedRule(Rule):
    """Tiered fixed amounts based on balance."""

    name = "tiered_fixed"
    rule_type = "sizing"
    description = "Use tiered fixed amounts based on balance level"
    config_schema = {
        "tiers": {
            "type": "array",
            "default": [
                [5000, 100], [2000, 75], [1000, 50], [500, 25], [250, 15], [0, 10]
            ],
            "description": "List of [min_balance, amount] pairs",
        },
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        tiers = config.get("tiers", [[0, 10]])
        # Sort by threshold descending
        sorted_tiers = sorted(tiers, key=lambda x: x[0], reverse=True)
        for threshold, amount in sorted_tiers:
            if context.balance >= threshold:
                return RuleResult(passed=True, value=amount, reason=f"Tier (>=${threshold}): ${amount:.2f}")
        # Fallback to smallest tier
        return RuleResult(passed=True, value=sorted_tiers[-1][1], reason=f"Default tier: ${sorted_tiers[-1][1]:.2f}")


class KellyCriterionRule(Rule):
    """Kelly criterion-based sizing."""

    name = "kelly_criterion"
    rule_type = "sizing"
    description = "Use Kelly criterion for optimal position sizing"
    config_schema = {
        "fraction": {"type": "number", "default": 0.25, "description": "Fraction of Kelly to use (0.25 = quarter Kelly)"},
        "max_pct": {"type": "number", "default": 10.0, "description": "Maximum % of balance"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        fraction = config.get("fraction", 0.25)
        max_pct = config.get("max_pct", 10.0) / 100

        # Kelly formula: f* = (bp - q) / b
        # where b = odds, p = win probability, q = 1 - p
        # For binary markets: b = (1/price - 1) for YES tokens
        if signal.price > 0 and signal.price < 1:
            b = (1 / signal.price) - 1  # Potential profit multiplier
            p = context.trader_win_rate / 100 if context.trader_win_rate > 0 else 0.5
            q = 1 - p

            kelly = (b * p - q) / b if b > 0 else 0
            kelly = max(0, kelly * fraction)  # Apply fraction, no negative sizing

            amount = min(context.balance * kelly, context.balance * max_pct)
            return RuleResult(passed=True, value=amount, reason=f"Kelly ({fraction}x): ${amount:.2f}")

        return RuleResult(passed=True, value=context.balance * 0.01, reason="Default 1% (Kelly undefined)")


# ── RISK RULES ───────────────────────────────────────────────────────────────


class MaxPositionPctRule(Rule):
    """Max % in single position."""

    name = "max_position_pct"
    rule_type = "risk"
    description = "Maximum percentage of balance in a single position"
    config_schema = {
        "max_pct": {"type": "number", "default": 15.0, "description": "Maximum position %"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        max_pct = config.get("max_pct", 15.0) / 100
        position_pct = context.position_value / context.balance if context.balance > 0 else 0
        if position_pct <= max_pct:
            return RuleResult(passed=True, reason=f"Position {position_pct*100:.1f}% <= {max_pct*100:.1f}%")
        return RuleResult(passed=False, reason=f"Position {position_pct*100:.1f}% > {max_pct*100:.1f}%")


class MaxWalletExposureRule(Rule):
    """Max % exposure per tracked wallet."""

    name = "max_wallet_exposure"
    rule_type = "risk"
    description = "Maximum exposure to a single tracked wallet"
    config_schema = {
        "max_pct": {"type": "number", "default": 50.0, "description": "Maximum wallet exposure %"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        max_pct = config.get("max_pct", 50.0) / 100
        exposure_pct = context.wallet_exposure / context.balance if context.balance > 0 else 0
        if exposure_pct <= max_pct:
            return RuleResult(passed=True, reason=f"Wallet exposure {exposure_pct*100:.1f}% <= {max_pct*100:.1f}%")
        return RuleResult(passed=False, reason=f"Wallet exposure {exposure_pct*100:.1f}% > {max_pct*100:.1f}%")


class MaxMarketExposureRule(Rule):
    """Max % exposure per market."""

    name = "max_market_exposure"
    rule_type = "risk"
    description = "Maximum exposure to a single market"
    config_schema = {
        "max_pct": {"type": "number", "default": 15.0, "description": "Maximum market exposure %"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        max_pct = config.get("max_pct", 15.0) / 100
        exposure_pct = context.market_exposure / context.balance if context.balance > 0 else 0
        if exposure_pct <= max_pct:
            return RuleResult(passed=True, reason=f"Market exposure {exposure_pct*100:.1f}% <= {max_pct*100:.1f}%")
        return RuleResult(passed=False, reason=f"Market exposure {exposure_pct*100:.1f}% > {max_pct*100:.1f}%")


class DailyLossLimitRule(Rule):
    """Stop trading after X% daily loss."""

    name = "daily_loss_limit"
    rule_type = "risk"
    description = "Stop trading after reaching daily loss limit"
    config_schema = {
        "max_loss_pct": {"type": "number", "default": 5.0, "description": "Maximum daily loss %"},
    }

    def evaluate(self, signal: TradeSignal, context: TradeContext, config: dict) -> RuleResult:
        max_loss_pct = config.get("max_loss_pct", 5.0) / 100
        loss_pct = abs(context.daily_pnl / context.balance) if context.balance > 0 and context.daily_pnl < 0 else 0
        if loss_pct <= max_loss_pct:
            return RuleResult(passed=True, reason=f"Daily loss {loss_pct*100:.1f}% <= {max_loss_pct*100:.1f}%")
        return RuleResult(passed=False, reason=f"Daily loss limit reached: {loss_pct*100:.1f}%")


# ── RULE REGISTRY ────────────────────────────────────────────────────────────


class RuleRegistry:
    """Registry of available rule types."""

    _rules: dict[str, type[Rule]] = {}

    @classmethod
    def register(cls, rule_class: type[Rule]) -> None:
        """Register a rule class."""
        cls._rules[rule_class.name] = rule_class

    @classmethod
    def get(cls, name: str) -> Optional[type[Rule]]:
        """Get a rule class by name."""
        return cls._rules.get(name)

    @classmethod
    def all(cls) -> dict[str, type[Rule]]:
        """Get all registered rules."""
        return cls._rules.copy()

    @classmethod
    def by_type(cls, rule_type: str) -> list[type[Rule]]:
        """Get all rules of a specific type."""
        return [r for r in cls._rules.values() if r.rule_type == rule_type]

    @classmethod
    def get_templates(cls) -> list[dict]:
        """Get all rule templates for the UI."""
        templates = []
        for name, rule_class in cls._rules.items():
            templates.append({
                "name": name,
                "type": rule_class.rule_type,
                "description": rule_class.description,
                "config_schema": rule_class.config_schema,
            })
        return templates


# Register all rules
for rule_class in [
    # Entry rules
    CopyTraderSignalRule,
    PriceDropRule,
    PriceThresholdRule,
    TraderWinRateRule,
    # Exit rules
    CopyTraderExitRule,
    ProfitTargetRule,
    StopLossRule,
    TimeExitRule,
    TrailingStopRule,
    # Sizing rules
    FixedAmountRule,
    FixedPercentRule,
    TieredFixedRule,
    KellyCriterionRule,
    # Risk rules
    MaxPositionPctRule,
    MaxWalletExposureRule,
    MaxMarketExposureRule,
    DailyLossLimitRule,
]:
    RuleRegistry.register(rule_class)


# ── STRATEGY ENGINE ──────────────────────────────────────────────────────────


class StrategyEngine:
    """
    Evaluates trading strategies composed of multiple rules.

    Usage:
        engine = StrategyEngine(strategy_id=1)
        result = engine.evaluate_entry(signal, context)
        if result.should_enter:
            size = engine.get_position_size(signal, context)
    """

    def __init__(self, strategy_id: Optional[int] = None):
        self.strategy_id = strategy_id
        self.rules: dict[str, list[tuple[Rule, dict]]] = {
            "entry": [],
            "exit": [],
            "sizing": [],
            "risk": [],
        }
        if strategy_id:
            self._load_rules()

    def _load_rules(self) -> None:
        """Load rules from database for this strategy."""
        from db import get_strategy_rules

        db_rules = get_strategy_rules(self.strategy_id)
        for rule_data in db_rules:
            if not rule_data.get("enabled", True):
                continue

            rule_name = rule_data["rule_name"]
            rule_type = rule_data["rule_type"]
            config = json.loads(rule_data["rule_config"]) if rule_data["rule_config"] else {}

            rule_class = RuleRegistry.get(rule_name)
            if rule_class:
                rule_instance = rule_class()
                self.rules[rule_type].append((rule_instance, config))
            else:
                log.warning(f"Unknown rule: {rule_name}")

    def add_rule(self, rule_name: str, config: dict = None) -> bool:
        """Add a rule to the engine (in-memory, not persisted)."""
        rule_class = RuleRegistry.get(rule_name)
        if not rule_class:
            return False

        rule_instance = rule_class()
        self.rules[rule_instance.rule_type].append((rule_instance, config or {}))
        return True

    def evaluate_entry(self, signal: TradeSignal, context: TradeContext) -> RuleResult:
        """
        Evaluate all entry rules. Returns passed=True only if ALL entry rules pass.
        """
        if not self.rules["entry"]:
            # No entry rules defined - default to copy trader signal
            return RuleResult(passed=True, reason="No entry rules defined")

        for rule, config in self.rules["entry"]:
            result = rule.evaluate(signal, context, config)
            if not result.passed:
                return RuleResult(passed=False, reason=f"Entry blocked by {rule.name}: {result.reason}")

        return RuleResult(passed=True, reason="All entry rules passed")

    def evaluate_exit(self, signal: TradeSignal, context: TradeContext) -> RuleResult:
        """
        Evaluate all exit rules. Returns passed=True if ANY exit rule passes.
        """
        for rule, config in self.rules["exit"]:
            result = rule.evaluate(signal, context, config)
            if result.passed:
                return RuleResult(passed=True, reason=f"Exit triggered by {rule.name}: {result.reason}")

        return RuleResult(passed=False, reason="No exit conditions met")

    def evaluate_risk(self, signal: TradeSignal, context: TradeContext) -> RuleResult:
        """
        Evaluate all risk rules. Returns passed=True only if ALL risk rules pass.
        """
        for rule, config in self.rules["risk"]:
            result = rule.evaluate(signal, context, config)
            if not result.passed:
                return RuleResult(passed=False, reason=f"Risk limit: {rule.name}: {result.reason}")

        return RuleResult(passed=True, reason="All risk checks passed")

    def get_position_size(self, signal: TradeSignal, context: TradeContext) -> float:
        """
        Get position size from sizing rules.
        Uses the first sizing rule's result, or returns 0 if no sizing rules.
        """
        if not self.rules["sizing"]:
            # Default to 5% of balance
            return context.balance * 0.05

        rule, config = self.rules["sizing"][0]
        result = rule.evaluate(signal, context, config)
        return result.value if result.value else 0

    def should_enter(self, signal: TradeSignal, context: TradeContext) -> tuple[bool, float, str]:
        """
        Full entry evaluation: entry rules + risk rules + sizing.
        Returns (should_enter, position_size, reason).
        """
        # Check entry rules
        entry_result = self.evaluate_entry(signal, context)
        if not entry_result.passed:
            return False, 0, entry_result.reason

        # Check risk rules
        risk_result = self.evaluate_risk(signal, context)
        if not risk_result.passed:
            return False, 0, risk_result.reason

        # Get position size
        size = self.get_position_size(signal, context)
        if size <= 0:
            return False, 0, "Position size is zero"

        return True, size, "Entry approved"

    def should_exit(self, signal: TradeSignal, context: TradeContext) -> tuple[bool, str]:
        """
        Full exit evaluation.
        Returns (should_exit, reason).
        """
        result = self.evaluate_exit(signal, context)
        return result.passed, result.reason


# ── HELPER FUNCTIONS ─────────────────────────────────────────────────────────


def create_default_strategy() -> dict:
    """Create a default copy-trading strategy configuration."""
    return {
        "name": "Default Copy Strategy",
        "description": "Basic copy-trading with tiered sizing and standard risk limits",
        "rules": [
            {"rule_type": "entry", "rule_name": "copy_trader_signal", "rule_config": json.dumps({"min_usdc": 1.0})},
            {"rule_type": "exit", "rule_name": "copy_trader_exit", "rule_config": "{}"},
            {"rule_type": "sizing", "rule_name": "tiered_fixed", "rule_config": json.dumps({
                "tiers": [[5000, 100], [2000, 75], [1000, 50], [500, 25], [250, 15], [0, 10]]
            })},
            {"rule_type": "risk", "rule_name": "max_position_pct", "rule_config": json.dumps({"max_pct": 15.0})},
            {"rule_type": "risk", "rule_name": "max_wallet_exposure", "rule_config": json.dumps({"max_pct": 50.0})},
            {"rule_type": "risk", "rule_name": "max_market_exposure", "rule_config": json.dumps({"max_pct": 15.0})},
        ],
    }


def build_engine_from_config(config: dict) -> StrategyEngine:
    """Build a StrategyEngine from a config dict (not from database)."""
    engine = StrategyEngine(strategy_id=None)

    for rule_def in config.get("rules", []):
        rule_name = rule_def["rule_name"]
        rule_config = json.loads(rule_def["rule_config"]) if isinstance(rule_def["rule_config"], str) else rule_def["rule_config"]
        engine.add_rule(rule_name, rule_config)

    return engine
