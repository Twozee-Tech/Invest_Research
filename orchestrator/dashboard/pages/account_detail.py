"""Account detail page: positions, decision history, P/L per holding."""

import streamlit as st
import yaml
from pathlib import Path
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.audit_logger import AuditLogger


def load_config():
    config_path = Path("config.yaml")
    if config_path.exists():
        with open(config_path) as f:
            return yaml.safe_load(f)
    return {"accounts": {}}


st.title("Account Detail")

config = load_config()
accounts = config.get("accounts", {})

if not accounts:
    st.warning("No accounts configured.")
    st.stop()

selected_key = st.selectbox(
    "Select Account",
    options=list(accounts.keys()),
    format_func=lambda k: accounts[k].get("name", k),
)

acct = accounts[selected_key]
audit = AuditLogger()

# Account info
col1, col2, col3 = st.columns(3)
with col1:
    st.write(f"**Model:** {acct.get('model', 'N/A')}")
    st.write(f"**Fallback:** {acct.get('fallback_model', 'N/A')}")
with col2:
    st.write(f"**Strategy:** {acct.get('strategy', 'N/A')}")
    st.write(f"**Horizon:** {acct.get('horizon', 'N/A')}")
with col3:
    st.write(f"**Schedule:** `{acct.get('cron', 'N/A')}`")
    st.write(f"**Ghostfolio ID:** `{acct.get('ghostfolio_account_id', 'N/A')[:12]}...`")

st.divider()

# Risk profile
st.subheader("Risk Profile")
risk = acct.get("risk_profile", {})
rcols = st.columns(5)
rcols[0].metric("Max Position", f"{risk.get('max_position_pct', 'N/A')}%")
rcols[1].metric("Min Cash", f"{risk.get('min_cash_pct', 'N/A')}%")
rcols[2].metric("Stop Loss", f"{risk.get('stop_loss_pct', 'N/A')}%")
rcols[3].metric("Max Trades/Cycle", risk.get("max_trades_per_cycle", "N/A"))
rcols[4].metric("Min Hold Days", risk.get("min_holding_days", "N/A"))

# Watchlist
st.subheader("Watchlist")
st.write(", ".join(acct.get("watchlist", [])))

st.divider()

# Decision history
st.subheader("Decision History")
logs = audit.get_recent_logs(account_key=selected_key, limit=20)

if logs:
    for log in logs:
        ts = log.get("timestamp", "")[:16]
        outlook = log.get("portfolio_outlook", "N/A")
        confidence = log.get("confidence")
        n_actions = log.get("actions_count", 0)
        regime = log.get("market_regime", "N/A")
        value = log.get("portfolio_value")
        error = log.get("error")

        with st.expander(f"{ts} | {outlook} | Actions: {n_actions}" + (f" | ERROR" if error else "")):
            if value:
                st.write(f"Portfolio Value: ${value:,.2f}")
            if confidence:
                st.write(f"Confidence: {confidence:.2f}")
            st.write(f"Market Regime: {regime}")
            if error:
                st.error(f"Error: {error}")

            # Load full log
            log_file = log.get("log_file")
            if log_file:
                detail = audit.get_log_detail(log_file)
                if detail:
                    trades = detail.get("executed_trades", [])
                    if trades:
                        st.write("**Executed Trades:**")
                        for t in trades:
                            status = "OK" if t.get("success") else "FAILED"
                            st.write(
                                f"  {t.get('type')} {t.get('symbol')} "
                                f"qty={t.get('quantity', 0):.4f} @ ${t.get('price', 0):.2f} "
                                f"= ${t.get('total', 0):,.2f} [{status}]"
                            )

                    risk_mods = detail.get("risk_manager", {})
                    if risk_mods.get("modifications"):
                        st.write("**Risk Modifications:**")
                        for m in risk_mods["modifications"]:
                            st.write(f"  - {m}")
                    if risk_mods.get("warnings"):
                        st.write("**Risk Warnings:**")
                        for w in risk_mods["warnings"]:
                            st.warning(w)
else:
    st.info("No decision history for this account yet.")
