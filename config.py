"""
config.py - Load and validate all environment variables for Cupsy trading bot.
"""

import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _get_env(key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
    """Retrieve an environment variable, optionally requiring it to be set."""
    value = os.getenv(key, default)
    if required and not value:
        print(f"[CONFIG ERROR] Required environment variable '{key}' is not set.")
        sys.exit(1)
    return value


def _get_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _get_float(key: str, default: float = 0.0, required: bool = False) -> float:
    raw = _get_env(key, str(default), required=required)
    try:
        return float(raw)
    except (ValueError, TypeError):
        print(f"[CONFIG ERROR] Environment variable '{key}' must be a number (got '{raw}').")
        sys.exit(1)


def _get_int(key: str, default: int = 0, required: bool = False) -> int:
    raw = _get_env(key, str(default), required=required)
    try:
        return int(raw)
    except (ValueError, TypeError):
        print(f"[CONFIG ERROR] Environment variable '{key}' must be an integer (got '{raw}').")
        sys.exit(1)


@dataclass
class Config:
    # Solana RPC endpoints
    solana_rpc_url: str = field(default_factory=lambda: _get_env(
        "SOLANA_RPC_URL",
        "https://api.mainnet-beta.solana.com",
    ))
    solana_ws_url: str = field(default_factory=lambda: _get_env(
        "SOLANA_WS_URL",
        "wss://api.mainnet-beta.solana.com",
    ))

    # Wallet
    wallet_private_key: Optional[str] = field(default_factory=lambda: _get_env(
        "WALLET_PRIVATE_KEY",
    ))

    # Anthropic
    anthropic_api_key: Optional[str] = field(default_factory=lambda: _get_env(
        "ANTHROPIC_API_KEY",
    ))

    # Trading settings
    paper_trading: bool = field(default_factory=lambda: _get_bool("PAPER_TRADING", True))
    buy_amount_sol: float = field(default_factory=lambda: _get_float("BUY_AMOUNT_SOL", 0.1))
    max_positions: int = field(default_factory=lambda: _get_int("MAX_POSITIONS", 5))
    take_profit_pct: float = field(default_factory=lambda: _get_float("TAKE_PROFIT_PCT", 150.0))
    stop_loss_pct: float = field(default_factory=lambda: _get_float("STOP_LOSS_PCT", 30.0))
    max_slippage_bps: int = field(default_factory=lambda: _get_int("MAX_SLIPPAGE_BPS", 300))

    # Token filtering
    min_liquidity_usd: float = field(default_factory=lambda: _get_float("MIN_LIQUIDITY_USD", 5000.0))
    max_dev_wallet_pct: float = field(default_factory=lambda: _get_float("MAX_DEV_WALLET_PCT", 10.0))
    min_holder_count: int = field(default_factory=lambda: _get_int("MIN_HOLDER_COUNT", 50))

    # External API endpoints (not usually overridden)
    dexscreener_base: str = "https://api.dexscreener.com/latest/dex/tokens"
    rugcheck_base: str = "https://api.rugcheck.xyz/v1/tokens"
    jupiter_quote_url: str = "https://quote-api.jup.ag/v6/quote"
    jupiter_swap_url: str = "https://quote-api.jup.ag/v6/swap"
    jupiter_price_url: str = "https://price.jup.ag/v4/price"
    pumpfun_ws_url: str = "wss://pumpportal.fun/api/data"
    helius_api_key: Optional[str] = field(default_factory=lambda: _get_env("HELIUS_API_KEY"))

    # Web dashboard
    dashboard_port: int = field(default_factory=lambda: _get_int("DASHBOARD_PORT", 8080))
    dashboard_enabled: bool = field(default_factory=lambda: _get_bool("DASHBOARD_ENABLED", True))

    def validate(self) -> "Config":
        """Warn about missing optional but important keys."""
        warnings = []
        if not self.wallet_private_key:
            warnings.append("WALLET_PRIVATE_KEY not set - wallet operations will fail")
        if not self.anthropic_api_key:
            warnings.append("ANTHROPIC_API_KEY not set - AI brain will not function")
        if self.paper_trading:
            warnings.append("PAPER_TRADING=true - no real trades will be executed")
        for w in warnings:
            print(f"[CONFIG WARN] {w}")
        return self


# Singleton instance
config = Config().validate()
