"""Account management page: create, edit, delete accounts."""

import streamlit as st
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.llm_client import LLMClient
from src.account_manager import AccountManager
from dashboard.config_utils import load_config, save_config, CONFIG_PATH

STRATEGY_TEMPLATES = {
    "core_satellite": {
        "description": "Core-Satellite: 60% ETF core + 30% stock satellites + 10% cash reserve",
        "prompt_style": "Balance risk and return. Prefer broad ETF exposure as core, select individual stocks as satellites for alpha.",
        "preferred_metrics": ["SMA", "RSI", "PE"],
        "horizon": "weeks to months",
    },
    "value_investing": {
        "description": "Value Investing: 40% ETF + 50% undervalued stocks + 10% cash reserve",
        "prompt_style": "Seek undervalued assets with margin of safety. Focus on fundamentals, dividends, and long-term compounding.",
        "preferred_metrics": ["PE", "PB", "DividendYield", "FCF"],
        "horizon": "months to years",
    },
    "momentum": {
        "description": "Momentum/Swing: 20% ETF + 70% high-momentum stocks + 10% cash reserve",
        "prompt_style": "Ride momentum, cut losers fast. Focus on technical breakouts, volume surges, and trend strength.",
        "preferred_metrics": ["RSI", "MACD", "Volume", "BollingerBands"],
        "horizon": "days to weeks",
    },
}

SCHEDULE_PRESETS = {
    "Daily (Mon-Fri 18:00)": "0 18 * * 1-5",
    "Weekly (Sunday 20:00)": "0 20 * * 0",
    "Monthly (1st, 20:00)": "0 20 1 * *",
}

DEFAULT_WATCHLISTS = {
    "core_satellite": "SPY, QQQ, VTI, AAPL, MSFT, GOOGL, AMZN, NVDA, BRK-B, JPM",
    "value_investing": "BRK-B, JPM, JNJ, PG, KO, VZ, XOM, CVX, WMT, BAC",
    "momentum": "NVDA, AMD, TSLA, SMCI, META, PLTR, ARM, CRWD, PANW, SQ",
}


st.title("Account Management")

config = load_config(fallback={"defaults": {"initial_budget": 10000, "currency": "USD"}, "accounts": {}})
accounts = config.get("accounts", {})

# Fetch available models
st.sidebar.subheader("Available Models")
try:
    llm = LLMClient()
    available_models = llm.list_models()
    if available_models:
        for m in available_models:
            st.sidebar.write(f"- {m}")
    else:
        available_models = ["Qwen3-Next", "Nemotron", "Miro_Thinker", "Mistral3_2"]
        st.sidebar.warning("Could not fetch models. Using defaults.")
except Exception:
    available_models = ["Qwen3-Next", "Nemotron", "Miro_Thinker", "Mistral3_2"]
    st.sidebar.warning("LLM not reachable. Using default model list.")

# Existing accounts
st.subheader("Existing Accounts")

if not accounts:
    st.info("No accounts configured yet. Create one below.")

for key, acct in accounts.items():
    name = acct.get("name", key)
    with st.expander(f"{name} ({key})", expanded=False):
        col1, col2 = st.columns([4, 1])
        with col1:
            st.write(f"**Ghostfolio ID:** `{acct.get('ghostfolio_account_id', 'TBD')}`")
            st.write(f"**Model:** {acct.get('model')} | **Fallback:** {acct.get('fallback_model')}")
            st.write(f"**Schedule:** `{acct.get('cron')}` | **Strategy:** {acct.get('strategy')}")
            st.write(f"**Watchlist:** {', '.join(acct.get('watchlist', []))}")

            new_model = st.selectbox(
                "Change Model",
                options=available_models,
                index=available_models.index(acct.get("model")) if acct.get("model") in available_models else 0,
                key=f"model_{key}",
            )
            if new_model != acct.get("model"):
                if st.button(f"Update Model to {new_model}", key=f"update_model_{key}"):
                    config["accounts"][key]["model"] = new_model
                    save_config(config)
                    st.success(f"Model updated to {new_model}")
                    st.rerun()

            new_watchlist = st.text_input(
                "Watchlist (comma-separated)",
                value=", ".join(acct.get("watchlist", [])),
                key=f"watchlist_{key}",
            )
            parsed_watchlist = [s.strip().upper() for s in new_watchlist.split(",") if s.strip()]
            if parsed_watchlist != acct.get("watchlist", []):
                if st.button("Update Watchlist", key=f"update_wl_{key}"):
                    config["accounts"][key]["watchlist"] = parsed_watchlist
                    save_config(config)
                    st.success("Watchlist updated")
                    st.rerun()

        with col2:
            if st.button("Delete", key=f"del_{key}", type="secondary"):
                del config["accounts"][key]
                save_config(config)
                st.warning(f"Account {name} removed from config")
                st.rerun()

st.divider()

# ── Create New Account ───────────────────────────────────────────────────────
st.subheader("Create New Account")

st.info(
    "Each account is an independent AI portfolio managed by a dedicated LLM. "
    "It runs on its own schedule, uses its own strategy, and trades inside its own "
    "Ghostfolio portfolio. You can run multiple accounts in parallel to compare models."
)

# Strategy selector is OUTSIDE the form so changing it immediately updates
# the description and watchlist default (inside a form only submit triggers rerun).
st.markdown("##### Strategy")
st.caption(
    "The strategy shapes the LLM's decision-making style — system prompt, "
    "metrics it focuses on, and time horizon."
)
strategy = st.selectbox(
    "Strategy Template",
    options=list(STRATEGY_TEMPLATES.keys()),
    key="new_account_strategy",
)
template = STRATEGY_TEMPLATES[strategy]
st.info(
    f"**{template['description']}**  \n"
    f"Horizon: *{template['horizon']}*  \n"
    f"Key metrics: {', '.join(template['preferred_metrics'])}"
)

with st.form("new_account"):
    col_name, col_key = st.columns(2)
    with col_name:
        name = st.text_input(
            "Account Name",
            placeholder="e.g., Weekly Growth",
            help="Human-readable label shown in the dashboard.",
        )
    with col_key:
        key = st.text_input(
            "Account Key",
            placeholder="e.g., weekly_growth",
            help="Unique identifier used internally. No spaces — use underscores.",
        )

    st.markdown("##### Model")
    st.caption(
        "The **Primary Model** does all analysis. The **Fallback** is used automatically "
        "if the primary fails (timeout, bad JSON, empty response)."
    )
    col1, col2 = st.columns(2)
    with col1:
        model = st.selectbox("Primary Model", options=available_models)
    with col2:
        fallback_model = st.selectbox(
            "Fallback Model",
            options=available_models,
            index=min(1, len(available_models) - 1),
        )

    st.markdown("##### Schedule")
    st.caption(
        "How often the AI runs. **Daily** suits momentum. "
        "**Weekly** suits balanced. **Monthly** suits value investing."
    )
    schedule_preset = st.selectbox("Schedule", options=list(SCHEDULE_PRESETS.keys()) + ["Custom"])
    if schedule_preset == "Custom":
        cron = st.text_input(
            "Custom Cron Expression",
            placeholder="0 20 * * 0",
            help="Standard cron: minute hour day month weekday.",
        )
    else:
        cron = SCHEDULE_PRESETS[schedule_preset]
        st.code(cron, language=None)

    st.markdown("##### Risk Profile")
    st.caption(
        "Enforced by the Risk Manager before any trade reaches Ghostfolio. "
        "LLM proposals that violate these rules are rejected or trimmed automatically."
    )
    rcol1, rcol2, rcol3 = st.columns(3)
    with rcol1:
        max_pos = st.number_input(
            "Max Position %", 5, 50, 20,
            help="Single stock can't exceed this % of total portfolio value.",
        )
        min_cash = st.number_input(
            "Min Cash %", 5, 50, 10,
            help="Always keep at least this % in cash.",
        )
    with rcol2:
        stop_loss = st.number_input(
            "Stop Loss %", -50, -1, -15,
            help="Portfolio drawdown from peak that triggers forced de-risking.",
        )
        max_trades = st.number_input(
            "Max Trades/Cycle", 1, 20, 5,
            help="Cap on trades per run. Lowest-urgency trades dropped first.",
        )
    with rcol3:
        min_hold = st.number_input(
            "Min Hold Days", 0, 365, 14,
            help="Prevents selling a position bought less than N days ago.",
        )
        max_sector = st.number_input(
            "Max Sector %", 10, 100, 40,
            help="Maximum exposure to any single sector.",
        )

    st.markdown("##### Watchlist")
    st.caption(
        "Tickers the AI analyses each cycle (price, news, technicals). "
        "8–15 symbols works best. Pre-filled with the strategy default."
    )
    # key includes strategy so Streamlit resets value when strategy changes
    watchlist_str = st.text_input(
        "Tickers (comma-separated)",
        value=DEFAULT_WATCHLISTS.get(strategy, ""),
        key=f"watchlist_input_{strategy}",
        help="e.g. SPY, QQQ, AAPL, NVDA — uppercase ticker symbols.",
    )

    submitted = st.form_submit_button("Create Account", type="primary")

    if submitted and name and key:
        watchlist = [s.strip().upper() for s in watchlist_str.split(",") if s.strip()]
        if not watchlist:
            watchlist = [s.strip().upper() for s in DEFAULT_WATCHLISTS.get(strategy, "").split(",") if s.strip()]

        risk_profile = {
            "max_position_pct": max_pos,
            "min_cash_pct": min_cash,
            "max_trades_per_cycle": max_trades,
            "stop_loss_pct": stop_loss,
            "min_holding_days": min_hold,
            "max_sector_exposure_pct": max_sector,
        }

        try:
            mgr = AccountManager(config_path=str(CONFIG_PATH))
            gf_id = mgr.add_account(
                key=key,
                name=name,
                model=model,
                cron=cron,
                strategy=strategy,
                risk_profile=risk_profile,
                watchlist=watchlist,
                fallback_model=fallback_model,
                strategy_description=template["description"],
                prompt_style=template["prompt_style"],
                preferred_metrics=template["preferred_metrics"],
                horizon=template["horizon"],
            )
            st.success(f"Account '{name}' created! Ghostfolio ID: {gf_id}")
            st.rerun()
        except Exception as e:
            st.error(f"Failed to create account: {e}")
