"""
logger.py - Structured logging with rich colors for Cupsy bot.
"""

import sys
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.panel import Panel
from rich import box

# Custom theme
_theme = Theme({
    "info":    "bold cyan",
    "warning": "bold yellow",
    "error":   "bold red",
    "trade":   "bold green",
    "signal":  "bold magenta",
    "dim":     "dim white",
    "header":  "bold white on dark_blue",
})

_console = Console(theme=_theme, stderr=False)
_err_console = Console(theme=_theme, stderr=True)


def _timestamp() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _format(level: str, color: str, message: str) -> Text:
    t = Text()
    t.append(f"[{_timestamp()}] ", style="dim")
    t.append(f"[{level:<7}] ", style=color)
    t.append(message)
    return t


def info(message: str) -> None:
    _console.print(_format("INFO", "info", message))


def warning(message: str) -> None:
    _console.print(_format("WARNING", "warning", message))


def error(message: str) -> None:
    _err_console.print(_format("ERROR", "error", message))


def signal(message: str) -> None:
    _console.print(_format("SIGNAL", "signal", message))


def trade(message: str) -> None:
    _console.print(_format("TRADE", "trade", message))


def trade_table(
    action: str,  # "BUY" or "SELL"
    symbol: str,
    mint: str,
    amount_sol: float,
    price_usd: Optional[float],
    token_amount: Optional[float] = None,
    pnl_pct: Optional[float] = None,
    pnl_sol: Optional[float] = None,
    reason: Optional[str] = None,
    paper: bool = True,
) -> None:
    """Print a nicely formatted table for a buy/sell event."""
    color = "green" if action == "BUY" else "yellow"
    title = f"[bold {color}]{'[PAPER] ' if paper else ''}{action}[/bold {color}] {symbol}"

    table = Table(box=box.ROUNDED, show_header=False, padding=(0, 1))
    table.add_column("Field", style="bold white", width=18)
    table.add_column("Value", style=f"bold {color}")

    table.add_row("Symbol", symbol)
    table.add_row("Mint", mint[:20] + "..." if len(mint) > 23 else mint)
    table.add_row("Action", action)
    table.add_row("Amount SOL", f"{amount_sol:.4f} SOL")

    if token_amount is not None:
        table.add_row("Token Amount", f"{token_amount:,.0f}")
    if price_usd is not None:
        table.add_row("Price USD", f"${price_usd:.8f}")
    if pnl_pct is not None:
        pnl_style = "bold green" if pnl_pct >= 0 else "bold red"
        table.add_row("P&L %", f"[{pnl_style}]{pnl_pct:+.2f}%[/{pnl_style}]")
    if pnl_sol is not None:
        pnl_style = "bold green" if pnl_sol >= 0 else "bold red"
        table.add_row("P&L SOL", f"[{pnl_style}]{pnl_sol:+.4f} SOL[/{pnl_style}]")
    if reason:
        table.add_row("Reason", reason)

    _console.print(Panel(table, title=title, border_style=color))


def startup_banner(
    paper_trading: bool,
    buy_amount_sol: float,
    max_positions: int,
    take_profit_pct: float,
    stop_loss_pct: float,
    wallet_address: Optional[str] = None,
) -> None:
    """Print startup banner with configuration."""
    mode_label = "[bold yellow]PAPER TRADING[/bold yellow]" if paper_trading else "[bold green]LIVE TRADING[/bold green]"

    lines = [
        "",
        "  [bold cyan]  ██████╗██╗   ██╗██████╗ ███████╗██╗   ██╗[/bold cyan]",
        "  [bold cyan] ██╔════╝██║   ██║██╔══██╗██╔════╝╚██╗ ██╔╝[/bold cyan]",
        "  [bold cyan] ██║     ██║   ██║██████╔╝███████╗ ╚████╔╝ [/bold cyan]",
        "  [bold cyan] ██║     ██║   ██║██╔═══╝ ╚════██║  ╚██╔╝  [/bold cyan]",
        "  [bold cyan] ╚██████╗╚██████╔╝██║     ███████║   ██║   [/bold cyan]",
        "  [bold cyan]  ╚═════╝ ╚═════╝ ╚═╝     ╚══════╝   ╚═╝   [/bold cyan]",
        "",
        f"  AI Memecoin Trading Bot - v1.0.0",
        "",
        f"  Mode:           {mode_label}",
        f"  Buy Amount:     [bold]{buy_amount_sol:.4f} SOL[/bold]",
        f"  Max Positions:  [bold]{max_positions}[/bold]",
        f"  Take Profit:    [bold green]+{take_profit_pct:.0f}%[/bold green]",
        f"  Stop Loss:      [bold red]-{stop_loss_pct:.0f}%[/bold red]",
    ]
    if wallet_address:
        lines.append(f"  Wallet:         [dim]{wallet_address}[/dim]")
    lines.append("")

    panel_content = "\n".join(lines)
    border = "yellow" if paper_trading else "green"
    _console.print(Panel(panel_content, border_style=border, expand=False))
