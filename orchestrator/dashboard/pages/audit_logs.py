"""Audit logs page: full prompt/response viewer with filters."""

import streamlit as st
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.audit_logger import AuditLogger
from dashboard.config_utils import load_config


st.title("Audit Logs")

config = load_config()
accounts = config.get("accounts", {})
audit = AuditLogger()

# Filters
col1, col2 = st.columns(2)
with col1:
    account_filter = st.selectbox(
        "Filter by Account",
        options=["All"] + list(accounts.keys()),
        format_func=lambda k: "All Accounts" if k == "All" else accounts.get(k, {}).get("name", k),
    )
with col2:
    limit = st.slider("Max entries", 5, 50, 20)

# Fetch logs
acct_key = None if account_filter == "All" else account_filter
logs = audit.get_recent_logs(account_key=acct_key, limit=limit)

if not logs:
    st.info("No audit logs found.")
    st.stop()

for log in logs:
    ts = log.get("timestamp", "")[:16]
    acct_name = log.get("account_name", "Unknown")
    model = log.get("model", "N/A")
    success = log.get("success", 1)
    error = log.get("error")

    header = f"{ts} | {acct_name} | {model}"
    if not success:
        header += " | ERROR"

    with st.expander(header, expanded=False):
        if error:
            st.error(f"Error: {error}")

        st.write(f"**Model:** {model}")
        st.write(f"**Market Regime:** {log.get('market_regime', 'N/A')}")
        st.write(f"**Outlook:** {log.get('portfolio_outlook', 'N/A')}")
        st.write(f"**Confidence:** {log.get('confidence', 'N/A')}")
        st.write(f"**Actions:** {log.get('actions_count', 0)} | "
                 f"Forced: {log.get('forced_actions_count', 0)} | "
                 f"Rejected: {log.get('rejected_count', 0)}")

        if log.get("portfolio_value"):
            st.write(f"**Portfolio Value:** ${log['portfolio_value']:,.2f}")
        if log.get("portfolio_pl_pct") is not None:
            st.write(f"**P/L:** {log['portfolio_pl_pct']:+.2f}%")

        # Full log detail
        log_file = log.get("log_file")
        if log_file and st.button(f"Show Full Log", key=f"full_{log_file}"):
            detail = audit.get_log_detail(log_file)
            if detail:
                # Pass 1
                st.subheader("Pass 1: Analysis")
                p1 = detail.get("pass1", {})
                if p1.get("messages"):
                    with st.expander("Pass 1 Prompt"):
                        for msg in p1["messages"]:
                            st.markdown(f"**{msg.get('role', 'unknown')}:**")
                            st.code(msg.get("content", "")[:3000], language=None)
                if p1.get("response"):
                    with st.expander("Pass 1 Response"):
                        st.json(p1["response"])

                # Pass 2
                st.subheader("Pass 2: Decision")
                p2 = detail.get("pass2", {})
                if p2.get("messages"):
                    with st.expander("Pass 2 Prompt"):
                        for msg in p2["messages"]:
                            st.markdown(f"**{msg.get('role', 'unknown')}:**")
                            st.code(msg.get("content", "")[:3000], language=None)
                if p2.get("response"):
                    with st.expander("Pass 2 Response"):
                        st.json(p2["response"])

                # Risk Manager
                rm = detail.get("risk_manager", {})
                if rm.get("modifications") or rm.get("warnings"):
                    st.subheader("Risk Manager")
                    for m in rm.get("modifications", []):
                        st.write(f"- {m}")
                    for w in rm.get("warnings", []):
                        st.warning(w)

                # Trades
                trades = detail.get("executed_trades", [])
                if trades:
                    st.subheader("Executed Trades")
                    st.json(trades)

                # Portfolio before/after
                st.subheader("Portfolio Snapshot")
                pcol1, pcol2 = st.columns(2)
                with pcol1:
                    st.write("**Before:**")
                    st.json(detail.get("portfolio_before"))
                with pcol2:
                    st.write("**After:**")
                    st.json(detail.get("portfolio_after"))
            else:
                st.error(f"Could not load log file: {log_file}")
