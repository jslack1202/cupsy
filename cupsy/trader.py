"""
trader.py - Jupiter V6 DEX swap execution for Cupsy bot.

Handles:
- Getting swap quotes from Jupiter V6 API
- Executing buy/sell swaps (or simulating in paper trading mode)
- Tracking open positions
"""

import asyncio
import base64
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

from config import config
from cupsy import logger
from cupsy.wallet import Wallet
from cupsy.paper_tracker import paper_tracker

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000


@dataclass
class Position:
    mint: str
    symbol: str
    buy_price_usd: float
    buy_price_sol: float        # price of token in SOL at buy time
    token_amount: float         # number of tokens received
    sol_spent: float            # SOL spent (after fees)
    buy_timestamp: float = field(default_factory=time.time)
    buy_tx_sig: Optional[str] = None
    paper: bool = True


@dataclass
class SwapResult:
    success: bool
    tx_signature: Optional[str]
    token_amount: float
    sol_amount: float
    price_usd: float
    error: Optional[str] = None


async def _retry_post(session: aiohttp.ClientSession, url: str, payload: dict,
                      max_attempts: int = 3) -> Optional[dict]:
    """POST with exponential backoff retry."""
    for attempt in range(max_attempts):
        try:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 429:
                    wait = 2 ** attempt
                    logger.warning(f"Rate limited on {url}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if resp.status == 200:
                    return await resp.json()
                text = await resp.text()
                logger.warning(f"HTTP {resp.status} from {url}: {text[:200]}")
                return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout on {url} (attempt {attempt + 1})")
        except Exception as exc:
            logger.warning(f"Request error {url}: {exc}")
        if attempt < max_attempts - 1:
            await asyncio.sleep(2 ** attempt)
    return None


async def _retry_get(session: aiohttp.ClientSession, url: str, params: Optional[dict] = None,
                     max_attempts: int = 3) -> Optional[dict]:
    """GET with exponential backoff retry."""
    for attempt in range(max_attempts):
        try:
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 429:
                    wait = 2 ** attempt
                    await asyncio.sleep(wait)
                    continue
                if resp.status == 200:
                    return await resp.json()
                return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout on {url} (attempt {attempt + 1})")
        except Exception as exc:
            logger.warning(f"Request error {url}: {exc}")
        if attempt < max_attempts - 1:
            await asyncio.sleep(2 ** attempt)
    return None


class Trader:
    """Executes trades via Jupiter V6 aggregator."""

    def __init__(self, wallet: Wallet):
        self._wallet = wallet

    async def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount_lamports: int,
        slippage_bps: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch a swap quote from Jupiter V6 /quote endpoint.
        Returns the raw quote response dict, or None on failure.
        """
        if slippage_bps is None:
            slippage_bps = config.max_slippage_bps

        params = {
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(amount_lamports),
            "slippageBps": str(slippage_bps),
            "onlyDirectRoutes": "false",
            "asLegacyTransaction": "false",
        }
        async with aiohttp.ClientSession() as session:
            data = await _retry_get(session, config.jupiter_quote_url, params=params)
        return data

    async def _get_token_price_usd(self, mint: str) -> float:
        """Fetch current USD price from Jupiter price API."""
        try:
            async with aiohttp.ClientSession() as session:
                data = await _retry_get(
                    session,
                    config.jupiter_price_url,
                    params={"ids": mint},
                )
            if data:
                return float(data.get("data", {}).get(mint, {}).get("price", 0))
        except Exception:
            pass
        return 0.0

    async def buy(self, mint: str, symbol: str, amount_sol: float) -> Optional[Position]:
        """
        Buy a token with the specified SOL amount.
        In paper trading mode, simulates the swap without sending a transaction.
        Returns a Position on success, None on failure.
        """
        lamports = int(amount_sol * LAMPORTS_PER_SOL)

        if config.paper_trading:
            return await self._paper_buy(mint, symbol, amount_sol, lamports)
        else:
            return await self._live_buy(mint, symbol, amount_sol, lamports)

    async def _paper_buy(self, mint: str, symbol: str, amount_sol: float,
                         lamports: int) -> Optional[Position]:
        """Simulate a buy: get quote to calculate tokens received, then fake the trade."""
        quote = await self.get_quote(SOL_MINT, mint, lamports)
        if not quote:
            logger.warning(f"Trader[paper]: could not get quote for {symbol}, skipping")
            return None

        out_amount = int(quote.get("outAmount", 0))
        if out_amount == 0:
            logger.warning(f"Trader[paper]: zero output amount for {symbol}")
            return None

        price_usd = await self._get_token_price_usd(mint)

        # Approximate: out_amount is in token lamports (raw), need to convert to UI amount
        # We store raw amount and convert based on decimals from quote
        route_plan = quote.get("routePlan") or []
        decimals = 6  # default, most SPL tokens
        if route_plan:
            # Try to infer decimals from the output amount vs quote
            pass

        token_amount = out_amount / (10 ** decimals)
        buy_price_sol = amount_sol / token_amount if token_amount > 0 else 0

        position = Position(
            mint=mint,
            symbol=symbol,
            buy_price_usd=price_usd,
            buy_price_sol=buy_price_sol,
            token_amount=token_amount,
            sol_spent=amount_sol,
            paper=True,
        )

        logger.trade_table(
            action="BUY",
            symbol=symbol,
            mint=mint,
            amount_sol=amount_sol,
            price_usd=price_usd if price_usd else None,
            token_amount=token_amount,
            paper=True,
        )
        paper_tracker.record_buy(position)
        return position

    async def _live_buy(self, mint: str, symbol: str, amount_sol: float,
                        lamports: int) -> Optional[Position]:
        """Execute a real buy via Jupiter swap."""
        # Check SOL balance first
        sol_balance = await self._wallet.get_sol_balance()
        if sol_balance < amount_sol + 0.01:  # 0.01 SOL for fees
            logger.error(f"Trader: insufficient SOL balance ({sol_balance:.4f}) for buy of {amount_sol:.4f}")
            return None

        quote = await self.get_quote(SOL_MINT, mint, lamports)
        if not quote:
            logger.warning(f"Trader: could not get quote for {symbol}")
            return None

        # Get swap transaction from Jupiter
        swap_payload = {
            "quoteResponse": quote,
            "userPublicKey": self._wallet.public_key,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }

        async with aiohttp.ClientSession() as session:
            swap_resp = await _retry_post(session, config.jupiter_swap_url, swap_payload)

        if not swap_resp or "swapTransaction" not in swap_resp:
            logger.error(f"Trader: failed to get swap transaction for {symbol}")
            return None

        # Decode, sign, and send
        tx_bytes = base64.b64decode(swap_resp["swapTransaction"])
        try:
            signed_bytes = self._wallet.sign_transaction_bytes(tx_bytes)
        except Exception as exc:
            logger.error(f"Trader: failed to sign transaction: {exc}")
            return None

        sig = await self._wallet.send_transaction(signed_bytes)
        if not sig:
            logger.error(f"Trader: transaction failed to send for {symbol}")
            return None

        logger.info(f"Trader: BUY tx sent: {sig}")

        out_amount = int(quote.get("outAmount", 0))
        decimals = 6
        token_amount = out_amount / (10 ** decimals)
        buy_price_sol = amount_sol / token_amount if token_amount > 0 else 0
        price_usd = await self._get_token_price_usd(mint)

        position = Position(
            mint=mint,
            symbol=symbol,
            buy_price_usd=price_usd,
            buy_price_sol=buy_price_sol,
            token_amount=token_amount,
            sol_spent=amount_sol,
            buy_tx_sig=sig,
            paper=False,
        )

        logger.trade_table(
            action="BUY",
            symbol=symbol,
            mint=mint,
            amount_sol=amount_sol,
            price_usd=price_usd if price_usd else None,
            token_amount=token_amount,
            paper=False,
        )
        return position

    async def sell(
        self,
        position: Position,
        reason: str = "EXIT",
        current_price_usd: Optional[float] = None,
    ) -> Optional[SwapResult]:
        """
        Sell all tokens in a position back to SOL.
        In paper mode, simulates the exit.
        """
        if config.paper_trading:
            return await self._paper_sell(position, reason, current_price_usd)
        else:
            return await self._live_sell(position, reason, current_price_usd)

    async def _paper_sell(
        self,
        position: Position,
        reason: str,
        current_price_usd: Optional[float],
    ) -> SwapResult:
        """Simulate a sell in paper trading mode."""
        if current_price_usd is None:
            current_price_usd = await self._get_token_price_usd(position.mint)

        # Calculate paper P&L
        if position.buy_price_usd > 0 and current_price_usd > 0:
            pnl_pct = ((current_price_usd - position.buy_price_usd) / position.buy_price_usd) * 100
        else:
            pnl_pct = 0.0

        # Estimate SOL received (proportional to price change)
        price_ratio = (current_price_usd / position.buy_price_usd) if position.buy_price_usd > 0 else 1.0
        sol_received = position.sol_spent * price_ratio
        pnl_sol = sol_received - position.sol_spent

        logger.trade_table(
            action="SELL",
            symbol=position.symbol,
            mint=position.mint,
            amount_sol=sol_received,
            price_usd=current_price_usd,
            token_amount=position.token_amount,
            pnl_pct=pnl_pct,
            pnl_sol=pnl_sol,
            reason=reason,
            paper=True,
        )
        paper_tracker.record_sell(
            position=position,
            exit_price_usd=current_price_usd,
            sol_received=sol_received,
            reason=reason,
        )

        return SwapResult(
            success=True,
            tx_signature=None,
            token_amount=position.token_amount,
            sol_amount=sol_received,
            price_usd=current_price_usd,
        )

    async def _live_sell(
        self,
        position: Position,
        reason: str,
        current_price_usd: Optional[float],
    ) -> Optional[SwapResult]:
        """Execute a real sell via Jupiter."""
        # Get actual token balance to sell
        token_balance = await self._wallet.get_token_balance(position.mint)
        if token_balance <= 0:
            logger.warning(f"Trader: no token balance to sell for {position.symbol}")
            return None

        # Convert UI amount to raw amount (lamports)
        decimals = 6
        raw_amount = int(token_balance * (10 ** decimals))

        quote = await self.get_quote(position.mint, SOL_MINT, raw_amount)
        if not quote:
            logger.warning(f"Trader: could not get sell quote for {position.symbol}")
            return None

        swap_payload = {
            "quoteResponse": quote,
            "userPublicKey": self._wallet.public_key,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "prioritizationFeeLamports": "auto",
        }

        async with aiohttp.ClientSession() as session:
            swap_resp = await _retry_post(session, config.jupiter_swap_url, swap_payload)

        if not swap_resp or "swapTransaction" not in swap_resp:
            logger.error(f"Trader: failed to get sell transaction for {position.symbol}")
            return None

        tx_bytes = base64.b64decode(swap_resp["swapTransaction"])
        try:
            signed_bytes = self._wallet.sign_transaction_bytes(tx_bytes)
        except Exception as exc:
            logger.error(f"Trader: failed to sign sell transaction: {exc}")
            return None

        sig = await self._wallet.send_transaction(signed_bytes)
        if not sig:
            logger.error(f"Trader: sell transaction failed for {position.symbol}")
            return None

        logger.info(f"Trader: SELL tx sent: {sig}")

        sol_out = int(quote.get("outAmount", 0)) / LAMPORTS_PER_SOL
        if current_price_usd is None:
            current_price_usd = await self._get_token_price_usd(position.mint)

        pnl_pct = ((current_price_usd - position.buy_price_usd) / position.buy_price_usd) * 100 if position.buy_price_usd > 0 else 0.0
        pnl_sol = sol_out - position.sol_spent

        logger.trade_table(
            action="SELL",
            symbol=position.symbol,
            mint=position.mint,
            amount_sol=sol_out,
            price_usd=current_price_usd,
            token_amount=token_balance,
            pnl_pct=pnl_pct,
            pnl_sol=pnl_sol,
            reason=reason,
            paper=False,
        )

        return SwapResult(
            success=True,
            tx_signature=sig,
            token_amount=token_balance,
            sol_amount=sol_out,
            price_usd=current_price_usd,
        )
