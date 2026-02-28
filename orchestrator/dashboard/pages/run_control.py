"""Run control page: manual trigger, dry-run, pause/resume, force sell all."""

import streamlit as st
from pathlib import Path
import sys
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dashboard.config_utils import load_config
from dashboard.pages.overview import cron_to_human, next_run_time


st.title("Run Control")

config = load_config()
accounts = config.get("accounts", {})

if not accounts:
    st.warning("No accounts configured.")
    st.stop()

st.subheader("Manual Trigger")
st.caption("Run a decision cycle manually for any account.")

for key, acct in accounts.items():
    name = acct.get("name", key)
    col1, col2, col3 = st.columns([3, 1, 1])

    with col1:
        cron = acct.get("cron", "")
        human = cron_to_human(cron)
        nxt = next_run_time(cron)
        st.write(f"**{name}** · {acct.get('model', 'N/A')}")
        st.caption(f"🕐 {human}  ·  📅 {nxt}")

    with col2:
        if st.button(f"Run Now", key=f"run_{key}"):
            with st.spinner(f"Running cycle for {name}..."):
                result = subprocess.run(
                    [sys.executable, "-m", "src.main", "--once", key],
                    capture_output=True,
                    text=True,
                    cwd=str(Path(__file__).resolve().parents[2]),
                    timeout=600,
                )
                if result.returncode == 0:
                    st.success(f"Cycle completed for {name}")
                else:
                    st.error(f"Cycle failed: {result.stderr[-500:]}")

    with col3:
        if st.button(f"Dry Run", key=f"dry_{key}"):
            with st.spinner(f"Dry run for {name}..."):
                result = subprocess.run(
                    [sys.executable, "-m", "src.main", "--once", key, "--dry-run"],
                    capture_output=True,
                    text=True,
                    cwd=str(Path(__file__).resolve().parents[2]),
                    timeout=600,
                )
                if result.returncode == 0:
                    st.success(f"Dry run completed for {name}")
                    st.code(result.stdout[-2000:])
                else:
                    st.error(f"Dry run failed: {result.stderr[-500:]}")

st.divider()

st.subheader("Run All Accounts")
col1, col2 = st.columns(2)
with col1:
    if st.button("Run All Now"):
        with st.spinner("Running all accounts..."):
            result = subprocess.run(
                [sys.executable, "-m", "src.main", "--all"],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parents[2]),
                timeout=1800,
            )
            if result.returncode == 0:
                st.success("All cycles completed")
            else:
                st.error(f"Failed: {result.stderr[-500:]}")

with col2:
    if st.button("Dry Run All"):
        with st.spinner("Dry running all accounts..."):
            result = subprocess.run(
                [sys.executable, "-m", "src.main", "--all", "--dry-run"],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parents[2]),
                timeout=1800,
            )
            if result.returncode == 0:
                st.success("All dry runs completed")
                st.code(result.stdout[-3000:])
            else:
                st.error(f"Failed: {result.stderr[-500:]}")
