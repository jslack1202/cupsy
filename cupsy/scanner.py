"""
scanner.py - WebSocket listener for new Solana memecoin token launches.

Monitors:
  1. pump.fun via pumpportal.fun WebSocket API
  2. Raydium new AMM pools via Solana program log subscriptions
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncGenerator, Optional

import websockets
import aiohttp
from websockets.exceptions import ConnectionClosed, WebSocketException

from config import config
from cupsy import logger


class TokenSource(str, Enum):
    PUMP_FUN = "pump_fun"
    RAYDIUM = "raydium"


@dataclass
class TokenLaunch:
    mint_address: str
    name: str
    symbol: str
    timestamp: float
    source: TokenSource
    initial_liquidity_sol: float
    creator_wallet: str
    uri: str = ""
    description: str = ""


# Raydium AMM v4 program
RAYDIUM_AMM_PROGRAM = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
SOL_MINT = "So11111111111111111111111111111111111111112"

# Reconnect backoff settings
_INITIAL_BACKOFF = 2.0
_MAX_BACKOFF = 60.0


async def _backoff_sleep(attempt: int) -> float:
    delay = min(_INITIAL_BACKOFF * (2 ** attempt), _MAX_BACKOFF)
    await asyncio.sleep(delay)
    return delay


class PumpFunScanner:
    """
    Connects to pumpportal.fun and subscribes to new token creation events.
    """

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                logger.info(f"PumpFun: connecting to {config.pumpfun_ws_url}")
                async with websockets.connect(
                    config.pumpfun_ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    attempt = 0
                    # Subscribe to new token events
                    subscribe_msg = json.dumps({"method": "subscribeNewToken"})
                    await ws.send(subscribe_msg)
                    logger.info("PumpFun: subscribed to new token events")

                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            token = self._parse_event(data)
                            if token:
                                await self._queue.put(token)
                        except Exception as exc:
                            logger.warning(f"PumpFun: failed to parse message: {exc}")

            except (ConnectionClosed, WebSocketException) as exc:
                delay = await _backoff_sleep(attempt)
                logger.warning(f"PumpFun: disconnected ({exc}), reconnecting in {delay:.0f}s")
                attempt += 1
            except Exception as exc:
                delay = await _backoff_sleep(attempt)
                logger.error(f"PumpFun: unexpected error ({exc}), reconnecting in {delay:.0f}s")
                attempt += 1

    def _parse_event(self, data: dict) -> Optional[TokenLaunch]:
        """Parse a pumpportal new token event into a TokenLaunch."""
        # pumpportal emits events with keys: mint, name, symbol, uri, traderPublicKey, initialBuy, etc.
        mint = data.get("mint") or data.get("mintAddress")
        if not mint:
            return None

        name = data.get("name", "Unknown")
        symbol = data.get("symbol", "???")
        creator = data.get("traderPublicKey") or data.get("creator") or ""
        uri = data.get("uri", "")
        description = data.get("description", "")

        # initial_buy is in SOL; some events include vSolInBondingCurve
        initial_sol = float(data.get("vSolInBondingCurve") or data.get("solAmount") or 0.0)

        return TokenLaunch(
            mint_address=mint,
            name=name,
            symbol=symbol,
            timestamp=time.time(),
            source=TokenSource.PUMP_FUN,
            initial_liquidity_sol=initial_sol,
            creator_wallet=creator,
            uri=uri,
            description=description,
        )


class RaydiumScanner:
    """
    Monitors Solana program logs for Raydium AMM initialize2 instructions,
    which signal a brand-new liquidity pool creation.
    """

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue
        self._seen: set[str] = set()

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                logger.info(f"Raydium: connecting to {config.solana_ws_url}")
                async with websockets.connect(
                    config.solana_ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    attempt = 0
                    sub_request = json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [RAYDIUM_AMM_PROGRAM]},
                            {"commitment": "confirmed"},
                        ],
                    })
                    await ws.send(sub_request)
                    logger.info("Raydium: subscribed to program logs")

                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            token = await self._parse_log_event(data)
                            if token:
                                await self._queue.put(token)
                        except Exception as exc:
                            logger.warning(f"Raydium: failed to parse log: {exc}")

            except (ConnectionClosed, WebSocketException) as exc:
                delay = await _backoff_sleep(attempt)
                logger.warning(f"Raydium: disconnected ({exc}), reconnecting in {delay:.0f}s")
                attempt += 1
            except Exception as exc:
                delay = await _backoff_sleep(attempt)
                logger.error(f"Raydium: unexpected error ({exc}), reconnecting in {delay:.0f}s")
                attempt += 1

    async def _parse_log_event(self, data: dict) -> Optional[TokenLaunch]:
        """Parse a Solana program log notification for new Raydium pool creation."""
        # Top-level response is subscription confirmation; skip those
        if "result" in data and "id" in data:
            return None

        params = data.get("params", {})
        result = params.get("result", {})
        value = result.get("value", {})
        logs: list = value.get("logs", [])
        signature: str = value.get("signature", "")

        if not logs or not signature:
            return None

        # Look for initialize2 in logs (new pool creation)
        is_init = any("initialize2" in log for log in logs)
        if not is_init:
            return None

        if signature in self._seen:
            return None
        self._seen.add(signature)

        # Keep seen set bounded
        if len(self._seen) > 10000:
            self._seen = set(list(self._seen)[-5000:])

        # Fetch transaction details to extract mint address
        token = await self._fetch_pool_details(signature)
        return token

    async def _fetch_pool_details(self, signature: str) -> Optional[TokenLaunch]:
        """Fetch transaction to extract mint address and creator from Raydium pool creation."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
            ],
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config.solana_rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
        except Exception as exc:
            logger.warning(f"Raydium: could not fetch tx {signature}: {exc}")
            return None

        tx = (data.get("result") or {})
        if not tx:
            return None

        # Extract accounts from the transaction
        try:
            account_keys = []
            meta = tx.get("meta", {})
            message = tx.get("transaction", {}).get("message", {})

            # Get all account keys from static + dynamic lists
            static_keys = message.get("accountKeys", [])
            for k in static_keys:
                if isinstance(k, dict):
                    account_keys.append(k.get("pubkey", ""))
                else:
                    account_keys.append(str(k))

            # The fee payer (index 0) is typically the creator
            creator = account_keys[0] if account_keys else ""

            # Find non-SOL mint: scan post token balances for a new mint
            post_balances = meta.get("postTokenBalances", [])
            mint_address = ""
            for tb in post_balances:
                m = tb.get("mint", "")
                if m and m != SOL_MINT:
                    mint_address = m
                    break

            if not mint_address:
                return None

            return TokenLaunch(
                mint_address=mint_address,
                name="Unknown",
                symbol="???",
                timestamp=time.time(),
                source=TokenSource.RAYDIUM,
                initial_liquidity_sol=0.0,
                creator_wallet=creator,
            )
        except Exception as exc:
            logger.warning(f"Raydium: error parsing tx {signature}: {exc}")
            return None


class Scanner:
    """
    Aggregates PumpFun and Raydium scanners into a single async generator.
    """

    def __init__(self):
        self._queue: asyncio.Queue[TokenLaunch] = asyncio.Queue(maxsize=500)
        self._pumpfun = PumpFunScanner(self._queue)
        self._raydium = RaydiumScanner(self._queue)

    async def scan(self) -> AsyncGenerator[TokenLaunch, None]:
        """
        Start background scanner tasks and yield TokenLaunch events as they arrive.
        """
        pf_task = asyncio.create_task(self._pumpfun.run(), name="pumpfun_scanner")
        ray_task = asyncio.create_task(self._raydium.run(), name="raydium_scanner")

        logger.info("Scanner: started PumpFun and Raydium listeners")

        try:
            while True:
                token = await self._queue.get()
                yield token
        finally:
            pf_task.cancel()
            ray_task.cancel()
            try:
                await asyncio.gather(pf_task, ray_task, return_exceptions=True)
            except Exception:
                pass
