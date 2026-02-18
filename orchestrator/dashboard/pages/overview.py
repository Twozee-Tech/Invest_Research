"""Overview page: account cards, performance summary, next run times."""

import streamlit as st
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.ghostfolio_client import GhostfolioClient
from src.audit_logger import AuditLogger
from dashboard.components.charts import render_performance_chart
from dashboard.config_utils import load_config


st.title("AI Investment Orchestrator")

config = load_config()
accounts = config.get("accounts", {})

if not accounts:
    st.warning("No accounts configured. Go to Account Management to create one.")
    st.stop()

# Account cards
cols = st.columns(len(accounts))

audit = AuditLogger()

for i, (key, acct) in enumerate(accounts.items()):
    with cols[i]:
        name = acct.get("name", key)
        model = acct.get("model", "Unknown")
        cron = acct.get("cron", "")
        strategy = acct.get("strategy", "")

        # Get latest log for this account
        logs = audit.get_recent_logs(account_key=key, limit=1)
        latest = logs[0] if logs else {}

        value = latest.get("portfolio_value")
        pl_pct = latest.get("portfolio_pl_pct")
        cash = latest.get("cash")
        last_regime = latest.get("market_regime", "N/A")
        success = latest.get("success", 1)

        initial_budget = config.get("defaults", {}).get("initial_budget", 10000)

        st.subheader(name)
        if value is not None:
            delta = f"{pl_pct:+.2f}%" if pl_pct is not None else None
            st.metric("Portfolio Value", f"${value:,.2f}", delta=delta)
        else:
            st.metric("Portfolio Value", f"${initial_budget:,.2f}", delta="New")

        st.caption(f"Model: **{model}** | Strategy: **{strategy}**")
        st.caption(f"Schedule: `{cron}`")
        st.caption(f"Market: {last_regime}")

        status = "OK" if success else "ERROR"
        if success:
            st.success(f"Status: {status}")
        else:
            st.error(f"Status: {status}")

st.divider()

# Latest decisions
st.subheader("Latest Decisions")
all_logs = audit.get_recent_logs(limit=10)

if all_logs:
    for log in all_logs:
        ts = log.get("timestamp", "")[:16]
        acct_name = log.get("account_name", "Unknown")
        outlook = log.get("portfolio_outlook", "N/A")
        confidence = log.get("confidence")
        n_actions = log.get("actions_count", 0)
        n_forced = log.get("forced_actions_count", 0)
        n_rejected = log.get("rejected_count", 0)
        error = log.get("error")

        if error:
            st.error(f"**{ts}** | {acct_name} | ERROR: {error}")
        else:
            conf_str = f"Confidence: {confidence:.2f}" if confidence else ""
            st.info(
                f"**{ts}** | {acct_name} | "
                f"Outlook: {outlook} | {conf_str} | "
                f"Trades: {n_actions} | Forced: {n_forced} | Rejected: {n_rejected}"
            )
else:
    st.info("No decision logs yet. Waiting for first cycle to run.")

st.divider()

# Performance chart placeholder
st.subheader("Performance Comparison")
render_performance_chart(config, audit)
