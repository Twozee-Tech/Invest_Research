"""Streamlit dashboard entry point - AI Investment Orchestrator."""

import streamlit as st

st.set_page_config(
    page_title="AI Investment Orchestrator",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Main page redirects to Overview
overview = st.Page("pages/overview.py", title="Overview", icon="📊", default=True)
account_detail = st.Page("pages/account_detail.py", title="Account Detail", icon="💼")
run_control = st.Page("pages/run_control.py", title="Run Control", icon="▶️")
model_compare = st.Page("pages/model_compare.py", title="Model Comparison", icon="🔬")
audit_logs = st.Page("pages/audit_logs.py", title="Audit Logs", icon="📋")
account_mgmt = st.Page("pages/account_management.py", title="Account Management", icon="⚙️")
settings = st.Page("pages/settings.py", title="Settings", icon="🔧")

pg = st.navigation([overview, account_detail, run_control, model_compare, audit_logs, account_mgmt, settings])
pg.run()
