"""Model comparison page: compare LLM performance across accounts."""

import streamlit as st
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.audit_logger import AuditLogger
from dashboard.config_utils import load_config

DB_PATH = Path("data/audit.db")


st.title("Model Comparison")

config = load_config()
audit = AuditLogger()

if not DB_PATH.exists():
    st.info("No data yet. Wait for decision cycles to run.")
    st.stop()

# Model stats from logs
try:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Per-model stats
    model_stats = conn.execute("""
        SELECT model,
               COUNT(*) as total_cycles,
               AVG(confidence) as avg_confidence,
               SUM(actions_count) as total_actions,
               SUM(forced_actions_count) as total_forced,
               SUM(rejected_count) as total_rejected,
               SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successful,
               AVG(portfolio_pl_pct) as avg_pl_pct
        FROM decision_log
        GROUP BY model
        ORDER BY total_cycles DESC
    """).fetchall()

    if model_stats:
        st.subheader("Model Performance Summary")
        cols = st.columns(len(model_stats))
        for i, stat in enumerate(model_stats):
            with cols[i]:
                st.markdown(f"### {stat['model']}")
                st.metric("Total Cycles", stat["total_cycles"])
                if stat["avg_confidence"]:
                    st.metric("Avg Confidence", f"{stat['avg_confidence']:.2f}")
                st.metric("Total Actions", stat["total_actions"] or 0)
                st.metric("Rejected Actions", stat["total_rejected"] or 0)
                success_rate = (stat["successful"] / stat["total_cycles"] * 100) if stat["total_cycles"] > 0 else 0
                st.metric("Success Rate", f"{success_rate:.0f}%")
                if stat["avg_pl_pct"] is not None:
                    st.metric("Avg P/L %", f"{stat['avg_pl_pct']:+.2f}%")
    else:
        st.info("No model data available yet.")

    # Per-account model history
    st.divider()
    st.subheader("Per-Account History")

    account_stats = conn.execute("""
        SELECT account_name, model,
               COUNT(*) as cycles,
               AVG(confidence) as avg_conf,
               AVG(portfolio_pl_pct) as avg_pl
        FROM decision_log
        GROUP BY account_name, model
        ORDER BY account_name, model
    """).fetchall()

    if account_stats:
        for stat in account_stats:
            st.write(
                f"**{stat['account_name']}** using **{stat['model']}**: "
                f"{stat['cycles']} cycles, "
                f"avg confidence {stat['avg_conf']:.2f}" if stat['avg_conf'] else "",
                f"avg P/L {stat['avg_pl']:+.2f}%" if stat['avg_pl'] is not None else "",
            )

    conn.close()
except Exception as e:
    st.error(f"Database error: {e}")
