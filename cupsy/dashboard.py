"""
dashboard.py - Live paper trading dashboard for Cupsy.

Displays a real-time terminal UI using Rich showing:
  - Session stats (win rate, total P&L, uptime, tokens scanned)
  - Open positions with live P&L from real market prices
  - Closed trades history with final results

Run as an asyncio background task via run_dashboard().
Only active when PAPER_TRADING=true.
"""

import asyncio
import time

from rich import box
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from cupsy.paper_tracker import paper_tracker, OpenTrade, TradeRecord, SessionStats

REFRESH_SECONDS = 5

console = Console()


def _pnl_style(pnl_pct: float) -> str:
    if pnl_pct >= 100:
        return "bold bright_green"
    elif pnl_pct >= 20:
        return "green"
    elif pnl_pct >= 0:
        return "bright_white"
    elif pnl_pct >= -15:
        return "yellow"
    else:
        return "red"


def _build_stats_panel(stats: SessionStats) -> Panel:
    uptime_s = time.time() - stats.session_start
    hours = int(uptime_s // 3600)
    minutes = int((uptime_s % 3600) // 60)
    seconds = int(uptime_s % 60)

    pnl_color = "bright_green" if stats.total_pnl_sol >= 0 else "red"
    win_color = (
        "bright_green" if stats.win_rate >= 60
        else "yellow" if stats.win_rate >= 40
        else "red"
    )

    text = Text(justify="center")
    text.append(f" Uptime {hours:02d}:{minutes:02d}:{seconds:02d} ", style="cyan")
    text.append("│ ", style="dim")
    text.append(f"Scanned {stats.tokens_scanned} ", style="white")
    text.append("│ ", style="dim")
    text.append(f"Bought {stats.tokens_bought} ", style="white")
    text.append("│ ", style="dim")
    text.append(f"Closed {stats.total_trades} ", style="white")
    text.append("│ ", style="dim")
    text.append(f"Win Rate {stats.win_rate:.0f}% ", style=f"bold {win_color}")
    text.append("│ ", style="dim")
    text.append(f"P&L {stats.total_pnl_sol:+.4f} SOL ", style=f"bold {pnl_color}")
    text.append("│ ", style="dim")
    text.append(f"Best {stats.best_trade_pct:+.1f}% ", style="green")
    text.append("│ ", style="dim")
    text.append(f"Worst {stats.worst_trade_pct:+.1f}%", style="red")

    return Panel(
        text,
        title="[bold cyan] CUPSY PAPER TRADING [/bold cyan]",
        subtitle="[dim]All prices are live market data[/dim]",
        border_style="cyan",
    )


def _build_open_table(open_trades: list[OpenTrade]) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold magenta",
        expand=True,
        show_edge=False,
    )
    table.add_column("Token", style="bold white", min_width=8)
    table.add_column("Entry Price", justify="right", min_width=14)
    table.add_column("Live Price", justify="right", min_width=14)
    table.add_column("P&L %", justify="right", min_width=9)
    table.add_column("P&L SOL", justify="right", min_width=10)
    table.add_column("SOL In", justify="right", min_width=8)
    table.add_column("Age", justify="right", min_width=8)
    table.add_column("Updated", justify="right", min_width=10)

    if not open_trades:
        table.add_row("[dim]Waiting for buys...[/dim]", "", "", "", "", "", "", "")
    else:
        for t in sorted(open_trades, key=lambda x: x.pnl_pct, reverse=True):
            age_m = int(t.age_seconds / 60)
            age_s = int(t.age_seconds % 60)
            updated_ago = int(time.time() - t.last_updated)
            style = _pnl_style(t.pnl_pct)
            live_price_str = (
                f"${t.current_price_usd:.8f}"
                if t.current_price_usd > 0
                else "[dim]fetching...[/dim]"
            )
            table.add_row(
                t.symbol,
                f"${t.entry_price_usd:.8f}",
                live_price_str,
                f"[{style}]{t.pnl_pct:+.1f}%[/{style}]",
                f"[{style}]{t.pnl_sol:+.4f}[/{style}]",
                f"{t.sol_spent:.4f}",
                f"{age_m}m{age_s:02d}s",
                f"{updated_ago}s ago",
            )

    count = len(open_trades)
    title = (
        f"[bold magenta]Open Positions ({count})[/bold magenta]"
        if count > 0
        else "[dim]Open Positions (0)[/dim]"
    )
    return Panel(table, title=title, border_style="magenta")


def _build_closed_table(closed_trades: list[TradeRecord]) -> Panel:
    table = Table(
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold blue",
        expand=True,
        show_edge=False,
    )
    table.add_column("#", style="dim", min_width=6)
    table.add_column("Token", style="bold white", min_width=8)
    table.add_column("P&L %", justify="right", min_width=9)
    table.add_column("P&L SOL", justify="right", min_width=10)
    table.add_column("Entry $", justify="right", min_width=12)
    table.add_column("Exit $", justify="right", min_width=12)
    table.add_column("SOL In", justify="right", min_width=8)
    table.add_column("Hold", justify="right", min_width=9)
    table.add_column("Exit Reason", min_width=22)
    table.add_column("Snapshots", justify="right", min_width=9)

    if not closed_trades:
        table.add_row("[dim]No closed trades yet[/dim]", "", "", "", "", "", "", "", "", "")
    else:
        for t in closed_trades[:15]:
            hold_m = int(t.hold_seconds / 60)
            hold_s = int(t.hold_seconds % 60)
            style = _pnl_style(t.pnl_pct)
            icon = "[bold bright_green]W[/bold bright_green]" if t.won else "[bold red]L[/bold red]"
            table.add_row(
                f"{icon} {t.trade_id}",
                t.symbol,
                f"[{style}]{t.pnl_pct:+.1f}%[/{style}]",
                f"[{style}]{t.pnl_sol:+.4f}[/{style}]",
                f"${t.entry_price_usd:.8f}",
                f"${t.exit_price_usd:.8f}",
                f"{t.sol_spent:.4f}",
                f"{hold_m}m{hold_s:02d}s",
                t.exit_reason,
                str(t.price_snapshots_taken),
            )

    count = len(closed_trades)
    return Panel(
        table,
        title=f"[bold blue]Closed Trades ({count} total)[/bold blue]",
        subtitle="[dim]Saved to data/paper_trades.csv[/dim]",
        border_style="blue",
    )


def _build_layout() -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="stats", size=3),
        Layout(name="open", ratio=2),
        Layout(name="closed", ratio=3),
    )
    stats = paper_tracker.get_stats()
    open_trades = paper_tracker.get_open_trades()
    closed_trades = paper_tracker.get_closed_trades(limit=15)
    layout["stats"].update(_build_stats_panel(stats))
    layout["open"].update(_build_open_table(open_trades))
    layout["closed"].update(_build_closed_table(closed_trades))
    return layout


async def run_dashboard() -> None:
    """
    Background asyncio task. Renders a live terminal dashboard that updates
    every REFRESH_SECONDS seconds using real price data from paper_tracker.
    """
    with Live(
        _build_layout(),
        console=console,
        refresh_per_second=1,
        screen=True,
    ) as live:
        while True:
            try:
                live.update(_build_layout())
            except Exception:
                pass  # never let a render error crash the bot
            await asyncio.sleep(REFRESH_SECONDS)
