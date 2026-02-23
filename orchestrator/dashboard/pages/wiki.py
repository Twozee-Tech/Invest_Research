"""Wiki / documentation page."""

import streamlit as st
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

st.title("Wiki — How the Orchestrator Works")

st.markdown("""
This page explains the system architecture, decision processes, and how to read
what the agents produce.
""")

tab_overview, tab_cycle, tab_accounts, tab_outputs, tab_risk, tab_glossary = st.tabs([
    "System Overview",
    "Decision Cycle",
    "Account Types",
    "Reading Outputs",
    "Risk Manager",
    "Glossary",
])


# ═══════════════════════════════════════════════════════════════════════════════
with tab_overview:
    st.header("System Overview")

    st.markdown("""
    The AI Investment Orchestrator is an autonomous portfolio manager that runs
    locally on a server. It uses a local LLM (Large Language Model) to analyse
    markets and decide trades, then executes them via the Ghostfolio API.

    ### Architecture

    ```
    config.yaml
        │
        ├── Scheduler (APScheduler)
        │       └── runs each account on its own cron schedule
        │
        ├── Orchestrator
        │       ├── MarketDataProvider  ← Yahoo Finance (prices, technicals)
        │       ├── NewsFetcher         ← RSS feeds (financial + geopolitical)
        │       ├── WatchlistManager    ← core symbols + screeners + LLM suggestions
        │       ├── LLMClient           ← local llama-swap server
        │       ├── RiskManager         ← validates every trade before execution
        │       ├── TradeExecutor       ← submits orders to Ghostfolio
        │       └── AuditLogger         ← logs every cycle to JSON + SQLite
        │
        ├── ResearchAgent (14:00 CET daily)
        │       └── writes data/daily_research.json
        │               └── read by all accounts at their next cycle
        │
        └── Ghostfolio (192.168.0.12:3333)
                └── tracks positions, prices, P&L
    ```

    ### Data flow per cycle

    ```
    Market data + News + Research brief
            │
            ▼
        LLM Pass 1 (analysis)
            │  "What is the market doing? Which symbols look good?"
            ▼
        LLM Pass 2 (decision)
            │  "Given the analysis, what should I BUY / SELL / HOLD?"
            ▼
        Risk Manager
            │  "Is this trade safe? Does it fit the rules?"
            ▼
        Trade Executor
            │  "Execute approved trades via Ghostfolio"
            ▼
        Audit Logger
           "Save everything for review"
    ```

    ### Infrastructure

    | Component | Location | Port |
    |-----------|----------|------|
    | Orchestrator | Docker container `invest-orchestrator-1` | — |
    | Ghostfolio (portfolio tracker) | 192.168.0.12 | 3333 |
    | LLM server (llama-swap) | 192.168.0.169 | 8080 |
    | Dashboard (this UI) | 192.168.0.169 | 8501 |
    """)


# ═══════════════════════════════════════════════════════════════════════════════
with tab_cycle:
    st.header("Decision Cycle")

    st.subheader("Standard cycle (weekly / daily accounts)")
    st.markdown("""
    Runs on each account's cron schedule (e.g. Sunday 20:00 CET for Weekly Balanced).

    #### Phase 1 — Gather context
    - **Portfolio state**: fetched live from Ghostfolio (positions, cash, P&L)
    - **Market data**: prices, 52-week range, P/E, dividend yield (Yahoo Finance)
    - **Technical indicators**: RSI-14, MACD, SMA-20/50, Bollinger Bands
    - **News**: up to 10 relevant articles from RSS feeds
    - **Fundamentals**: earnings history, analyst consensus, revenue growth
    - **Earnings calendar**: upcoming earnings dates for watchlist symbols
    - **Research brief**: today's pre-market analysis from the Research Agent
    - **Decision history**: last 4 cycles (with P&L results) for LLM memory

    #### Phase 2 — LLM Pass 1 (Market Analysis)
    The LLM acts as a **senior analyst**. It receives all context and returns a
    structured JSON with:
    - `market_regime` — BULL_TREND / BEAR_TREND / SIDEWAYS / HIGH_VOLATILITY
    - `sector_analysis` — rating per sector (OVERWEIGHT / NEUTRAL / UNDERWEIGHT)
    - `portfolio_health` — diversification, risk level, issues
    - `opportunities` — symbols worth looking at
    - `threats` — macro or specific risks

    > The LLM is explicitly told: **do NOT make trades in Pass 1, only analyse.**

    #### Phase 3 — LLM Pass 2 (Trading Decision)
    The LLM acts as a **portfolio manager**. It receives the Pass 1 analysis and
    decides actual trades:
    - `actions` — list of BUY / SELL instructions with amount, thesis, stop-loss
    - `portfolio_outlook` — BULLISH / CAUTIOUSLY_BULLISH / NEUTRAL / etc.
    - `confidence` — 0.0 to 1.0
    - `suggest_symbols` — up to 5 tickers to add to the watchlist next cycle

    #### Phase 4 — Risk Validation
    Every action from Pass 2 goes through the Risk Manager before execution.
    Trades can be **approved**, **rejected**, or **modified**. See the Risk Manager tab.

    #### Phase 5 — Trade Execution
    Approved trades are submitted to Ghostfolio as orders. After execution:
    - Cash balance is updated in Ghostfolio (`PUT /api/v1/account/{id}`)
    - LLM-suggested symbols are saved for the next cycle's watchlist

    #### Phase 6 — Audit Logging
    The full cycle is saved to:
    - `logs/YYYY-MM-DD_{account}_{time}.json` — complete cycle with all prompts, responses, trades
    - `data/audit.db` (SQLite) — summary row for dashboard display
    """)

    st.divider()

    st.subheader("Intraday cycle")
    st.markdown("""
    Runs every 30 minutes during market hours (e.g. 09:00–16:00 ET).
    Has an extra **Pass 0 anti-overtrade filter** to avoid unnecessary trades.

    #### Pass 0 — Scan
    A lightweight LLM call (~200 tokens) that compares current prices to the
    previous cycle's prices. Returns:
    - `HOLD` — nothing significant changed, skip the full cycle (saves tokens + avoids overtrading)
    - `ACT` — something changed enough to warrant a full Pass 1 + Pass 2

    If `HOLD`, only a minimal log entry is written. No trades, no further LLM calls.

    #### Cost filter
    After risk validation, trades are additionally filtered by a **cost breakeven**
    check: the expected profit must exceed `multiplier × transaction_fee`.
    This prevents churning on tiny moves that would be eaten by commissions.
    """)

    st.divider()

    st.subheader("Options cycle (Wheel Strategy)")
    st.markdown("""
    Runs on its own schedule for accounts with `strategy: wheel`.

    #### The Wheel Strategy
    1. **Sell Cash-Secured Put (CSP)** on a stock you'd be happy to own.
       Collect premium. If the stock drops below strike → you get assigned (own the stock).
    2. **If assigned: Sell Covered Call (CC)** above your cost basis.
       Collect more premium. If the stock rises above strike → called away (sell stock at profit).
    3. **Repeat** — keep collecting premium on both sides.

    #### Options Pass 1 — IV Analysis
    LLM analyses:
    - IV percentile (is implied volatility high enough to sell premium?)
    - Per-symbol suitability (support levels, earnings proximity, wheel suitability)
    - Portfolio health (open CSPs, open CCs, cash deployed)

    #### Options Pass 2 — Wheel Actions
    LLM decides:
    - `SELL_CSP` — open a new cash-secured put
    - `SELL_CC` — open a covered call on an assigned position
    - `CLOSE` — buy back an existing position early (e.g. 50%+ premium captured)
    - `SKIP` — do nothing for a symbol this cycle

    > The LLM picks **symbol and action only**. The system automatically selects
    > the exact strike (target delta ~0.30 for CSP, ~0.25 for CC) and expiration
    > date (target DTE 30-45 for CSP, 14-30 for CC).

    #### Auto-close rules (independent of LLM)
    The Options Risk Manager automatically closes positions when:
    - **DTE ≤ 3** — avoid last-minute assignment / exercise risk
    - **≥ 50% of premium captured** — take profit early (standard wheel practice)
    """)

    st.divider()

    st.subheader("Research Agent cycle (daily, 14:00 CET)")
    st.markdown("""
    Runs once a day before NYSE opens (NYSE opens 15:30 CET).

    1. Fetches **40 full articles** from 12 RSS feeds (financial + geopolitical)
    2. Runs **6 Yahoo Finance screeners** (day gainers/losers, most actives,
       growth tech, undervalued large caps, most shorted)
    3. Fetches **market overview** (indices, VIX, TNX)
    4. Sends everything (~30k tokens) to Nemotron for synthesis
    5. Saves `data/daily_research.json`

    All trading accounts load this brief at the start of their next cycle and
    inject it into Pass 1 as `== DAILY RESEARCH BRIEF ==`.

    The Research Agent **does not trade** and has no Ghostfolio account.
    """)


# ═══════════════════════════════════════════════════════════════════════════════
with tab_accounts:
    st.header("Account Types")

    st.markdown("""
    Each account in `config.yaml` has a `cycle_type` field:

    | cycle_type | Description |
    |------------|-------------|
    | `standard` (default) | Weekly / daily stock trading via Pass 1 + Pass 2 |
    | `intraday` | Every 30 min during market hours, with Pass 0 scan |
    | `research` | Daily research brief only, no trading |

    Accounts also differ by **strategy**, which controls the LLM's personality
    and the risk rules applied:

    | Strategy | Description |
    |----------|-------------|
    | `core_satellite` | 60% ETF core + 30% stock satellites + 10% cash |
    | `value_investing` | Focus on P/E, P/B, dividend yield; RSI alone not enough |
    | `momentum` | Follow trend, higher turnover, tighter stops |
    | `wheel` | Options income: sell CSPs → if assigned, sell CCs |

    ### Dynamic watchlist
    Every cycle the symbol universe is built from 3 sources:
    1. **Core** — symbols defined in the account's `watchlist` in config.yaml
    2. **Screener** — Yahoo Finance day gainers, most actives, day losers (top 8 each)
    3. **LLM suggestions** — symbols the LLM asked to watch last cycle (`suggest_symbols`)
    4. **Research Agent** — `top_symbols` from today's brief

    This means the LLM can discover new stocks by suggesting them, and they
    appear with full market data + technicals in the next cycle.

    ### A/B model comparison
    Paired accounts (e.g. `weekly_balanced` with Qwen3-Next and
    `weekly_balanced_nemotron` with Nemotron) run identical strategies with
    different LLMs. The Model Comparison page tracks which performs better.
    """)


# ═══════════════════════════════════════════════════════════════════════════════
with tab_outputs:
    st.header("Reading Outputs")

    st.subheader("Overview page — account cards")
    st.markdown("""
    | Field | Source | Meaning |
    |-------|--------|---------|
    | **Portfolio Value** | Ghostfolio live (`valueInBaseCurrency`) | Securities market value + cash balance |
    | **+X.XX%** delta | `(current - initial) / initial` | Total return since account creation |
    | **Market** | Last audit log | Market regime from the most recent Pass 1 |
    | **Status: OK / ERROR** | Last audit log | Whether the last cycle completed without errors |
    | **🕐 schedule** | config.yaml cron | Human-readable run schedule |
    | **📅 Next:** | APScheduler | Next scheduled run time |
    """)

    st.subheader("Overview page — Latest Decisions")
    st.markdown("""
    Each row is one cycle's summary:

    ```
    2026-02-23 12:07 | Weekly Balanced (Nemotron) |
    Outlook: CAUTIOUSLY_BULLISH | Confidence: 0.72 |
    Trades: 4 | Forced: 0 | Rejected: 1
    ```

    - **Outlook** — the LLM's overall view from Pass 2
    - **Confidence** — self-reported LLM certainty (0.0–1.0); treat as qualitative
    - **Trades** — LLM-requested actions that were approved and executed
    - **Forced** — trades the Risk Manager added (e.g. stop-loss triggers)
    - **Rejected** — trades the LLM wanted but the Risk Manager blocked
    """)

    st.subheader("Audit Logs page")
    st.markdown("""
    Every cycle is saved as a full JSON file. Key sections:

    **`pass1.response`** — Market analysis:
    ```json
    {
      "market_regime": "HIGH_VOLATILITY",
      "regime_reasoning": "VIX at 28, SPY -2.3% week, tariff headlines...",
      "sector_analysis": {
        "Technology": {"rating": "OVERWEIGHT", "score": 1, "reason": "AI demand intact"},
        "Energy": {"rating": "NEUTRAL", "score": 0, "reason": "oil range-bound"}
      },
      "opportunities": [{"symbol": "NVDA", "signal": "pullback to support, IV elevated"}],
      "threats": [{"description": "Fed hawkish surprise risk next Wednesday"}]
    }
    ```

    **`pass2.response`** — Trading decisions:
    ```json
    {
      "reasoning": "Given HIGH_VOLATILITY I'm keeping positions small...",
      "actions": [{
        "type": "BUY", "symbol": "NVDA", "amount_usd": 2000,
        "urgency": "MEDIUM",
        "thesis": "Pullback to $185 support, GTC keynote catalyst, IV high for CSP",
        "stop_loss_pct": -12.0,
        "take_profit_pct": 20.0,
        "time_stop_days": 30
      }],
      "portfolio_outlook": "CAUTIOUSLY_BULLISH",
      "confidence": 0.68,
      "suggest_symbols": ["ARM", "CELH"]
    }
    ```

    **`risk_manager.modifications`** — What the Risk Manager changed:
    ```
    [REJECTED] BUY TSLA $3000: would exceed max position size ($2000)
    [FORCED] SELL COIN: stop-loss triggered (-15.2%)
    ```

    **`executed_trades`** — What actually happened:
    ```json
    {"type": "BUY", "symbol": "NVDA", "quantity": 10.53,
     "price": 189.82, "total": 1999.62, "fee": 1.0, "success": true}
    ```
    """)

    st.subheader("Research Agent page")
    st.markdown("""
    Shows today's `data/daily_research.json`. Key fields to read:

    - **Top Research Picks** — symbols the LLM identified as worth watching,
      with full thesis and specific catalyst. These symbols are automatically
      added to all accounts' watchlists for the next cycle.
    - **Sector Biases** — OVERWEIGHT means the LLM expects outperformance;
      UNDERWEIGHT means avoid or reduce.
    - **Geopolitical Risks** — events that could move specific sectors.
      Read the *Market Impact* line to understand directional effect.
    - **Avoid Today** — the LLM's specific warnings. These are not hard rules
      (the risk manager doesn't enforce them) but inform each account's Pass 1.
    """)

    st.subheader("Options Spreads page")
    st.markdown("""
    | Field | Meaning |
    |-------|---------|
    | **DTE** | Days To Expiration — number of days until the option expires |
    | **% captured** | `(entry_premium - current_cost) / entry_premium × 100` — how much of the max profit has been realised |
    | **Auto-close** flags | DTE ≤ 3 or ≥ 50% captured — position will be closed next cycle automatically |
    | **Strike** | The price at which the option gives the right to buy/sell the stock |
    | **Delta** | Approximate probability the option expires in-the-money (0.30 = ~30%) |
    | **Theta** | Daily time decay — the premium the portfolio earns per day just from time passing |
    """)


# ═══════════════════════════════════════════════════════════════════════════════
with tab_risk:
    st.header("Risk Manager")

    st.markdown("""
    The Risk Manager validates **every** trade before it reaches the executor.
    It cannot be bypassed. There are two separate risk managers:
    one for stock accounts and one for options accounts.
    """)

    st.subheader("Stock account rules")
    st.markdown("""
    Configured per account in `config.yaml` under `risk_profile`:

    | Rule | Config key | What it does |
    |------|-----------|-------------|
    | Max position size | `max_position_pct` | No single position > X% of portfolio. BUY amount is capped. |
    | Min cash reserve | `min_cash_pct` | Always keep X% of portfolio as cash. BUYs are rejected if they'd breach this. |
    | Max trades per cycle | `max_trades_per_cycle` | Excess trades are dropped (lowest urgency first). |
    | Stop-loss | `stop_loss_pct` | If a held position is down > X%, a SELL is **forced** (added by risk manager, not LLM). |
    | Min holding days | `min_holding_days` | Cannot sell a position bought fewer than X days ago. Prevents panic selling. |
    | Max sector exposure | `max_sector_exposure_pct` | Cannot concentrate > X% of portfolio in one sector. |
    | Cost breakeven | `cost_breakeven_multiplier` | Intraday only: trade profit must exceed `multiplier × fee`. |
    """)

    st.subheader("Outcomes")
    st.markdown("""
    Each action gets one of three outcomes:

    - ✅ **Approved** — passes all rules, sent to executor
    - ❌ **Rejected** — blocked by a rule; logged with reason; LLM sees this next cycle
    - ⚡ **Forced** — added by risk manager independently of LLM
      (e.g. stop-loss triggered, min_holding_days expired on a loser)
    """)

    st.subheader("Options risk rules")
    st.markdown("""
    | Rule | Config key | What it does |
    |------|-----------|-------------|
    | Max open CSPs | `max_open_csps` | Total number of open cash-secured puts |
    | Max CCs per symbol | `max_ccs_per_symbol` | Prevents stacking too many calls on one stock |
    | Min cash reserve | `min_cash_pct` | CSP collateral = `strike × 100`. Must leave this % free. |
    | Earnings blackout | `earnings_blackout_days` | Rejects CSPs when the LLM flags imminent earnings in its reason |
    | Take-profit auto-close | `take_profit_pct` | Automatically closes when X% of premium captured (default 50%) |
    | DTE auto-close | `auto_close_dte` | Automatically closes when ≤ X days to expiry (default 3) |

    > **Note on collateral**: A CSP on a $50 stock requires $5,000 in cash as collateral
    > (the broker holds it in case you get assigned). `min_cash_pct: 5` means only 5%
    > must remain free — the rest can be deployed as CSP collateral, which is the correct
    > setting for a pure wheel strategy account.
    """)

    st.subheader("Why did my trade get rejected?")
    st.markdown("""
    Check **Audit Logs → risk_manager.modifications** for the exact reason.
    Common rejections:

    | Message | Cause |
    |---------|-------|
    | `would exceed max position size` | Amount too large; risk manager caps it but if cap < minimum viable it rejects |
    | `insufficient cash` | Cash after trade would drop below `min_cash_pct` |
    | `stop-loss triggered` | Position is down more than `stop_loss_pct` |
    | `min_holding_days not met` | Trying to sell too soon after buying |
    | `max open CSPs reached` | Options: already at the CSP limit |
    | `near-earnings flag in reason` | Options: LLM mentioned imminent earnings in its reason text |
    """)


# ═══════════════════════════════════════════════════════════════════════════════
with tab_glossary:
    st.header("Glossary")

    st.markdown("""
    | Term | Definition |
    |------|-----------|
    | **LLM** | Large Language Model — the AI that analyses markets and makes decisions (Nemotron, Qwen3-Next) |
    | **Pass 1** | First LLM call per cycle: market analysis only, no trades |
    | **Pass 2** | Second LLM call per cycle: trading decisions based on Pass 1 analysis |
    | **Pass 0** | Intraday only: lightweight scan to decide if a full cycle is worth running |
    | **Cycle** | One full run for one account: gather → Pass 1 → Pass 2 → risk → execute → log |
    | **DTE** | Days To Expiration — days until an option contract expires |
    | **CSP** | Cash-Secured Put — sell the right to buy a stock at a set price; requires cash as collateral |
    | **CC** | Covered Call — sell the right to buy stock you already own at a higher price |
    | **Delta** | Options: probability that an option expires in-the-money (~0.30 = 30% chance) |
    | **Theta** | Options: daily premium decay — money earned each day just from time passing |
    | **IV Percentile** | How high implied volatility is today vs the past year (0–100). High IV = richer premiums |
    | **Wheel Strategy** | Sell CSP → if assigned sell CC → repeat. Collects premium on both sides |
    | **Assignment** | Happens when an options buyer exercises their right: you must buy the stock at the strike price |
    | **Cost basis** | The average price you paid for a position (including fees) |
    | **P&L** | Profit and Loss |
    | **Regime** | The overall market environment: BULL_TREND / BEAR_TREND / SIDEWAYS / HIGH_VOLATILITY |
    | **Watchlist** | The set of symbols an account considers for trading in a given cycle |
    | **Screener** | Yahoo Finance automated filter (e.g. "day gainers") that surfaces active symbols |
    | **Ghostfolio** | Open-source portfolio tracker used to record trades and track P&L |
    | **llama-swap** | Local LLM server that switches between models (Nemotron, Qwen3-Next, etc.) |
    | **Forced action** | A trade added by the Risk Manager, not requested by the LLM (e.g. stop-loss) |
    | **Rejected action** | A trade the LLM wanted but the Risk Manager blocked |
    | **Research brief** | Daily pre-market intelligence file (`data/daily_research.json`) produced by the Research Agent |
    | **Confidence** | LLM's self-reported certainty in its decision (0.0–1.0). Qualitative — use as context, not gospel |
    | **Grace time** | How long after a missed schedule the scheduler will still try to run the job |
    """)
