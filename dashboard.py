#!/usr/bin/env python3
# dashboard.py — live terminal dashboard for DenseWealth
#
# Usage:
#   python dashboard.py          → live dashboard (refreshes every 5s)
#   python dashboard.py --once   → print once and exit

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure emoji output works on Windows consoles
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config import (
    COPY_STRATEGY,
    MAX_MARKET_PCT,
    MAX_TRADE_PCT,
    MAX_WALLET_PCT,
    PAPER_STARTING_BALANCE,
    POLL_INTERVAL,
    TARGET_WALLETS,
)
from db import (
    get_all_positions,
    get_paper_balance,
    get_paper_stats,
    get_trade_history,
    get_wallet_exposure,
)
from portfolio import get_trader_portfolio, refresh_all_portfolios

DB_PATH = Path(__file__).parent / "densewealth.db"

# Wallet labels (short names for display)
WALLET_LABELS = {}
for w in TARGET_WALLETS:
    WALLET_LABELS[w] = w[:10] + "..."


def _pnl_color(value: float) -> str:
    if value > 0:
        return "green"
    elif value < 0:
        return "red"
    return "white"


def build_header() -> Panel:
    """Top header with bot status."""
    stats = get_paper_stats()
    balance = stats.get("balance_usdc", 0) if stats else 0
    total_pnl = stats.get("total_pnl", 0) if stats else 0
    total_trades = stats.get("total_trades", 0) if stats else 0
    pnl_pct = (total_pnl / PAPER_STARTING_BALANCE * 100) if PAPER_STARTING_BALANCE > 0 else 0

    pnl_color = _pnl_color(total_pnl)

    grid = Table.grid(padding=(0, 3))
    grid.add_column(justify="left", min_width=20)
    grid.add_column(justify="left", min_width=20)
    grid.add_column(justify="left", min_width=20)
    grid.add_column(justify="left", min_width=20)
    grid.add_column(justify="left", min_width=20)

    grid.add_row(
        Text.assemble(("BALANCE  ", "bold"), (f"${balance:,.2f}", "bold cyan")),
        Text.assemble(("P&L  ", "bold"), (f"${total_pnl:+,.2f}", f"bold {pnl_color}")),
        Text.assemble(("RETURN  ", "bold"), (f"{pnl_pct:+.2f}%", f"bold {pnl_color}")),
        Text.assemble(("TRADES  ", "bold"), (f"{total_trades}", "bold white")),
        Text.assemble(("STRATEGY  ", "bold"), (f"{COPY_STRATEGY.upper()}", "bold yellow")),
    )

    now = datetime.now().strftime("%H:%M:%S")
    return Panel(
        grid,
        title=f"[bold white]DENSEWEALTH PAPER DASHBOARD[/bold white]  [dim]{now}[/dim]",
        border_style="cyan",
        padding=(1, 2),
    )


def build_wallets() -> Panel:
    """Per-wallet allocation panel with dynamic caps."""
    balance = get_paper_balance()
    wallet_budget = balance * MAX_WALLET_PCT

    table = Table(show_header=True, header_style="bold", expand=True, show_edge=False, pad_edge=False)
    table.add_column("Wallet", style="dim", min_width=14)
    table.add_column("Portfolio", justify="right", min_width=12)
    table.add_column("Exposure", justify="right", min_width=10)
    table.add_column(f"Budget ({MAX_WALLET_PCT:.0%})", justify="right", min_width=10)
    table.add_column("Remaining", justify="right", min_width=10)
    table.add_column("Usage", justify="left", min_width=20)

    for wallet in TARGET_WALLETS:
        exposure = get_wallet_exposure(wallet)
        remaining = wallet_budget - exposure
        usage_pct = (exposure / wallet_budget * 100) if wallet_budget > 0 else 0

        # Trader portfolio size
        trader_port = get_trader_portfolio(wallet)
        port_str = f"${trader_port:,.0f}" if trader_port > 0 else "[dim]unknown[/dim]"

        # Progress bar
        bar_width = 15
        filled = min(int(usage_pct / 100 * bar_width), bar_width)
        bar_color = "green" if usage_pct < 60 else ("yellow" if usage_pct < 85 else "red")
        bar = f"[{bar_color}]{'█' * filled}{'░' * (bar_width - filled)}[/{bar_color}] {usage_pct:.0f}%"

        remaining_color = "green" if remaining > 10 else ("yellow" if remaining > 0 else "red")

        table.add_row(
            WALLET_LABELS.get(wallet, wallet[:10]),
            port_str,
            f"${exposure:,.2f}",
            f"${wallet_budget:,.2f}",
            f"[{remaining_color}]${remaining:,.2f}[/{remaining_color}]",
            bar,
        )

    return Panel(table, title="[bold]WALLET BUDGETS[/bold]", border_style="blue")


def build_positions() -> Panel:
    """Open positions table."""
    positions = get_all_positions()

    table = Table(show_header=True, header_style="bold", expand=True, show_edge=False, pad_edge=False)
    table.add_column("Market", style="dim", min_width=14)
    table.add_column("Wallet", style="dim", min_width=12)
    table.add_column("Side", justify="center", min_width=5)
    table.add_column("Shares", justify="right", min_width=10)
    table.add_column("Spent", justify="right", min_width=9)
    table.add_column("Avg Cost", justify="right", min_width=9)

    if not positions:
        table.add_row("[dim]No open positions[/dim]", "", "", "", "", "")
    else:
        for pos in positions[:20]:
            market = pos["market_id"][:12] + "..."
            wallet = WALLET_LABELS.get(pos["trader_wallet"], pos["trader_wallet"][:10])
            side_color = "green" if pos["side"] == "BUY" else "red"
            avg_cost = pos["usdc_spent"] / pos["shares"] if pos["shares"] > 0 else 0

            table.add_row(
                market,
                wallet,
                f"[{side_color}]{pos['side']}[/{side_color}]",
                f"{pos['shares']:.4f}",
                f"${pos['usdc_spent']:.2f}",
                f"${avg_cost:.4f}",
            )
        if len(positions) > 20:
            table.add_row(f"[dim]... +{len(positions) - 20} more[/dim]", "", "", "", "", "")

    return Panel(table, title=f"[bold]POSITIONS ({len(positions)})[/bold]", border_style="green")


def build_trades() -> Panel:
    """Recent trades feed."""
    trades = get_trade_history(15)

    table = Table(show_header=True, header_style="bold", expand=True, show_edge=False, pad_edge=False)
    table.add_column("Time", style="dim", min_width=10)
    table.add_column("Side", justify="center", min_width=5)
    table.add_column("Shares", justify="right", min_width=10)
    table.add_column("Price", justify="right", min_width=8)
    table.add_column("Amount", justify="right", min_width=9)
    table.add_column("Market", style="dim", min_width=14)
    table.add_column("", justify="center", min_width=3)

    if not trades:
        table.add_row("[dim]No trades yet[/dim]", "", "", "", "", "", "")
    else:
        for t in trades:
            ts = t["timestamp"]
            if isinstance(ts, str) and len(ts) > 10:
                ts = ts[11:19]  # extract HH:MM:SS
            side_color = "green" if t["side"] == "BUY" else "red"
            status = "[green]OK[/green]" if t["success"] else "[red]FAIL[/red]"
            price = t["price"] or 0

            table.add_row(
                str(ts),
                f"[{side_color}]{t['side']}[/{side_color}]",
                f"{t['shares']:.4f}",
                f"${price:.4f}",
                f"${t['usdc_amount']:.2f}",
                t["market_id"][:12] + "...",
                status,
            )

    return Panel(table, title="[bold]LIVE TRADE FEED[/bold]", border_style="yellow")


def build_footer() -> Panel:
    """Config summary footer."""
    info = (
        f"Wallets: {len(TARGET_WALLETS)} | "
        f"Poll: {POLL_INTERVAL}s | "
        f"Strategy: {COPY_STRATEGY} | "
        f"Max trade: {MAX_TRADE_PCT:.0%} | "
        f"Max/wallet: {MAX_WALLET_PCT:.0%} | "
        f"Max/market: {MAX_MARKET_PCT:.0%} | "
        f"Starting: ${PAPER_STARTING_BALANCE:,.0f}"
    )
    return Panel(
        Text(info, style="dim"),
        border_style="dim",
        padding=(0, 1),
    )


def build_dashboard() -> Layout:
    """Assemble the full dashboard layout."""
    layout = Layout()

    layout.split_column(
        Layout(name="header", size=5),
        Layout(name="wallets", size=7 + len(TARGET_WALLETS)),
        Layout(name="middle"),
        Layout(name="footer", size=3),
    )

    layout["middle"].split_row(
        Layout(name="positions", ratio=1),
        Layout(name="trades", ratio=1),
    )

    layout["header"].update(build_header())
    layout["wallets"].update(build_wallets())
    layout["positions"].update(build_positions())
    layout["trades"].update(build_trades())
    layout["footer"].update(build_footer())

    return layout


def main():
    parser = argparse.ArgumentParser(description="DenseWealth Live Dashboard")
    parser.add_argument("--once", action="store_true", help="Print once and exit")
    parser.add_argument("--refresh", type=int, default=5, help="Refresh interval in seconds (default: 5)")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print("Database not found. Run main.py first to initialize.")
        sys.exit(1)

    # Fetch portfolio sizes for display
    refresh_all_portfolios()

    console = Console()

    if args.once:
        console.print(build_dashboard())
        return

    try:
        with Live(build_dashboard(), console=console, refresh_per_second=1, screen=True) as live:
            while True:
                time.sleep(args.refresh)
                live.update(build_dashboard())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
