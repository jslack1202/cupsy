"""
wallet.py - Solana wallet management for Cupsy bot.

Handles:
- Loading keypair from base58-encoded private key
- Querying SOL and token balances
- Signing transactions
"""

import base64
from typing import Optional

import aiohttp
import base58
from solders.keypair import Keypair  # type: ignore
from solders.pubkey import Pubkey  # type: ignore

from config import config
from cupsy import logger

SOL_MINT = "So11111111111111111111111111111111111111112"
LAMPORTS_PER_SOL = 1_000_000_000


class Wallet:
    """Solana wallet backed by a private key loaded from the environment."""

    def __init__(self):
        if not config.wallet_private_key:
            raise ValueError("WALLET_PRIVATE_KEY is not set.")
        self._keypair = self._load_keypair(config.wallet_private_key)
        logger.info(f"Wallet loaded: {self.public_key}")

    @staticmethod
    def _load_keypair(private_key_str: str) -> Keypair:
        """
        Accept multiple key formats:
        - 64-byte base58 string (standard Phantom/Solflare export)
        - JSON array string like [1,2,3,...] (Solana CLI format)
        """
        private_key_str = private_key_str.strip()

        # JSON array format
        if private_key_str.startswith("["):
            import json as _json
            raw = bytes(_json.loads(private_key_str))
            return Keypair.from_bytes(raw)

        # Base58 format - could be 32 bytes (seed) or 64 bytes (full keypair)
        decoded = base58.b58decode(private_key_str)
        if len(decoded) == 64:
            return Keypair.from_bytes(decoded)
        elif len(decoded) == 32:
            return Keypair.from_seed(decoded)
        else:
            raise ValueError(f"Unexpected key length: {len(decoded)} bytes")

    @property
    def keypair(self) -> Keypair:
        return self._keypair

    @property
    def public_key(self) -> str:
        return str(self._keypair.pubkey())

    @property
    def pubkey(self) -> Pubkey:
        return self._keypair.pubkey()

    async def get_sol_balance(self) -> float:
        """Return SOL balance as a float."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [self.public_key, {"commitment": "confirmed"}],
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config.solana_rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    lamports = (data.get("result") or {}).get("value", 0)
                    return lamports / LAMPORTS_PER_SOL
        except Exception as exc:
            logger.error(f"Wallet: failed to get SOL balance: {exc}")
            return 0.0

    async def get_token_balance(self, mint: str) -> float:
        """Return token balance (UI amount) for a given mint."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                self.public_key,
                {"mint": mint},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
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
                    accounts = (data.get("result") or {}).get("value") or []
                    total = 0.0
                    for acct in accounts:
                        parsed = (acct.get("account") or {}).get("data", {}).get("parsed", {})
                        ui_amount = parsed.get("info", {}).get("tokenAmount", {}).get("uiAmount") or 0
                        total += float(ui_amount)
                    return total
        except Exception as exc:
            logger.error(f"Wallet: failed to get token balance for {mint}: {exc}")
            return 0.0

    def sign_transaction_bytes(self, transaction_bytes: bytes) -> bytes:
        """
        Sign a transaction blob returned by Jupiter swap API.
        Jupiter returns base64-encoded versioned transactions; we sign and return bytes.
        """
        from solders.transaction import VersionedTransaction  # type: ignore

        tx = VersionedTransaction.from_bytes(transaction_bytes)
        tx.sign([self._keypair], tx.message.recent_blockhash)
        return bytes(tx)

    async def send_transaction(self, signed_tx_bytes: bytes) -> Optional[str]:
        """Send a signed transaction and return the signature string."""
        tx_b64 = base64.b64encode(signed_tx_bytes).decode()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [
                tx_b64,
                {
                    "encoding": "base64",
                    "skipPreflight": False,
                    "preflightCommitment": "confirmed",
                    "maxRetries": 3,
                },
            ],
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    config.solana_rpc_url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    data = await resp.json()
                    if "error" in data:
                        logger.error(f"Wallet: RPC sendTransaction error: {data['error']}")
                        return None
                    sig = data.get("result")
                    return sig
        except Exception as exc:
            logger.error(f"Wallet: sendTransaction failed: {exc}")
            return None
