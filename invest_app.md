# AI Investment Simulator - Documentation

## Overview

Autonomous AI investment system where a local LLM manages multiple virtual portfolios
($10,000 each, US stocks + ETFs). Each portfolio has its own strategy, decision frequency,
and LLM model. The system runs unattended for months, making decisions and tracking
performance through Ghostfolio.

## Architecture

```
  EXTERNAL SERVICES                     DOCKER COMPOSE
  (already running)                     (orchestrator + dashboard)
+---------------------------+    +-------------------------------------+
|                           |    |                                     |
| Ghostfolio                |    |  Orchestrator (Python 3.12)         |
| 192.168.0.12:3333         |<---|  - Multi-account scheduler          |
| - 3x accounts ($10k)     |    |  - 2-pass LLM reasoning             |
| - Portfolio UI            |    |  - Risk manager per account         |
| - Performance tracking    |    |  - Trade executor                   |
|                           |    |  - Audit logger                     |
+---------------------------+    |                                     |
                                 |  Web Dashboard (Streamlit :8501)    |
| llama-swap                |    |  - Account overview                 |
| 192.168.0.169:8080/v1     |<---|  - Decision logs                    |
| - Nemotron (primary)      |    |  - Performance comparison           |
| - Qwen3-Next (fallback)   |    |  - Manual trigger / override        |
| - Miro_Thinker (deep)     |    |  - Account management               |
+---------------------------+    +-------------------------------------+
                                          |
                                 +-------------------------------------+
                                 |  Market Data + Logs                 |
                                 |  yfinance / RSS / SQLite / JSON     |
                                 +-------------------------------------+
```

## External Services

### Ghostfolio (192.168.0.12:3333)
Portfolio tracking application providing:
- REST API for order management (`POST /api/v1/order`)
- Performance tracking with S&P 500 benchmark
- Portfolio X-Ray (concentration risk analysis)
- Web UI for visual portfolio overview
- Yahoo Finance as data source for US stocks + ETFs

Authentication: 2-step (access token -> JWT via `POST /api/v1/auth/anonymous`)

### llama-swap (192.168.0.169:8080/v1)
Local LLM inference server (128 GB VRAM) with automatic model swapping:

| Model | Role | Strengths |
|-------|------|-----------|
| **Nemotron** | Primary | Strong reasoning, good financial analysis |
| **Qwen3-Next** | Secondary/Fallback | Good structured JSON output |
| **Miro_Thinker** | Deep analysis | Thinking model for quarterly reviews |

Model selection is per-request via the `model` field. No restart needed.

## Accounts & Strategies

| Account | Frequency | Model | Strategy | Stop-Loss |
|---------|-----------|-------|----------|-----------|
| Weekly Balanced | Sunday 20:00 | Nemotron | Core-Satellite (60% ETF + 30% stock + 10% cash) | -15% |
| Monthly Value | 1st of month 20:00 | Nemotron | Value investing (40% ETF + 50% stock + 10% cash) | -20% |
| Daily Momentum | Mon-Fri 18:00 | Qwen3-Next | Momentum/Swing (20% ETF + 70% stock + 10% cash) | -8% |

Each account starts with $10,000. All tracked in Ghostfolio with separate account IDs.

## Decision Process (3 Phases)

### Phase 1: Context Gathering (automated, no LLM)
- **Portfolio state** from Ghostfolio API (positions, P/L, cash, history)
- **Market data** from yfinance (prices, PE, market cap, 52-week range, dividends)
- **Technical indicators** computed locally (SMA 20/50/200, RSI 14, MACD, Bollinger Bands, volume ratio)
- **Financial news** from RSS feeds (CNBC, Reuters, Yahoo Finance, MarketWatch)
- **Decision history** from audit logs (last 4 cycles for context)

### Phase 2: LLM Reasoning (2-pass approach)

**Pass 1 - Analysis:** Model receives all data and produces a market analysis:
- Market regime classification (BULL_TREND / BEAR_TREND / SIDEWAYS / HIGH_VOLATILITY)
- Sector analysis (overweight/neutral/underweight per sector)
- Portfolio health assessment (diversification, risk level, issues)
- Opportunities and threats identification

**Pass 2 - Decision:** Model receives the analysis + portfolio state + strategy rules:
- Specific trade actions (BUY/SELL with amounts and theses)
- Portfolio outlook and confidence score
- Next cycle focus areas

Both passes require structured JSON output. If the primary model produces invalid JSON,
the system falls back to the configured fallback model.

### Phase 3: Validation & Execution (automated, no LLM)

**Risk Manager** applies hard rules (per-account):
1. Stop-loss check (before model decisions) - forced SELL if triggered
2. Portfolio drawdown check (-20% -> force 50% exposure reduction)
3. Per-action validation:
   - Position size <= max_position_pct
   - Cash after BUY >= min_cash_pct
   - Trades count <= max_trades_per_cycle (lowest urgency dropped first)
   - Average daily volume > $100K
   - Price > $5 (no penny stocks)
   - Holding period >= min_holding_days for SELL

**Trade Executor** creates orders in Ghostfolio:
1. Get current price from yfinance
2. Calculate quantity = amount_usd / price
3. POST order to Ghostfolio API
4. Verify order appears in Ghostfolio

**Audit Logger** records full cycle:
- JSON log file per cycle (`logs/YYYY-MM-DD_account_HHMMSS.json`)
- SQLite summary for dashboard queries (`data/audit.db`)
- Full prompts, responses, risk modifications, trades, portfolio snapshots

## Model Memory

The LLM has no memory between cycles. Instead, the orchestrator injects the last 4
decision cycles into the prompt, showing:
- Previous outlook and confidence
- Actions taken and their theses
- Subsequent performance results

This gives the model context about its own decisions without fine-tuning.

## Project Structure

```
investment/
├── invest_app.md              # This documentation
├── docker-compose.yml         # Orchestrator container
├── config.yaml                # Multi-account config
├── .env.example               # Environment template
├── orchestrator/
│   ├── Dockerfile
│   ├── pyproject.toml         # Python dependencies (Poetry)
│   ├── supervisord.conf       # Runs scheduler + Streamlit together
│   ├── src/
│   │   ├── main.py            # Entry point + APScheduler
│   │   ├── ghostfolio_client.py  # Ghostfolio REST API (2-step auth)
│   │   ├── llm_client.py      # llama-swap OpenAI-compatible client
│   │   ├── market_data.py     # yfinance quotes + history + caching
│   │   ├── technical_indicators.py  # SMA, RSI, MACD, Bollinger via `ta`
│   │   ├── portfolio_state.py # Portfolio state from Ghostfolio
│   │   ├── news_fetcher.py    # RSS feeds + relevance filtering
│   │   ├── account_manager.py # Account lifecycle management
│   │   ├── prompt_builder.py  # 2-pass prompt construction
│   │   ├── decision_parser.py # JSON parse + Pydantic validation
│   │   ├── risk_manager.py    # Per-account risk rules
│   │   ├── trade_executor.py  # Ghostfolio order creation
│   │   └── audit_logger.py    # JSON logs + SQLite summary
│   ├── dashboard/
│   │   ├── app.py             # Streamlit entry point
│   │   ├── pages/
│   │   │   ├── overview.py        # Account cards + performance
│   │   │   ├── account_detail.py  # Positions + decision history
│   │   │   ├── run_control.py     # Manual trigger + dry-run
│   │   │   ├── model_compare.py   # Model performance comparison
│   │   │   ├── audit_logs.py      # Full prompt/response viewer
│   │   │   ├── account_management.py  # Create/edit/delete accounts
│   │   │   └── settings.py        # Global config editor
│   │   └── components/
│   │       └── charts.py      # Performance charts (Plotly)
│   └── tests/
│       ├── test_risk_manager.py
│       ├── test_decision_parser.py
│       ├── test_trade_executor.py
│       └── test_prompt_builder.py
├── logs/                      # JSON logs per cycle
└── data/                      # Market data cache + audit.db
```

## Dependencies

### Python (Poetry)
- `httpx` - async HTTP client
- `openai` - OpenAI-compatible LLM client
- `yfinance` - Yahoo Finance market data
- `pandas` + `ta` - technical indicators
- `pydantic` - data validation
- `apscheduler` - cron scheduling
- `structlog` - structured logging
- `feedparser` - RSS news parsing
- `streamlit` + `plotly` - dashboard

## Running

### Docker (production)
```bash
# Set up environment
cp .env.example .env
# Edit .env with your Ghostfolio access token

# Start
docker compose up -d

# Dashboard available at http://localhost:8501
```

### Local development
```bash
cd orchestrator
poetry install

# Run single cycle (dry-run)
python -m src.main --once weekly_balanced --dry-run

# Run single cycle (live)
python -m src.main --once weekly_balanced

# Run all accounts once
python -m src.main --all --dry-run

# Start scheduler (production mode)
python -m src.main

# Start dashboard separately
streamlit run dashboard/app.py
```

### CLI Options
| Flag | Description |
|------|-------------|
| `--once <key>` | Run one cycle for account, then exit |
| `--all` | Run all accounts once, then exit |
| `--dry-run` | Don't execute real trades |
| `--config <path>` | Config file path (default: config.yaml) |

## Configuration

### config.yaml
Defines accounts, strategies, risk profiles, and watchlists. Changes take effect
on the next cycle (config is reloaded before each run).

### Environment Variables
| Variable | Description |
|----------|-------------|
| `GHOSTFOLIO_URL` | Ghostfolio API URL |
| `GHOSTFOLIO_ACCESS_TOKEN` | Ghostfolio access token |
| `LLM_BASE_URL` | llama-swap OpenAI endpoint |
| `INITIAL_BUDGET` | Default starting balance |
| `LOG_LEVEL` | Logging level (INFO/DEBUG) |

## Dashboard Pages

1. **Overview** - Account cards with value, P/L, next run time, performance chart
2. **Account Detail** - Positions, decision history, risk profile, watchlist
3. **Run Control** - Manual trigger, dry-run, run all
4. **Model Comparison** - Per-model stats (cycles, confidence, success rate, P/L)
5. **Audit Logs** - Full prompt/response viewer with date/account filters
6. **Account Management** - Create/edit/delete accounts, change models, update watchlists
7. **Settings** - Connection URLs, defaults, cache TTLs, environment variables

## Risk Management Rules

### Global (all accounts)
- Minimum liquidity: avg daily volume > $100K
- No penny stocks: price > $5
- Max portfolio drawdown: -20% triggers forced 50% exposure reduction
- US-listed stocks only (NYSE/NASDAQ)

### Per-account (configurable)
- Max single position size
- Minimum cash reserve
- Maximum trades per cycle
- Stop-loss threshold
- Minimum holding period
- Maximum sector exposure

## Verification

### Smoke test
```bash
python -m src.main --once weekly_balanced --dry-run
```
Verifies full cycle: data gathering -> LLM analysis -> decision -> risk check -> dry trade.

### Risk manager test
```bash
cd orchestrator && poetry run pytest tests/test_risk_manager.py -v
```

### Full test suite
```bash
cd orchestrator && poetry run pytest -v
```

### Integration test
Run a live cycle and verify the transaction appears in Ghostfolio UI at `http://192.168.0.12:3333`.

## Inspirations & Patterns

- **Stotra (Mubelotix/stotra)**: Liquidity filtering ($100K min volume), weighted average cost basis, data fallback chains, cache with TTL
- **AI Financial Simulator (Prabhakar9611)**: Separation of engine from UI, JSON audit trail for every decision
