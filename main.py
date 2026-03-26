"""
main.py - Cupsy AI Memecoin Trading Bot entry point.

Orchestrates:
- Token scanning (pump.fun + Raydium)
- Token analysis (liquidity, holders, rugcheck)
- AI decision making (Claude)
- Trade execution (Jupiter V6)
- Risk management (take-profit, stop-loss, time exit)
"""

import asyncio
import sys

from config import config
from cupsy import logger
from cupsy.scanner import Scanner
from cupsy.analyzer import Analyzer
from cupsy.ai_brain import AICupsy
from cupsy.trader import Trader
from cupsy.wallet import Wallet
from cupsy.risk_manager import RiskManager


async def main() -> None:
    # --- Startup Banner ---
    wallet_address = None
    wallet = None

    if config.wallet_private_key:
        try:
            wallet = Wallet()
            wallet_address = wallet.public_key
        except Exception as exc:
            logger.warning(f"Wallet load failed: {exc}. Continuing in paper mode only.")

    logger.startup_banner(
        paper_trading=config.paper_trading,
        buy_amount_sol=config.buy_amount_sol,
        max_positions=config.max_positions,
        take_profit_pct=config.take_profit_pct,
        stop_loss_pct=config.stop_loss_pct,
        wallet_address=wallet_address,
    )

    # --- Validate prerequisites ---
    if not config.anthropic_api_key:
        logger.error("ANTHROPIC_API_KEY is required. Set it in your .env file.")
        sys.exit(1)

    if not config.paper_trading and wallet is None:
        logger.error("WALLET_PRIVATE_KEY required for live trading.")
        sys.exit(1)

    # If paper trading and no wallet, create a dummy wallet substitute
    if config.paper_trading and wallet is None:
        logger.warning("No wallet loaded - paper trading with no wallet context.")
        # We still need a Trader; pass None wallet and handle in paper mode
        class _DummyWallet:
            public_key = "PAPER_TRADING_NO_WALLET"
            async def get_sol_balance(self): return 100.0
            async def get_token_balance(self, mint): return 0.0
            def sign_transaction_bytes(self, tx): raise RuntimeError("Paper mode")
            async def send_transaction(self, tx): raise RuntimeError("Paper mode")

        wallet = _DummyWallet()

    # --- Initialize components ---
    scanner = Scanner()
    analyzer = Analyzer()

    try:
        brain = AICupsy()
    except ValueError as exc:
        logger.error(str(exc))
        sys.exit(1)

    trader = Trader(wallet)
    risk_manager = RiskManager(trader)

    # --- Start background risk monitoring ---
    monitor_task = asyncio.create_task(
        risk_manager.monitor_loop(),
        name="risk_monitor",
    )

    logger.info("Cupsy is live. Scanning for new tokens...")

    # --- Main scanning loop ---
    try:
        async for token in scanner.scan():
            asyncio.create_task(
                _process_token(token, analyzer, brain, trader, risk_manager),
                name=f"process_{token.mint_address[:8]}",
            )
    except asyncio.CancelledError:
        logger.info("Scanner cancelled, shutting down.")
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received, shutting down.")
    finally:
        monitor_task.cancel()
        try:
            await asyncio.wait_for(monitor_task, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        logger.info("Cupsy shut down cleanly.")


async def _process_token(token, analyzer, brain, trader, risk_manager) -> None:
    """
    Process a single token through the full analysis and decision pipeline.
    Wrapped in a broad try/except so one bad token never crashes the bot.
    """
    mint = token.mint_address
    symbol = token.symbol or "???"

    try:
        logger.info(f"New token: [{token.source.value}] {token.name} ({symbol}) | {mint}")

        # --- Analyze ---
        analysis = await analyzer.analyze(token)
        logger.info(
            f"Analysis: {symbol} | score={analysis.score}/100 "
            f"| liq=${analysis.liquidity_usd:,.0f} "
            f"| holders={analysis.holder_count} "
            f"| top_holder={analysis.top_holder_pct:.1f}%"
        )

        # --- Hard filters ---
        if analysis.is_honeypot:
            logger.warning(f"SKIP {symbol}: honeypot detected")
            return

        if analysis.score < 40:
            logger.warning(f"SKIP {symbol}: low score ({analysis.score}/100)")
            if analysis.warnings:
                logger.warning(f"  Warnings: {', '.join(analysis.warnings[:3])}")
            return

        # --- AI Decision ---
        decision = await brain.decide(token, analysis)
        logger.info(
            f"AI Decision: {symbol} -> {decision.decision} "
            f"(confidence={decision.confidence}/10) | {decision.reasoning}"
        )

        if decision.decision != "BUY":
            logger.signal(f"SKIP {symbol}: AI said no")
            return

        # --- Position limit check ---
        if risk_manager.position_count >= config.max_positions:
            logger.warning(
                f"SKIP {symbol}: max positions reached "
                f"({risk_manager.position_count}/{config.max_positions})"
            )
            return

        # --- Execute buy ---
        buy_amount = config.buy_amount_sol * (decision.suggested_buy_pct / 100)
        logger.signal(
            f"BUYING {symbol} | amount={buy_amount:.4f} SOL "
            f"| suggested_pct={decision.suggested_buy_pct}%"
        )

        position = await trader.buy(
            mint=mint,
            symbol=symbol,
            amount_sol=buy_amount,
        )

        if position is None:
            logger.error(f"Buy failed for {symbol}")
            return

        risk_manager.add_position(position)
        logger.trade(
            f"Position opened: {symbol} "
            f"| tokens={position.token_amount:,.0f} "
            f"| sol={position.sol_spent:.4f} "
            f"| paper={position.paper}"
        )

    except Exception as exc:
        logger.error(f"Error processing {symbol} ({mint}): {exc}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
