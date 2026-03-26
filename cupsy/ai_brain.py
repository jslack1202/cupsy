"""
ai_brain.py - Claude AI-powered trading decision engine for Cupsy.

Uses Claude claude-sonnet-4-6 with tool_use for structured, reliable output parsing.
Cupsy persona: elite memecoin trader - selective, data-driven, confident.
"""

import json
from dataclasses import dataclass
from typing import Optional

import anthropic

from config import config
from cupsy import logger
from cupsy.scanner import TokenLaunch
from cupsy.analyzer import TokenAnalysis

_SYSTEM_PROMPT = """You are Cupsy, an elite AI memecoin trader operating on Solana.

Your trading philosophy:
- You are EXTREMELY selective. You reject 19 out of every 20 tokens you see.
- You never FOMO. If you missed the initial pump, you pass.
- You look for: healthy holder distribution, good liquidity, zero red flags, organic momentum.
- You instantly reject anything with: mint authority enabled, freeze authority, honeypot signals, dev holding >10%, single whale >30%.
- You move fast but with conviction. Brief, sharp analysis only.
- Confidence scale: 1-3 = weak signal (SKIP), 4-6 = possible (lean SKIP unless strong data), 7-8 = strong signal (BUY), 9-10 = exceptional (BUY with max size).

Your personality: concise, decisive, data-driven. No fluff. One or two sentences max for reasoning.

When given token analysis data, call the make_trading_decision tool with your decision."""

_TOOL_DEFINITION = {
    "name": "make_trading_decision",
    "description": "Record the final trading decision for a memecoin token.",
    "input_schema": {
        "type": "object",
        "properties": {
            "decision": {
                "type": "string",
                "enum": ["BUY", "SKIP"],
                "description": "Whether to buy this token or skip it.",
            },
            "confidence": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Confidence score 1-10. Only BUY with confidence >= 7.",
            },
            "reasoning": {
                "type": "string",
                "description": "1-2 sentence sharp explanation of the decision.",
            },
            "suggested_buy_pct": {
                "type": "integer",
                "minimum": 50,
                "maximum": 150,
                "description": "Percentage of standard buy amount (50-150%). Use 100 for normal confidence, 150 for exceptional, 50 for cautious.",
            },
        },
        "required": ["decision", "confidence", "reasoning", "suggested_buy_pct"],
    },
}


@dataclass
class AIDecision:
    decision: str          # "BUY" or "SKIP"
    confidence: int        # 1-10
    reasoning: str
    suggested_buy_pct: int # 50-150
    raw_response: Optional[str] = None


def _format_analysis_prompt(token: TokenLaunch, analysis: TokenAnalysis) -> str:
    """Build a compact but information-rich prompt from the analysis data."""
    lines = [
        f"Token: {token.name} ({token.symbol})",
        f"Mint: {token.mint_address}",
        f"Source: {token.source.value}",
        f"",
        f"=== ANALYSIS SCORE: {analysis.score}/100 ===",
        f"Liquidity USD:     ${analysis.liquidity_usd:,.0f}",
        f"Market Cap:        ${analysis.market_cap_usd:,.0f}",
        f"24h Volume:        ${analysis.volume_24h:,.0f}",
        f"Price USD:         ${analysis.price_usd:.8f}",
        f"Holder Count:      {analysis.holder_count}",
        f"Top Holder %:      {analysis.top_holder_pct:.1f}%",
        f"Is Honeypot:       {analysis.is_honeypot}",
        f"RugCheck Score:    {analysis.rugcheck_score}/100 (higher = riskier)",
        f"",
    ]
    if analysis.warnings:
        lines.append("=== WARNINGS ===")
        for w in analysis.warnings:
            lines.append(f"  - {w}")
        lines.append("")

    lines.append(f"Analyzer Recommendation: {analysis.recommendation.value}")
    return "\n".join(lines)


class AICupsy:
    """AI brain that makes BUY/SKIP decisions using Claude."""

    def __init__(self):
        if not config.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set. AI brain cannot function.")
        self._client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    async def decide(self, token: TokenLaunch, analysis: TokenAnalysis) -> AIDecision:
        """
        Ask Claude to evaluate the token and return a structured trading decision.
        Falls back to a conservative SKIP if the API call fails.
        """
        prompt = _format_analysis_prompt(token, analysis)
        logger.info(f"AI Brain: evaluating {token.symbol} (score={analysis.score})")

        try:
            response = await self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=512,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_DEFINITION],
                tool_choice={"type": "tool", "name": "make_trading_decision"},
                messages=[
                    {"role": "user", "content": prompt},
                ],
            )

            # Extract tool_use block
            tool_block = None
            for block in response.content:
                if block.type == "tool_use" and block.name == "make_trading_decision":
                    tool_block = block
                    break

            if not tool_block:
                logger.warning("AI Brain: no tool_use block in response, defaulting to SKIP")
                return _fallback_decision("No tool_use block in response")

            inp = tool_block.input
            decision = str(inp.get("decision", "SKIP")).upper()
            if decision not in ("BUY", "SKIP"):
                decision = "SKIP"

            confidence = int(inp.get("confidence", 5))
            confidence = max(1, min(10, confidence))

            # Safety gate: only allow BUY if confidence >= 7
            if decision == "BUY" and confidence < 7:
                decision = "SKIP"

            suggested_buy_pct = int(inp.get("suggested_buy_pct", 100))
            suggested_buy_pct = max(50, min(150, suggested_buy_pct))

            reasoning = str(inp.get("reasoning", ""))

            return AIDecision(
                decision=decision,
                confidence=confidence,
                reasoning=reasoning,
                suggested_buy_pct=suggested_buy_pct,
                raw_response=json.dumps(inp),
            )

        except anthropic.RateLimitError:
            logger.warning("AI Brain: rate limited by Anthropic API, defaulting to SKIP")
            return _fallback_decision("Rate limited")
        except anthropic.APIError as exc:
            logger.error(f"AI Brain: API error: {exc}")
            return _fallback_decision(f"API error: {exc}")
        except Exception as exc:
            logger.error(f"AI Brain: unexpected error: {exc}")
            return _fallback_decision(f"Unexpected error: {exc}")


def _fallback_decision(reason: str) -> AIDecision:
    """Return a safe SKIP decision when the AI call fails."""
    return AIDecision(
        decision="SKIP",
        confidence=1,
        reasoning=f"AI unavailable: {reason}. Defaulting to SKIP for safety.",
        suggested_buy_pct=100,
        raw_response=None,
    )
