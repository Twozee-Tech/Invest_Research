# AI Investment Orchestrator

LLM-powered autonomous portfolio management. Four virtual portfolios ($10k each) managed by local AI models via llama-swap, tracked in Ghostfolio. Includes a backtesting engine that replays the full LLM decision pipeline on historical data.

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

No Python installation required on the host — everything runs in Docker.

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
+---------------------------+    |  - Options spreads engine           |
                                 |  - Backtest runner                  |
| llama-swap                |    |                                     |
| (LLM inference)           |<---|  Streamlit Dashboard (:8501)        |
| - Qwen3-Next (default)    |    |  - Account overview                 |
| - Nemotron (balanced)     |    |  - Decision logs                    |
| - Miro_Thinker (deep)     |    |  - Options positions                |
+---------------------------+    |  - Backtesting (historical sim)     |
                                 |  - Manual trigger / dry-run         |
| yfinance / RSS feeds      |    |  - Account management               |
| (market data + news)      |<---+                                     |
+---------------------------+    +-------------------------------------+
```

## Accounts & Strategies

| Account | Frequency | Strategy | Stop-Loss | Min Holding |
|---------|-----------|----------|-----------|-------------|
| Weekly Balanced | Sunday 20:00 | Core-Satellite (60% ETF + 30% stock + 10% cash) | -15% | 14 days |
| Monthly Value | 1st of month 20:00 | Value investing (40% ETF + 50% stock + 10% cash) | -20% | 30 days |
| Daily Momentum | Mon–Fri 18:00 | Momentum/Swing (20% ETF + 70% stock + 10% cash) | -8% | 1 day |
| Options Spreads | Mon/Wed/Fri 19:00 | Vertical spreads (bull/bear call/put, delta-neutral) | 50% of max loss | DTE-based |

Each account starts with $10,000. All tracked in Ghostfolio with separate account IDs.

## Decision Process

Each equity cycle runs 6 phases:

1. **Context Gathering** — portfolio state from Ghostfolio, market data from yfinance (including VIX + 10Y yield), technical indicators (SMA20/50/200, RSI-14, MACD, Bollinger Bands), upcoming earnings calendar, news filtered by account watchlist, previous decision history with live P/L
2. **LLM Pass 1 — Market Analysis** — regime classification (BULL/BEAR/SIDEWAYS/HIGH_VOLATILITY), sector analysis with numeric score (−2 to +2), portfolio health, opportunities and threats
3. **LLM Pass 2 — Trade Decisions** — specific trades with `stop_loss_pct`, `take_profit_pct`, `time_stop_days` (structured percentage-based fields, no hallucinated price levels), position sizing verified against explicit dollar limits
4. **Risk Validation** — hard rules enforced (position limits, cash reserves, liquidity, holding period, correlation warnings, bootstrap mode). Stop-loss triggers generate forced sells automatically
5. **Trade Execution** — orders created in Ghostfolio via REST API; portfolio state estimated from executed results
6. **Audit Logging** — full cycle saved as JSON + SQLite summary (pass 1/2 messages, responses, risk modifications, executed trades)

Options cycles additionally compute IV percentiles, portfolio Greeks (Δ/Θ/ν), and use a separate decision parser for open/close/roll actions.

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
- **Configurable** — any account config, any date range, adjustable starting capital

Run from the Backtesting page in the dashboard, or directly:

```python
from src.backtest.runner import run_backtest
result = run_backtest(account_config, "2024-01-01", "2024-06-30", llm_client)
```

## Project Structure

```
├── install.sh                     # One-line curl installer
├── docker-compose.yml
├── orchestrator/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── supervisord.conf           # Runs scheduler + dashboard in one container
│   ├── src/
│   │   ├── main.py                # Entry point + APScheduler
│   │   ├── prompt_builder.py      # Pass 1 & 2 message construction
│   │   ├── decision_parser.py     # Pydantic models + LLM response normalisation
│   │   ├── risk_manager.py        # Hard risk rules + bootstrap + correlation check
│   │   ├── trade_executor.py      # Ghostfolio order creation
│   │   ├── audit_logger.py        # JSON + SQLite audit trail
│   │   ├── market_data.py         # yfinance quotes, history, earnings calendar
│   │   ├── technical_indicators.py # SMA, RSI, MACD, Bollinger Bands
│   │   ├── news_fetcher.py        # RSS feeds, watchlist-filtered relevance scoring
│   │   ├── portfolio_state.py     # Ghostfolio → PortfolioState aggregation
│   │   ├── ghostfolio_client.py   # Ghostfolio REST client (2-step auth)
│   │   ├── llm_client.py          # OpenAI-compatible LLM client with fallback
│   │   ├── account_manager.py     # Config + Ghostfolio account lifecycle
│   │   ├── options/               # Options spreads subsystem
│   │   │   ├── prompt_builder.py  # Options-specific Pass 1 & 2 prompts
│   │   │   ├── decision_parser.py # open/close/roll decision models
│   │   │   ├── executor.py        # Spread execution + position tracking
│   │   │   ├── risk_manager.py    # Greeks-based risk rules
│   │   │   ├── positions.py       # OptionsPosition tracker (SQLite)
│   │   │   ├── greeks.py          # Black-Scholes Greeks calculation
│   │   │   ├── selector.py        # Strike + expiry selection
│   │   │   └── data.py            # IV percentile from option chains
│   │   └── backtest/              # Historical simulation engine
│   │       ├── runner.py          # Weekly tick loop + metrics
│   │       ├── historical_data.py # Prefetch + no-lookahead slicing
│   │       └── portfolio_sim.py   # In-memory SimulatedPortfolio
│   ├── dashboard/                 # Streamlit (8 pages)
│   │   ├── app.py
│   │   └── pages/
│   │       ├── overview.py
│   │       ├── logs.py
│   │       ├── accounts.py
│   │       ├── manual_run.py
│   │       ├── options_positions.py
│   │       └── backtesting.py
│   └── tests/                     # Unit + integration tests
├── logs/                          # JSON audit logs (one per cycle)
└── data/                          # SQLite summary + config + cache
```

## Development

```bash
cd orchestrator
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run single cycle locally (dry run)
python -m src.main --once weekly_balanced --dry-run

# Run all accounts once
python -m src.main --all --dry-run

# Start dashboard locally
streamlit run dashboard/app.py
```

## Reinstall / Update

```bash
# Re-download from GitHub and rebuild
invest update

# Or re-run the full installer
curl -fsSL https://raw.githubusercontent.com/Twozee-Tech/Invest_Research/main/install.sh | bash
```

## Uninstall

```bash
invest stop
rm ~/.local/bin/invest
docker rmi invest-orchestrator
docker volume rm invest_invest_data invest_invest_logs
rm -rf ~/Projects/invest
```

## License

MIT
