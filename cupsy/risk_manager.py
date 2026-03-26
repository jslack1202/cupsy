"""
risk_manager.py - Position tracking and automated exit management for Cupsy.

Monitors all open positions and triggers sells when:
- Take-profit threshold is reached
- Stop-loss threshold is reached
- Position age exceeds 4 hours (stale position exit)
"""

import asyncio
import time
from typing import Optional

import aiohttp

from config import config
from cupsy import logger
from cupsy.trader import Position, Trader
from cupsy.paper_tracker import paper_tracker

# Maximum age for a position before forced exit (4 hours)
MAX_POSITION_AGE_SECONDS = 4 * 60 * 60
MONITOR_INTERVAL_SECONDS = 30


async def _get_price_dexscreener(session: aiohttp.ClientSession, mint: str) -> Optional[float]:
    """Fetch current USD price from DexScreener API."""
    url = f"{config.dexscreener_base}/{mint}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            pairs = data.get("pairs") or []
            if not pairs:
                return None
            # Highest liquidity pair
            pairs = sorted(
                pairs,
                key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0),
                reverse=True,
            )
            price = float(pairs[0].get("priceUsd") or 0)
            return price if price > 0 else None
    except Exception:
        return None


async def _get_price_jupiter(session: aiohttp.ClientSession, mint: str) -> Optional[float]:
    """Fetch current USD price from Jupiter price API as fallback."""
    try:
        async with session.get(
            config.jupiter_price_url,
            params={"ids": mint},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            price = float(data.get("data", {}).get(mint, {}).get("price", 0))
            return price if price > 0 else None
    except Exception:
        return None


async def get_current_price(mint: str) -> Optional[float]:
    """Try DexScreener first, fallback to Jupiter."""
    async with aiohttp.ClientSession() as session:
        price = await _get_price_dexscreener(session, mint)
        if price is None:
            price = await _get_price_jupiter(session, mint)
        return price


class RiskManager:
    """Tracks open positions and manages automated exits."""

    def __init__(self, trader: Trader):
        self._trader = trader
        self._positions: dict[str, Position] = {}  # mint -> Position
        self._closing: set[str] = set()  # mints currently being closed

    @property
    def position_count(self) -> int:
        return len(self._positions)

    def add_position(self, position: Position) -> None:
        """Register a new position for monitoring."""
        self._positions[position.mint] = position
        logger.info(
            f"RiskManager: tracking {position.symbol} "
            f"| buy_price=${position.buy_price_usd:.8f} "
            f"| tokens={position.token_amount:,.0f} "
            f"| sol_spent={position.sol_spent:.4f}"
        )

    def remove_position(self, mint: str) -> None:
        """Remove a position (after it has been closed)."""
        self._positions.pop(mint, None)
        self._closing.discard(mint)

    async def check_positions(self) -> None:
        """
        Check all open positions once and trigger exits as needed.
        Called in a loop by monitor_loop().
        """
        if not self._positions:
            return

        mints = list(self._positions.keys())
        for mint in mints:
            if mint not in self._positions:
                continue  # removed during iteration
            if mint in self._closing:
                continue  # already being closed

            position = self._positions[mint]
            try:
                await self._evaluate_position(position)
            except Exception as exc:
                logger.error(f"RiskManager: error evaluating {position.symbol}: {exc}")

    async def _evaluate_position(self, position: Position) -> None:
        """Evaluate a single position and sell if exit condition is met."""
        current_price = await get_current_price(position.mint)

        if current_price is None or current_price == 0:
            # Can't price it; check age
            age = time.time() - position.buy_timestamp
            if age > MAX_POSITION_AGE_SECONDS:
                logger.warning(
                    f"RiskManager: {position.symbol} unpriceable and >4h old, force exiting"
                )
                await self._exit_position(position, reason="TIME EXIT (unpriceable)", current_price=None)
            return

        buy_price = position.buy_price_usd
        if buy_price <= 0:
            return

        # Record real price snapshot for paper trading data analysis
        if config.paper_trading:
            paper_tracker.update_price(position.mint, position.symbol, current_price)

        pnl_pct = ((current_price - buy_price) / buy_price) * 100
        age_seconds = time.time() - position.buy_timestamp
        age_minutes = age_seconds / 60

        # --- Exit conditions ---

        # Time exit: stale position
        if age_seconds > MAX_POSITION_AGE_SECONDS:
            logger.warning(
                f"RiskManager: {position.symbol} TIME EXIT "
                f"| age={age_minutes:.0f}m | pnl={pnl_pct:+.1f}%"
            )
            await self._exit_position(position, reason=f"TIME EXIT ({age_minutes:.0f}m)", current_price=current_price)
            return

        # Take profit
        if pnl_pct >= config.take_profit_pct:
            logger.signal(
                f"RiskManager: {position.symbol} TAKE PROFIT "
                f"| pnl={pnl_pct:+.1f}% | price=${current_price:.8f}"
            )
            await self._exit_position(position, reason=f"TAKE PROFIT +{pnl_pct:.1f}%", current_price=current_price)
            return

        # Stop loss
        if pnl_pct <= -config.stop_loss_pct:
            logger.warning(
                f"RiskManager: {position.symbol} STOP LOSS "
                f"| pnl={pnl_pct:+.1f}% | price=${current_price:.8f}"
            )
            await self._exit_position(position, reason=f"STOP LOSS {pnl_pct:.1f}%", current_price=current_price)
            return

        # Log current status
        logger.info(
            f"RiskManager: {position.symbol} "
            f"| pnl={pnl_pct:+.1f}% "
            f"| price=${current_price:.8f} "
            f"| age={age_minutes:.0f}m"
        )

    async def _exit_position(
        self,
        position: Position,
        reason: str,
        current_price: Optional[float],
    ) -> None:
        """Execute sell and remove position from tracking."""
        self._closing.add(position.mint)
        try:
            result = await self._trader.sell(
                position=position,
                reason=reason,
                current_price_usd=current_price,
            )
            if result and result.success:
                logger.trade(
                    f"Position closed: {position.symbol} | reason={reason} | "
                    f"sol_out={result.sol_amount:.4f}"
                )
            else:
                logger.error(f"RiskManager: sell failed for {position.symbol}, keeping position")
                self._closing.discard(position.mint)
                return
        except Exception as exc:
            logger.error(f"RiskManager: exception during sell of {position.symbol}: {exc}")
            self._closing.discard(position.mint)
            return
        finally:
            pass

        self.remove_position(position.mint)

    async def monitor_loop(self) -> None:
        """
        Continuously monitor positions every MONITOR_INTERVAL_SECONDS.
        Designed to run as a background asyncio task.
        """
        logger.info(f"RiskManager: monitoring loop started (interval={MONITOR_INTERVAL_SECONDS}s)")
        while True:
            try:
                await self.check_positions()
            except Exception as exc:
                logger.error(f"RiskManager: unhandled error in monitor loop: {exc}")
            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)

    def get_summary(self) -> list[dict]:
        """Return a summary of all open positions for display."""
        summary = []
        for mint, pos in self._positions.items():
            summary.append({
                "symbol": pos.symbol,
                "mint": mint,
                "sol_spent": pos.sol_spent,
                "token_amount": pos.token_amount,
                "buy_price_usd": pos.buy_price_usd,
                "age_minutes": (time.time() - pos.buy_timestamp) / 60,
                "paper": pos.paper,
            })
        return summary
