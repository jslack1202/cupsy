"""
analyzer.py - Token scoring and risk analysis for Cupsy bot.

Scores each token 0-100 based on:
1. Liquidity check via DexScreener
2. Holder analysis via Helius or RPC
3. RugCheck API risk report
4. Dev wallet history check
5. Momentum / price direction
6. Social signal heuristics
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import aiohttp

from config import config
from cupsy import logger
from cupsy.scanner import TokenLaunch

# SOL price approximation for liquidity conversion (refreshed lazily)
_SOL_PRICE_USD: float = 150.0
_SOL_PRICE_UPDATED: float = 0.0
_SOL_PRICE_TTL: float = 120.0  # seconds


class Recommendation(str, Enum):
    BUY = "BUY"
    SKIP = "SKIP"
    WAIT = "WAIT"


@dataclass
class TokenAnalysis:
    mint_address: str
    symbol: str
    score: int  # 0-100
    liquidity_usd: float
    holder_count: int
    top_holder_pct: float
    is_honeypot: bool
    rugcheck_score: int  # 0-100 (higher = riskier per rugcheck)
    warnings: list[str]
    recommendation: Recommendation
    price_usd: float = 0.0
    market_cap_usd: float = 0.0
    volume_24h: float = 0.0


async def _get_sol_price(session: aiohttp.ClientSession) -> float:
    """Lazily refresh SOL/USD price from Jupiter price API."""
    global _SOL_PRICE_USD, _SOL_PRICE_UPDATED
    if time.time() - _SOL_PRICE_UPDATED < _SOL_PRICE_TTL:
        return _SOL_PRICE_USD
    try:
        async with session.get(
            "https://price.jup.ag/v4/price",
            params={"ids": "So11111111111111111111111111111111111111112"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            data = await resp.json()
            price = data["data"]["So11111111111111111111111111111111111111112"]["price"]
            _SOL_PRICE_USD = float(price)
            _SOL_PRICE_UPDATED = time.time()
    except Exception:
        pass  # keep using stale value
    return _SOL_PRICE_USD


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
                    logger.warning(f"Rate limited on {url}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if resp.status == 200:
                    return await resp.json()
                logger.warning(f"HTTP {resp.status} from {url}")
                return None
        except asyncio.TimeoutError:
            logger.warning(f"Timeout on {url} (attempt {attempt + 1})")
        except Exception as exc:
            logger.warning(f"Request error {url}: {exc}")
        if attempt < max_attempts - 1:
            await asyncio.sleep(2 ** attempt)
    return None


async def _check_liquidity(session: aiohttp.ClientSession, mint: str) -> dict:
    """Fetch DexScreener data for the token. Returns enriched data dict."""
    url = f"{config.dexscreener_base}/{mint}"
    data = await _retry_get(session, url)
    result = {
        "liquidity_usd": 0.0,
        "price_usd": 0.0,
        "market_cap_usd": 0.0,
        "volume_24h": 0.0,
        "price_change_5m": None,
        "price_change_1h": None,
    }
    if not data:
        return result
    pairs = data.get("pairs") or []
    if not pairs:
        return result
    # Pick the pair with highest liquidity
    pairs = sorted(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
    best = pairs[0]

    result["liquidity_usd"] = float((best.get("liquidity") or {}).get("usd") or 0)
    result["price_usd"] = float(best.get("priceUsd") or 0)
    result["market_cap_usd"] = float(best.get("marketCap") or best.get("fdv") or 0)
    result["volume_24h"] = float((best.get("volume") or {}).get("h24") or 0)
    pc = best.get("priceChange") or {}
    if "m5" in pc:
        result["price_change_5m"] = float(pc["m5"])
    if "h1" in pc:
        result["price_change_1h"] = float(pc["h1"])
    return result


async def _check_holders(session: aiohttp.ClientSession, mint: str) -> dict:
    """
    Fetch token largest accounts via Solana RPC to estimate holder distribution.
    Returns holder_count, top_holder_pct, whale_flag.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint, {"commitment": "confirmed"}],
    }
    result = {"holder_count": 0, "top_holder_pct": 100.0, "whale_flag": True}
    try:
        async with session.post(
            config.solana_rpc_url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
    except Exception as exc:
        logger.warning(f"Holder check failed for {mint}: {exc}")
        return result

    accounts = (data.get("result") or {}).get("value") or []
    if not accounts:
        return result

    # Sum up all balances to find total supply represented
    amounts = [int(a.get("amount", "0")) for a in accounts]
    total = sum(amounts)
    if total == 0:
        return result

    top = amounts[0]
    top_pct = (top / total) * 100 if total > 0 else 100.0

    result["holder_count"] = len(accounts)  # limited to top 20 by RPC
    result["top_holder_pct"] = round(top_pct, 2)
    result["whale_flag"] = top_pct > 15.0
    return result


async def _check_rugcheck(session: aiohttp.ClientSession, mint: str) -> dict:
    """Call rugcheck.xyz API and parse risk indicators."""
    url = f"{config.rugcheck_base}/{mint}/report"
    data = await _retry_get(session, url)
    result = {
        "rugcheck_score": 50,  # neutral default
        "mint_authority_enabled": False,
        "freeze_authority_enabled": False,
        "is_honeypot": False,
        "rugcheck_warnings": [],
    }
    if not data:
        return result

    # rugcheck score: 0 = safe, 100 = extremely risky
    score = data.get("score") or data.get("riskScore") or 50
    result["rugcheck_score"] = int(score)

    risks = data.get("risks") or data.get("warnings") or []
    warnings = []
    for risk in risks:
        name = (risk.get("name") or risk.get("type") or "").lower()
        description = risk.get("description") or risk.get("message") or name
        warnings.append(str(description))

        if "mint" in name and "authority" in name:
            result["mint_authority_enabled"] = True
        if "freeze" in name:
            result["freeze_authority_enabled"] = True
        if "honeypot" in name or "trap" in name:
            result["is_honeypot"] = True

    # Also check top-level flags
    if data.get("mintAuthorityEnabled") or data.get("mintAuthority"):
        result["mint_authority_enabled"] = True
    if data.get("freezeAuthorityEnabled") or data.get("freezeAuthority"):
        result["freeze_authority_enabled"] = True
    if data.get("isHoneypot"):
        result["is_honeypot"] = True

    result["rugcheck_warnings"] = warnings
    return result


async def _check_dev_wallet(session: aiohttp.ClientSession, creator: str, mint: str) -> dict:
    """
    Simple dev wallet check: look at token accounts owned by creator.
    If creator holds a large portion of supply, flag it.
    """
    result = {"dev_hold_pct": 0.0, "dev_flag": False}
    if not creator:
        return result

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            creator,
            {"mint": mint},
            {"encoding": "jsonParsed", "commitment": "confirmed"},
        ],
    }
    try:
        async with session.post(
            config.solana_rpc_url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            data = await resp.json()
    except Exception:
        return result

    accounts = (data.get("result") or {}).get("value") or []
    if not accounts:
        return result

    # Sum creator's balance
    creator_balance = 0
    for acct in accounts:
        parsed = (acct.get("account") or {}).get("data", {}).get("parsed", {})
        info = parsed.get("info", {})
        token_amount = info.get("tokenAmount", {})
        creator_balance += int(token_amount.get("amount", "0"))

    # Fetch total supply from token info
    supply_payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "getTokenSupply",
        "params": [mint],
    }
    total_supply = 0
    try:
        async with session.post(
            config.solana_rpc_url,
            json=supply_payload,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            supply_data = await resp.json()
            total_supply = int(
                ((supply_data.get("result") or {}).get("value") or {}).get("amount", "0")
            )
    except Exception:
        pass

    if total_supply > 0:
        pct = (creator_balance / total_supply) * 100
        result["dev_hold_pct"] = round(pct, 2)
        result["dev_flag"] = pct > float(config.max_dev_wallet_pct)

    return result


def _compute_score(
    liquidity_usd: float,
    holder_count: int,
    top_holder_pct: float,
    dev_hold_pct: float,
    rugcheck_score: int,
    mint_authority: bool,
    freeze_authority: bool,
    is_honeypot: bool,
    price_change_5m: Optional[float],
    warnings: list,
) -> int:
    """
    Compute composite score 0-100 (higher = more desirable).
    Immediate disqualifiers return 0.
    """
    if is_honeypot:
        return 0
    if mint_authority:
        return 0
    if freeze_authority:
        return 0

    score = 100

    # Liquidity (max 25 pts)
    if liquidity_usd >= 50_000:
        score -= 0
    elif liquidity_usd >= 20_000:
        score -= 5
    elif liquidity_usd >= 10_000:
        score -= 10
    elif liquidity_usd >= config.min_liquidity_usd:
        score -= 18
    else:
        score -= 30  # below minimum

    # Holder count (max 20 pts)
    if holder_count >= 500:
        score -= 0
    elif holder_count >= 200:
        score -= 5
    elif holder_count >= config.min_holder_count:
        score -= 12
    elif holder_count >= 20:
        score -= 18
    else:
        score -= 25

    # Top holder concentration (max 20 pts)
    if top_holder_pct > 50:
        score -= 25
    elif top_holder_pct > 30:
        score -= 18
    elif top_holder_pct > 15:
        score -= 10
    elif top_holder_pct > 10:
        score -= 5

    # Dev wallet (max 15 pts)
    if dev_hold_pct > 20:
        score -= 20
    elif dev_hold_pct > 10:
        score -= 12
    elif dev_hold_pct > 5:
        score -= 5

    # Rugcheck score (lower rugcheck score = safer = less penalty)
    if rugcheck_score >= 80:
        score -= 25
    elif rugcheck_score >= 60:
        score -= 15
    elif rugcheck_score >= 40:
        score -= 8
    elif rugcheck_score >= 20:
        score -= 3

    # Momentum: penalize if dumping
    if price_change_5m is not None:
        if price_change_5m < -20:
            score -= 20
        elif price_change_5m < -10:
            score -= 10
        elif price_change_5m > 10:
            score += 5  # positive momentum bonus

    # Warning count penalty
    score -= min(len(warnings) * 3, 15)

    return max(0, min(100, score))


class Analyzer:
    """Orchestrates all token checks and returns a TokenAnalysis."""

    async def analyze(self, token: TokenLaunch) -> TokenAnalysis:
        warnings: list[str] = []
        mint = token.mint_address

        async with aiohttp.ClientSession() as session:
            # Run independent checks in parallel
            sol_price_task = asyncio.create_task(_get_sol_price(session))
            liquidity_task = asyncio.create_task(_check_liquidity(session, mint))
            holders_task = asyncio.create_task(_check_holders(session, mint))
            rugcheck_task = asyncio.create_task(_check_rugcheck(session, mint))
            dev_task = asyncio.create_task(_check_dev_wallet(session, token.creator_wallet, mint))

            sol_price, liq_data, holder_data, rug_data, dev_data = await asyncio.gather(
                sol_price_task,
                liquidity_task,
                holders_task,
                rugcheck_task,
                dev_task,
                return_exceptions=False,
            )

        liquidity_usd = liq_data["liquidity_usd"]
        price_usd = liq_data["price_usd"]
        market_cap_usd = liq_data["market_cap_usd"]
        volume_24h = liq_data["volume_24h"]
        price_change_5m = liq_data["price_change_5m"]

        holder_count = holder_data["holder_count"]
        top_holder_pct = holder_data["top_holder_pct"]

        rugcheck_score = rug_data["rugcheck_score"]
        is_honeypot = rug_data["is_honeypot"]
        mint_auth = rug_data["mint_authority_enabled"]
        freeze_auth = rug_data["freeze_authority_enabled"]

        dev_hold_pct = dev_data["dev_hold_pct"]

        # Collect warnings
        if liquidity_usd < config.min_liquidity_usd:
            warnings.append(f"Low liquidity: ${liquidity_usd:,.0f}")
        if holder_count < config.min_holder_count:
            warnings.append(f"Low holder count: {holder_count}")
        if top_holder_pct > 15:
            warnings.append(f"Whale concentration: top holder {top_holder_pct:.1f}%")
        if dev_hold_pct > config.max_dev_wallet_pct:
            warnings.append(f"Dev wallet holds {dev_hold_pct:.1f}%")
        if mint_auth:
            warnings.append("Mint authority still enabled")
        if freeze_auth:
            warnings.append("Freeze authority still enabled")
        if is_honeypot:
            warnings.append("Honeypot detected by rugcheck")
        for w in rug_data["rugcheck_warnings"]:
            if w not in warnings:
                warnings.append(w)
        if price_change_5m is not None and price_change_5m < -15:
            warnings.append(f"Price dumping: {price_change_5m:.1f}% in 5m")

        score = _compute_score(
            liquidity_usd=liquidity_usd,
            holder_count=holder_count,
            top_holder_pct=top_holder_pct,
            dev_hold_pct=dev_hold_pct,
            rugcheck_score=rugcheck_score,
            mint_authority=mint_auth,
            freeze_authority=freeze_auth,
            is_honeypot=is_honeypot,
            price_change_5m=price_change_5m,
            warnings=warnings,
        )

        if is_honeypot or mint_auth or freeze_auth:
            recommendation = Recommendation.SKIP
        elif score >= 60:
            recommendation = Recommendation.BUY
        elif score >= 40:
            recommendation = Recommendation.WAIT
        else:
            recommendation = Recommendation.SKIP

        return TokenAnalysis(
            mint_address=mint,
            symbol=token.symbol,
            score=score,
            liquidity_usd=liquidity_usd,
            holder_count=holder_count,
            top_holder_pct=top_holder_pct,
            is_honeypot=is_honeypot,
            rugcheck_score=rugcheck_score,
            warnings=warnings,
            recommendation=recommendation,
            price_usd=price_usd,
            market_cap_usd=market_cap_usd,
            volume_24h=volume_24h,
        )
