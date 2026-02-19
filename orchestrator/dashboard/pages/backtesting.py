"""Backtesting / Historical Simulation dashboard page."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# Add orchestrator root to path so src.* imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dashboard.config_utils import load_config
from src.backtest.runner import BacktestResult, run_backtest
from src.llm_client import LLMClient

st.title("Backtesting / Historical Simulation")
st.caption(
    "Simulate how the LLM would have traded on historical market data. "
    "Dates are hidden from the model to reduce temporal bias."
)

config = load_config()
accounts = config.get("accounts", {})
defaults = config.get("defaults", {})

# Filter out options-spreads accounts (not supported for backtesting)
eligible = {
    k: v for k, v in accounts.items()
    if v.get("strategy", "") != "vertical_spreads"
}

if not eligible:
    st.warning("No eligible accounts found (vertical_spreads accounts are excluded).")
    st.stop()

# ---------------------------------------------------------------------------
# Configuration panel
# ---------------------------------------------------------------------------
st.subheader("Configuration")

col1, col2 = st.columns(2)

with col1:
    account_key = st.selectbox(
        "Strategy / Account",
        options=list(eligible.keys()),
        format_func=lambda k: f"{eligible[k].get('name', k)} ({eligible[k].get('strategy', '')})",
    )
    initial_capital = st.number_input(
        "Initial capital ($)",
        min_value=1_000,
        max_value=1_000_000,
        value=10_000,
        step=1_000,
    )

with col2:
    default_end = date.today() - timedelta(days=1)
    default_start = default_end - timedelta(days=365)

    start_date = st.date_input("Start date", value=default_start)
    end_date = st.date_input("End date", value=default_end)

account_cfg = eligible[account_key]
watchlist = account_cfg.get("watchlist", [])
model_name = account_cfg.get("model", "N/A")
cron = account_cfg.get("cron", "N/A")

# Estimation info
days = max(1, (end_date - start_date).days)
est_weeks = days // 7
st.info(
    f"**{account_cfg.get('name', account_key)}** — model: `{model_name}` — "
    f"watchlist: {len(watchlist)} symbols\n\n"
    f"Estimated cycles: **~{est_weeks} weeks** × ~10s/cycle = **~{est_weeks * 10 // 60} min**\n\n"
    f"⚠️ The LLM may recognise the time period from absolute price levels "
    f"(e.g. SPY@450 ≈ 2022). Historical news is not available."
)

if start_date >= end_date:
    st.error("Start date must be before end date.")
    st.stop()

# ---------------------------------------------------------------------------
# Run button
# ---------------------------------------------------------------------------
run_btn = st.button("▶ Run Simulation", type="primary", use_container_width=True)

if run_btn:
    st.session_state.pop("backtest_result", None)  # Clear previous result

    progress_bar = st.progress(0.0, text="Initialising…")
    status_text = st.empty()

    def on_progress(week_num: int, total_weeks: int, current_date: str) -> None:
        pct = week_num / total_weeks
        progress_bar.progress(pct, text=f"Week {week_num}/{total_weeks} — {current_date}")
        status_text.text(f"Running week {week_num} of {total_weeks} ({current_date})…")

    llm_base_url = defaults.get("llm_base_url", "http://192.168.0.169:8080/v1")
    llm = LLMClient(base_url=llm_base_url)

    try:
        result = run_backtest(
            account_config=account_cfg,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            llm_client=llm,
            initial_cash=float(initial_capital),
            on_progress=on_progress,
        )
        st.session_state["backtest_result"] = result
        progress_bar.progress(1.0, text="Simulation complete")
        status_text.empty()
        if result.error:
            st.error(f"Simulation ended with error: {result.error}")
    except Exception as exc:
        progress_bar.empty()
        status_text.empty()
        st.error(f"Simulation failed: {exc}")

# ---------------------------------------------------------------------------
# Results section
# ---------------------------------------------------------------------------
result: BacktestResult | None = st.session_state.get("backtest_result")

if result is None:
    st.stop()

st.divider()
st.subheader("Results")

# Top-line metrics
m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Total Return", f"{result.total_return_pct:+.1f}%",
          delta=f"{result.total_return_pct - result.benchmark_return_pct:+.1f}% vs SPY")
m2.metric("Benchmark (SPY)", f"{result.benchmark_return_pct:+.1f}%")
m3.metric("Max Drawdown", f"{result.max_drawdown_pct:.1f}%")
m4.metric("Win Rate", f"{result.win_rate_pct:.0f}%")
m5.metric("Total Trades", str(len(result.trades)))

# ---------------------------------------------------------------------------
# Performance chart: portfolio vs SPY benchmark
# ---------------------------------------------------------------------------
if result.snapshots:
    st.subheader("Performance Chart")

    snap_df = pd.DataFrame(result.snapshots)
    snap_df["date"] = pd.to_datetime(snap_df["date"])
    snap_df = snap_df.set_index("date")

    # Build SPY benchmark curve from initial_capital
    if result.benchmark_return_pct != 0 and len(snap_df) > 1:
        # Linear interpolation of SPY growth as a proxy
        n = len(snap_df)
        spy_growth = [(result.benchmark_return_pct / 100) * i / (n - 1) for i in range(n)]
        snap_df["spy_value"] = [initial_capital * (1 + g) for g in spy_growth]
    else:
        snap_df["spy_value"] = initial_capital

    chart_df = snap_df[["total_value", "spy_value"]].rename(
        columns={"total_value": "Portfolio", "spy_value": "SPY Benchmark"}
    )
    st.line_chart(chart_df)

# ---------------------------------------------------------------------------
# Decisions table
# ---------------------------------------------------------------------------
st.subheader("Weekly Decisions")

if result.decisions:
    rows = []
    for d in result.decisions:
        actions_str = ", ".join(
            f"{a['type']} {a['symbol']}" for a in d.get("actions", [])
        ) or "HOLD"
        risk_mods_str = "; ".join(d.get("risk_mods", []))[:80] if d.get("risk_mods") else "—"
        snap = next((s for s in result.snapshots if s["date"] == d["date"]), {})
        rows.append({
            "Week": d["week_num"],
            "Date": d["date"],
            "Regime": d.get("market_regime", "—"),
            "Outlook": d.get("outlook", "—"),
            "Conf": f"{d.get('confidence', 0):.2f}",
            "Actions": actions_str,
            "Risk mods": risk_mods_str,
            "Portfolio $": f"${snap.get('total_value', 0):,.0f}",
            "P/L %": f"{snap.get('pl_pct', 0):+.1f}%",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Trade log
# ---------------------------------------------------------------------------
if result.trades:
    with st.expander(f"Trade Log ({len(result.trades)} trades)"):
        trade_rows = [
            {
                "Date": t.date,
                "Type": t.type,
                "Symbol": t.symbol,
                "Qty": round(t.quantity, 4),
                "Price": f"${t.price:,.2f}",
                "Total": f"${t.total:,.2f}",
                "Avg Cost": f"${t.avg_cost:,.2f}" if t.type == "SELL" else "—",
                "P/L": (
                    f"{(t.price - t.avg_cost) / t.avg_cost * 100:+.1f}%"
                    if t.type == "SELL" and t.avg_cost > 0 else "—"
                ),
            }
            for t in result.trades
        ]
        st.dataframe(pd.DataFrame(trade_rows), use_container_width=True, hide_index=True)
