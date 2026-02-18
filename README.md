# AI Investment Orchestrator

LLM-powered autonomous portfolio management. Three virtual portfolios ($10k each) managed by local AI models via llama-swap, tracked in Ghostfolio.

## Install

One command installs everything. The interactive installer guides you through configuration.

```bash
curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install.sh | bash
```

[View install.sh source](https://github.com/Twozee-Tech/Invest_Research/blob/main/install.sh)

**What it does:**
- Checks Docker availability
- Configures Ghostfolio and llama-swap connections (interactive prompts)
- Downloads all project files
- Builds the Docker image
- Installs the `invest` CLI command to `~/.local/bin`

No Python installation required on the host - everything runs in Docker.

## Usage

```bash
invest start                   # Start orchestrator + dashboard
invest run-all --dry-run       # Test all accounts (no real trades)
invest run weekly_balanced     # Run single account cycle
invest dashboard               # Open web dashboard
invest logs                    # Follow container logs
invest stop                    # Stop everything
```

Dashboard available at **http://localhost:8501** after `invest start`.

## Requirements

- Docker with Docker Compose
- Ghostfolio instance (external, already running)
- llama-swap instance with LLM models (external, already running)

## Architecture

```
  EXTERNAL SERVICES                     DOCKER (orchestrator)
+---------------------------+    +-------------------------------------+
|                           |    |                                     |
| Ghostfolio                |    |  Orchestrator (Python 3.12)         |
| (portfolio tracking)      |<---|  - Multi-account scheduler          |
| - REST API                |    |  - 2-pass LLM reasoning             |
| - Performance UI          |    |  - Risk manager per account         |
| - Yahoo Finance data      |    |  - Trade executor                   |
|                           |    |  - Audit logger                     |
+---------------------------+    |                                     |
                                 |  Streamlit Dashboard (:8501)        |
| llama-swap                |    |  - Account overview                 |
| (LLM inference)           |<---|  - Decision logs                    |
| - Qwen3-Next (default)    |    |  - Model comparison                 |
| - Nemotron (fallback)     |    |  - Manual trigger / dry-run         |
| - Miro_Thinker (deep)     |    |  - Account management               |
+---------------------------+    +-------------------------------------+
```

## Accounts & Strategies

| Account | Frequency | Model | Strategy | Stop-Loss |
|---------|-----------|-------|----------|-----------|
| Weekly Balanced | Sunday 20:00 | Qwen3-Next | Core-Satellite (60% ETF + 30% stock + 10% cash) | -15% |
| Monthly Value | 1st of month 20:00 | Qwen3-Next | Value investing (40% ETF + 50% stock + 10% cash) | -20% |
| Daily Momentum | Mon-Fri 18:00 | Qwen3-Next | Momentum/Swing (20% ETF + 70% stock + 10% cash) | -8% |

Each account starts with $10,000. All tracked in Ghostfolio with separate account IDs.

## Decision Process

Each cycle runs 3 phases:

1. **Context Gathering** (automated) - portfolio state from Ghostfolio, market data from yfinance, technical indicators (SMA, RSI, MACD, Bollinger), news from RSS feeds, previous decision history
2. **LLM 2-Pass Reasoning** - Pass 1: market analysis (regime, sectors, opportunities, threats). Pass 2: specific trade decisions with theses and position sizing
3. **Validation & Execution** (automated) - risk manager enforces hard rules (position limits, cash reserves, stop-losses, liquidity checks), then executes approved trades via Ghostfolio API

## Risk Rules

| Rule | Description |
|------|-------------|
| Max position | 15-25% per account |
| Min cash | 10% reserve |
| Stop-loss | -8% to -20% per account |
| Min liquidity | Avg daily volume > $100K |
| No penny stocks | Price > $5 |
| Max drawdown | -20% triggers forced 50% reduction |
| Min holding | 1-30 days per account |

## Project Structure

```
├── install.sh                 # One-line curl installer
├── docker-compose.yml
├── config.yaml                # Multi-account config
├── invest_app.md              # Full documentation
├── orchestrator/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── supervisord.conf
│   ├── src/                   # 13 Python modules
│   │   ├── main.py            # Entry point + APScheduler
│   │   ├── ghostfolio_client.py
│   │   ├── llm_client.py
│   │   ├── market_data.py
│   │   ├── technical_indicators.py
│   │   ├── portfolio_state.py
│   │   ├── news_fetcher.py
│   │   ├── account_manager.py
│   │   ├── prompt_builder.py
│   │   ├── decision_parser.py
│   │   ├── risk_manager.py
│   │   ├── trade_executor.py
│   │   └── audit_logger.py
│   ├── dashboard/             # Streamlit (7 pages)
│   └── tests/                 # 57 tests (unit + LLM integration)
├── logs/                      # JSON audit logs per cycle
└── data/                      # SQLite summary + cache
```

## Development

```bash
cd orchestrator
python3 -m venv .venv && source .venv/bin/activate
pip install httpx openai yfinance pandas ta pydantic pydantic-settings \
    apscheduler structlog feedparser pyyaml streamlit plotly pytest

# Run tests
python -m pytest tests/ -v

# Run single cycle locally
python -m src.main --once weekly_balanced --dry-run

# Start dashboard locally
streamlit run dashboard/app.py
```

## Reinstall / Update

```bash
curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install.sh | bash
```

## Uninstall

```bash
invest stop
rm ~/.local/bin/invest
docker rmi investment-orchestrator
# Optionally remove install directory:
rm -rf ~/invest-orchestrator
```

## License

MIT
