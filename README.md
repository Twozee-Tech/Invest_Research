# AI Investment Orchestrator

LLM-powered autonomous portfolio management. Thirteen virtual portfolios managed by local AI models via llama-swap, tracked in Ghostfolio. Two parallel model families (Qwen3-Next and Nemotron) run identical strategies for A/B comparison. Includes a vertical spreads options engine, intraday momentum cycles, a daily research agent, and a backtesting engine that replays the full LLM decision pipeline on historical data.

Multi-arch Docker image (amd64 + arm64) — runs on standard x86 servers and ARM hardware (Raspberry Pi, Proxmox ARM nodes, etc.).

## Install

### Standard (Linux/macOS — Docker required)

One command installs everything. The interactive installer guides you through configuration.

```bash
curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install.sh | bash
```

**What it does:**
- Configures Ghostfolio and llama-swap connections (interactive prompts)
- Downloads all project files and builds the Docker image
- Installs the `invest` CLI command to `~/.local/bin`

### Proxmox LXC — Native Python (recommended, lighter)

Run on the **Proxmox host shell** — creates a Debian 12 LXC and runs Python + Supervisor directly. No Docker.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install-proxmox-native.sh)"
```

**Resource usage:** ~250 MB RAM idle, ~1.2 GB disk, 1 core
**What it does:**
- Creates an unprivileged Debian 12 LXC (arm64 or amd64)
- Installs Python 3.12 + Supervisor directly (no Docker)
- Clones the repo, creates a venv, installs all dependencies
- Configures Supervisor to manage the scheduler + dashboard processes

Requires a GitHub PAT with `repo` (read) scope (prompted during install).

### Proxmox LXC — Docker variant

Same LXC setup but runs a pre-built Docker image instead of raw Python. Heavier (~420 MB RAM), but no compilation step.

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install-proxmox.sh)"
```

Requires a GitHub PAT with `read:packages` scope.

## Usage

```bash
invest start                   # Start orchestrator + dashboard
invest run-all --dry-run       # Test all accounts (no real trades)
invest run weekly_balanced     # Run single account cycle
invest dashboard               # Open web dashboard
invest logs                    # Follow container logs
invest stop                    # Stop everything
invest rebuild                 # Rebuild Docker image from local files
invest update                  # Re-download from GitHub and rebuild
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
| (portfolio tracking)      |<---|  - Multi-account APScheduler        |
| - REST API                |    |  - 2-pass LLM reasoning             |
| - Performance UI          |    |  - Risk manager (per account)       |
| - Yahoo Finance data      |    |  - Trade executor                   |
|                           |    |  - Audit logger (JSON + SQLite)     |
+---------------------------+    |  - Vertical spreads engine          |
                                 |  - Intraday momentum engine         |
| llama-swap                |    |  - Research agent                   |
| (LLM inference)           |<---|  - Backtest runner                  |
| - Qwen3-Next (default)    |    |                                     |
| - Nemotron (balanced)     |    |  Streamlit Dashboard (:8501)        |
+---------------------------+    |  - Account overview                 |
                                 |  - Decision logs                    |
| yfinance / RSS feeds      |    |  - Options positions                |
| (market data + news)      |<---+  - Backtesting (historical sim)     |
+---------------------------+    +-------------------------------------+
```

## Accounts & Strategies

Thirteen accounts across two parallel model families (Qwen3-Next / Nemotron) for A/B comparison:

| Account | Model | Frequency | Strategy |
|---------|-------|-----------|----------|
| Weekly Balanced | Qwen3-Next | Sunday 20:00 | Core-Satellite (60% ETF + 30% stock + 10% cash) |
| Monthly Value | Qwen3-Next | 1st of month 20:00 | Value investing (40% ETF + 50% stock + 10% cash) |
| Daily Momentum | Qwen3-Next | Mon–Fri 18:00 | Momentum/Swing (20% ETF + 70% stock + 10% cash) |
| Wheel Strategy | Qwen3-Next | Thursday 18:20 | Sell CSP → Sell CC (Wheel), earnings-filtered |
| Vertical Spreads | Qwen3-Next | Thursday 19:00 | Multi-leg spreads (bull/bear call/put, iron condor, butterfly) |
| Intraday Momentum | Qwen3-Next | Mon–Fri 15:00–21:00 (30min) | Short-term momentum, intraday signals |
| Weekly Balanced (Nemotron) | Nemotron | Sunday 20:20 | Core-Satellite |
| Monthly Value (Nemotron) | Nemotron | 1st of month 20:40 | Value investing |
| Daily Momentum (Nemotron) | Nemotron | Mon–Fri 18:20 | Momentum/Swing |
| Wheel Strategy (Nemotron) | Nemotron | Thursday 18:40 | Wheel (CSP → CC) |
| Vertical Spreads (Nemotron) | Nemotron | Thursday 19:20 | Multi-leg spreads |
| Intraday Momentum (Nemotron) | Nemotron | Mon–Fri 15:15–21:15 (30min) | Short-term momentum |
| Daily Research Agent | Qwen3-Next | Mon–Fri 14:00 | Builds dynamic watchlist for other accounts |

Each account starts with $10,000. All tracked in Ghostfolio with separate account IDs.

## Decision Process

### Equity cycles (standard + intraday)

1. **Context Gathering** — portfolio state from Ghostfolio, market data from yfinance (VIX, 10Y yield), technical indicators (SMA20/50/200, RSI-14, MACD, Bollinger Bands), earnings calendar, news filtered by watchlist, previous decision history with live P/L
2. **LLM Pass 1 — Market Analysis** — regime classification (BULL/BEAR/SIDEWAYS/HIGH_VOLATILITY), sector scores (−2 to +2), portfolio health
3. **LLM Pass 2 — Trade Decisions** — specific trades with `stop_loss_pct`, `take_profit_pct`, `time_stop_days`; position sizing verified against dollar limits
4. **Risk Validation** — position limits, cash reserves, liquidity, holding period, correlation warnings, bootstrap mode; stop-loss triggers generate forced sells
5. **Trade Execution** — Ghostfolio orders via REST API
6. **Audit Logging** — full cycle saved as JSON + SQLite summary

### Vertical spreads cycles

Same 6-phase structure with a spreads-specific pipeline:
- **Pass 1** — IV percentile, skew analysis, regime, best spread type recommendation
- **Pass 2** — OPEN_SPREAD / CLOSE / SKIP actions with spread_type, contracts, reason
- **Spreads selector** — Black-Scholes delta targeting for strike selection; supports iron_condor, bull_call, bear_put, bull_put, bear_call, butterfly
- **Spreads risk manager** — max open spreads, cash reserve (40%), earnings blackout, auto-close on DTE ≤ 3, take-profit ≥ 50%, stop-loss ≥ 100% of max loss
- **Position tracker** — SQLite with synthetic Ghostfolio tickers (`SPREAD-SYM-TYPE-DATE-strikes`)

### Wheel strategy cycles

- Runs CSP (Cash Secured Put) on selected stocks until assignment, then switches to CC (Covered Call)
- Earnings blackout prevents opening positions within 7 days of earnings
- Separate decision parser for SELL_CSP / SELL_CC / CLOSE / SKIP / ROLL actions

## Risk Rules

| Rule | Description |
|------|-------------|
| Max position | 15–25% of portfolio per symbol |
| Min cash | 10% reserve (options: 40%) |
| Stop-loss | −8% to −20% per position (triggered automatically) |
| Min liquidity | Avg daily volume > $100K |
| No penny stocks | Price > $5 |
| Max drawdown | −20% triggers forced 50% exposure reduction |
| Min holding | 1–30 days (prevents rapid flipping) |
| Bootstrap mode | If cash > 80%, max trades doubled to deploy capital faster |
| Correlation check | Warning when buying highly correlated pairs (VTI+VOO, QQQ+TQQQ, NVDA+SOXL…) |

## Backtesting

The dashboard includes a backtesting engine that replays the full 2-pass LLM pipeline on historical data:

- **No look-ahead bias** — each week only sees data up to that date
- **Date anonymisation** — LLM receives "Week N" labels instead of calendar dates to reduce temporal bias
- **Same pipeline** — identical prompt_builder, decision_parser, and risk_manager as live trading
- **Metrics** — total return, max drawdown, win rate, benchmark comparison (SPY buy-and-hold)

Run from the Backtesting page in the dashboard, or directly:

```python
from src.backtest.runner import run_backtest
result = run_backtest(account_config, "2024-01-01", "2024-06-30", llm_client)
```

## Project Structure

```
├── install.sh                        # One-line curl installer (builds from source)
├── install-proxmox-native.sh         # Proxmox host installer — native Python in LXC (recommended)
├── install-proxmox.sh                # Proxmox host installer — Docker in LXC variant
├── docker-compose.yml                # Development (builds locally)
├── docker-compose.prod.yml           # Production (pulls ghcr.io image)
├── .github/workflows/
│   └── docker-publish.yml            # Builds linux/amd64 + linux/arm64, pushes to GHCR
├── orchestrator/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── supervisord.conf              # Runs scheduler + dashboard in one container
│   ├── src/
│   │   ├── main.py                   # Entry point + APScheduler
│   │   ├── prompt_builder.py         # Pass 1 & 2 message construction
│   │   ├── decision_parser.py        # Pydantic models + LLM response normalisation
│   │   ├── risk_manager.py           # Hard risk rules + bootstrap + correlation check
│   │   ├── trade_executor.py         # Ghostfolio order creation
│   │   ├── audit_logger.py           # JSON + SQLite audit trail
│   │   ├── market_data.py            # yfinance quotes, history, earnings calendar
│   │   ├── technical_indicators.py   # SMA, RSI, MACD, Bollinger Bands
│   │   ├── news_fetcher.py           # RSS feeds, watchlist-filtered relevance scoring
│   │   ├── portfolio_state.py        # Ghostfolio → PortfolioState aggregation
│   │   ├── ghostfolio_client.py      # Ghostfolio REST client (2-step auth)
│   │   ├── llm_client.py             # OpenAI-compatible LLM client with fallback
│   │   ├── account_manager.py        # Config + Ghostfolio account lifecycle
│   │   ├── research_agent.py         # Dynamic watchlist builder
│   │   ├── options/                  # Options trading subsystem
│   │   │   ├── greeks.py             # Black-Scholes Greeks calculation
│   │   │   ├── data.py               # IV percentile from option chains
│   │   │   ├── positions.py          # OptionsPosition tracker (SQLite)
│   │   │   ├── selector.py           # Wheel: strike + expiry selection
│   │   │   ├── prompt_builder.py     # Wheel: Pass 1 & 2 prompts
│   │   │   ├── decision_parser.py    # Wheel: SELL_CSP/SELL_CC/CLOSE/ROLL models
│   │   │   ├── executor.py           # Wheel: execution + position tracking
│   │   │   ├── risk_manager.py       # Wheel: Greeks-based risk rules
│   │   │   ├── spreads_selector.py   # Spreads: delta-targeted strike selection
│   │   │   ├── spreads_prompt_builder.py  # Spreads: IV skew + spread type prompts
│   │   │   ├── spreads_decision_parser.py # Spreads: OPEN_SPREAD/CLOSE/SKIP models
│   │   │   ├── spreads_executor.py   # Spreads: multi-leg execution
│   │   │   └── spreads_risk_manager.py    # Spreads: auto-close, cash rules
│   │   └── backtest/                 # Historical simulation engine
│   │       ├── runner.py             # Weekly tick loop + metrics
│   │       ├── historical_data.py    # Prefetch + no-lookahead slicing
│   │       └── portfolio_sim.py      # In-memory SimulatedPortfolio
│   ├── dashboard/                    # Streamlit (multiple pages)
│   │   ├── app.py
│   │   └── pages/
│   │       ├── overview.py
│   │       ├── options_positions.py
│   │       ├── options_spreads.py
│   │       └── ...
│   └── tests/                        # 118 unit tests
├── logs/                             # JSON audit logs (one per cycle)
└── data/                             # SQLite summary + config + cache
```

## Development

```bash
cd orchestrator
python3 -m venv .venv && source .venv/bin/activate
pip install poetry && poetry install

# Run tests
.venv/bin/python -m pytest tests/ -v

# Run single cycle locally (dry run)
python -m src.main --once weekly_balanced --dry-run

# Run all accounts once
python -m src.main --all --dry-run

# Start dashboard locally
streamlit run dashboard/app.py
```

## Update

```bash
# Pull new image (Proxmox / prod setup)
docker compose -f docker-compose.prod.yml pull && docker compose -f docker-compose.prod.yml up -d

# Rebuild from source (standard install)
invest update
```

## Uninstall

```bash
invest stop
rm ~/.local/bin/invest
docker rmi invest-orchestrator ghcr.io/twozee-tech/invest-orchestrator
docker volume rm invest_invest_data invest_invest_logs
```

## License

MIT
