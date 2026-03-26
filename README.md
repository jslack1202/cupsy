# Cupsy - AI Memecoin Trading Bot

Cupsy is an autonomous AI-powered Solana memecoin trading bot that uses Claude AI to make buy/sell decisions on newly launched tokens from pump.fun and Raydium.

## Features

- **Real-time scanning** - WebSocket listeners for pump.fun and Raydium new token launches
- **Multi-signal analysis** - Liquidity, holder distribution, rugcheck, dev wallet, momentum
- **Claude AI brain** - Uses Claude claude-sonnet-4-6 with structured outputs for reliable decisions
- **Jupiter V6 execution** - Best-route swap execution with configurable slippage
- **Risk management** - Automated take-profit, stop-loss, and time-based exits
- **Paper trading mode** - Full simulation without real funds
- **Beautiful logging** - Rich terminal output with colored tables for trades

## Project Structure

```
cupsy/
├── main.py              # Orchestrator - runs the full pipeline
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
├── config.py            # Config dataclass loaded from env vars
└── cupsy/
    ├── __init__.py
    ├── scanner.py       # pump.fun + Raydium WebSocket scanner
    ├── analyzer.py      # Token scoring: liquidity, holders, rugcheck
    ├── ai_brain.py      # Claude AI buy/no-buy decisions
    ├── trader.py        # Jupiter DEX swap execution
    ├── risk_manager.py  # Position tracking, TP/SL/time exits
    ├── wallet.py        # Solana wallet management
    └── logger.py        # Rich colored console logging
```

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your keys
```

Required settings:
- `ANTHROPIC_API_KEY` - Your Anthropic API key (Claude AI brain)
- `SOLANA_RPC_URL` - Solana RPC endpoint (Helius recommended)
- `SOLANA_WS_URL` - Solana WebSocket endpoint

For live trading (optional):
- `WALLET_PRIVATE_KEY` - Your Solana wallet private key (base58 encoded)
- `PAPER_TRADING=false`

### 3. Run in paper trading mode (default)

```bash
python main.py
```

Paper trading mode is enabled by default (`PAPER_TRADING=true`). No real funds are used.

### 4. Run with live trading

**WARNING: Live trading involves real financial risk. Only use funds you can afford to lose.**

```bash
PAPER_TRADING=false python main.py
```

## Configuration

| Variable | Default | Description |
|---|---|---|
| `PAPER_TRADING` | `true` | Simulate trades without sending transactions |
| `BUY_AMOUNT_SOL` | `0.1` | SOL to spend per trade |
| `MAX_POSITIONS` | `5` | Maximum concurrent open positions |
| `TAKE_PROFIT_PCT` | `150` | Take profit at +150% |
| `STOP_LOSS_PCT` | `30` | Stop loss at -30% |
| `MAX_SLIPPAGE_BPS` | `300` | Maximum slippage in basis points (3%) |
| `MIN_LIQUIDITY_USD` | `5000` | Minimum liquidity to consider a token |
| `MAX_DEV_WALLET_PCT` | `10` | Skip if dev holds more than this % |
| `MIN_HOLDER_COUNT` | `50` | Minimum number of holders required |

## How It Works

1. **Scanner** connects to pump.fun WebSocket and Solana RPC to detect new token launches
2. **Analyzer** fetches data from DexScreener, rugcheck.xyz, and Solana RPC to score tokens 0-100
3. **AI Brain** (Claude) receives the analysis and makes a final BUY/SKIP decision with reasoning
4. **Trader** executes swaps via Jupiter V6 API (or simulates in paper mode)
5. **Risk Manager** monitors all open positions every 30 seconds and exits on TP/SL/time conditions

## Token Scoring

Tokens are automatically rejected (score = 0) if:
- Honeypot detected
- Mint authority is still enabled
- Freeze authority is still enabled

Score penalties for:
- Low liquidity (below $5,000 USD)
- Low holder count (below 50)
- Whale concentration (single holder >15%)
- High dev wallet holdings (>10%)
- High rugcheck risk score
- Negative price momentum (dumping)

The AI only buys tokens where:
- Score >= 40 (passes hard filter)
- AI confidence >= 7/10

## Disclaimer

This software is for educational and research purposes. Memecoin trading carries extreme financial risk. The authors are not responsible for any financial losses. Never trade with funds you cannot afford to lose.
