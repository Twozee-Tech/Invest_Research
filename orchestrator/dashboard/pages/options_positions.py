"""Options Spreads dashboard: active positions, Greeks, P&L history."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import sys

import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dashboard.config_utils import load_config

st.title("Options Spreads")

config = load_config()
accounts = config.get("accounts", {})

# Find options accounts
options_accounts = {
    key: acct
    for key, acct in accounts.items()
    if acct.get("strategy") == "vertical_spreads"
}

if not options_accounts:
    st.warning("No options spreads accounts configured. Add an account with `strategy: vertical_spreads` in config.yaml.")
    st.stop()

# Account selector
selected_key = st.selectbox(
    "Account",
    list(options_accounts.keys()),
    format_func=lambda k: options_accounts[k].get("name", k),
)
acct = options_accounts[selected_key]

DB_PATH = Path("data/audit.db")


def _db_connect():
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _load_active(account_key: str) -> list[dict]:
    conn = _db_connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """SELECT * FROM options_positions
            WHERE account_key=? AND status='open'
            ORDER BY expiration_date ASC""",
            (account_key,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _load_history(account_key: str, limit: int = 30) -> list[dict]:
    conn = _db_connect()
    if conn is None:
        return []
    try:
        rows = conn.execute(
            """SELECT * FROM options_positions
            WHERE account_key=? AND status IN ('closed','expired')
            ORDER BY close_date DESC LIMIT ?""",
            (account_key, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _parse_greeks(raw) -> dict:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return raw if isinstance(raw, dict) else {}


# ── Portfolio Greeks Summary ──────────────────────────────────────────────────

active = _load_active(selected_key)

st.subheader("Portfolio Greeks")

total_delta = sum(_parse_greeks(p.get("current_greeks")).get("net_delta", 0) for p in active)
total_theta = sum(_parse_greeks(p.get("current_greeks")).get("net_theta", 0) for p in active)
total_vega = sum(_parse_greeks(p.get("current_greeks")).get("net_vega", 0) for p in active)
total_gamma = sum(_parse_greeks(p.get("current_greeks")).get("net_gamma", 0) for p in active)

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    st.metric("Net Delta", f"{total_delta:+.2f}")
with c2:
    st.metric("Theta / day", f"${total_theta:+.2f}")
with c3:
    st.metric("Vega / 1% IV", f"${total_vega:+.2f}")
with c4:
    st.metric("Gamma", f"{total_gamma:+.4f}")
with c5:
    st.metric("Open Spreads", len(active))

st.divider()

# ── Active Positions ──────────────────────────────────────────────────────────

st.subheader("Active Positions")

if not active:
    st.info("No open positions. Run a cycle to let the AI open spreads.")
else:
    for pos in active:
        g = _parse_greeks(pos.get("current_greeks"))
        entry_debit = pos.get("entry_debit", 0)
        current_value = pos.get("current_value")
        current_pl = pos.get("current_pl")
        max_profit = pos.get("max_profit", 0)
        max_loss = pos.get("max_loss", 0)
        dte = pos.get("dte")
        contracts = pos.get("contracts", 1)

        # P&L strings
        pl_str = f"${current_pl:+,.2f}" if current_pl is not None else "N/A"
        if current_pl is not None and max_loss > 0:
            profit_captured = current_pl / max_profit * 100 if max_profit > 0 else 0
            pl_pct_str = f" ({profit_captured:+.0f}% of max profit)"
        else:
            pl_pct_str = ""

        # Color based on DTE urgency
        dte_color = "red" if (dte or 99) <= 7 else ("orange" if (dte or 99) <= 14 else "green")

        header = (
            f"{pos.get('symbol')} {pos.get('spread_type')} "
            f"{pos.get('buy_strike')}/{pos.get('sell_strike')} "
            f"| exp {pos.get('expiration_date')} "
            f"| DTE: {dte}"
        )

        with st.expander(header, expanded=True):
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric(
                    "P&L",
                    pl_str + pl_pct_str,
                    delta=f"Entry: ${entry_debit:.2f}",
                )
            with col2:
                st.metric(
                    "Max Profit / Max Loss",
                    f"${max_profit:.2f} / ${max_loss:.2f}",
                )
            with col3:
                st.metric("Delta", f"{g.get('net_delta', 0):+.2f}")
                st.metric("Theta/day", f"${g.get('net_theta', 0):+.2f}")
            with col4:
                st.metric("Vega/1%IV", f"${g.get('net_vega', 0):+.2f}")
                st.metric("Contracts", contracts)

            st.caption(
                f"Buy: {pos.get('buy_strike')} {pos.get('buy_option_type').upper()} @ ${entry_debit:.2f} entry | "
                f"Sell: {pos.get('sell_strike')} {pos.get('sell_option_type').upper()} | "
                f"ID: {pos.get('id')}"
            )

            # DTE progress bar
            max_dte = 60
            dte_val = dte or 0
            st.progress(
                max(0.0, min(1.0, dte_val / max_dte)),
                text=f"DTE: {dte_val} days remaining",
            )

st.divider()

# ── P&L History ───────────────────────────────────────────────────────────────

st.subheader("Closed Positions — P&L History")

history = _load_history(selected_key)

if not history:
    st.info("No closed positions yet.")
else:
    # Summary table
    rows = []
    for p in history:
        rows.append({
            "Symbol": p.get("symbol"),
            "Type": p.get("spread_type"),
            "Strikes": f"{p.get('buy_strike')}/{p.get('sell_strike')}",
            "Entry": p.get("entry_date", "")[:10],
            "Close": p.get("close_date", "")[:10],
            "Reason": p.get("close_reason", ""),
            "Entry $": f"${p.get('entry_debit', 0):.2f}",
            "Close $": f"${p.get('close_value', 0):.2f}" if p.get("close_value") else "N/A",
            "P&L": f"${p.get('realized_pl', 0):+.2f}" if p.get("realized_pl") is not None else "N/A",
        })

    import pandas as pd
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Cumulative P&L chart
    pl_values = [p.get("realized_pl") or 0 for p in reversed(history)]
    dates = [p.get("close_date", "")[:10] for p in reversed(history)]
    cumulative = []
    running = 0.0
    for v in pl_values:
        running += v
        cumulative.append(running)

    if cumulative:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=dates,
            y=cumulative,
            mode="lines+markers",
            name="Cumulative P&L",
            line=dict(color="green" if cumulative[-1] >= 0 else "red", width=2),
            fill="tozeroy",
            fillcolor="rgba(0, 200, 0, 0.1)" if cumulative[-1] >= 0 else "rgba(200, 0, 0, 0.1)",
        ))
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        fig.update_layout(
            title="Cumulative Realized P&L — Options Spreads",
            xaxis_title="Close Date",
            yaxis_title="Cumulative P&L ($)",
            height=350,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Per-trade P&L bar chart
    if pl_values:
        colors = ["green" if v >= 0 else "red" for v in pl_values]
        labels = [f"{p.get('symbol')} {p.get('spread_type')[:2]}" for p in reversed(history)]
        fig2 = go.Figure(go.Bar(
            x=labels,
            y=pl_values,
            marker_color=colors,
            text=[f"${v:+.0f}" for v in pl_values],
            textposition="outside",
        ))
        fig2.add_hline(y=0, line_dash="dash", line_color="gray")
        fig2.update_layout(
            title="Per-Trade P&L",
            xaxis_title="Position",
            yaxis_title="Realized P&L ($)",
            height=300,
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig2, use_container_width=True)

st.divider()

# ── Account Info ──────────────────────────────────────────────────────────────

with st.expander("Account Configuration"):
    rp = acct.get("risk_profile", {})
    c1, c2, c3 = st.columns(3)
    with c1:
        st.caption(f"Model: **{acct.get('model')}**")
        st.caption(f"Schedule: `{acct.get('cron')}`")
        st.caption(f"Max Open Spreads: {rp.get('max_open_spreads', 5)}")
    with c2:
        st.caption(f"Target DTE: {rp.get('min_new_position_dte', 21)}-60 days")
        st.caption(f"Auto-close DTE: ≤{rp.get('auto_close_dte', 7)}")
        st.caption(f"Take profit: {rp.get('take_profit_pct', 75)}% of max profit")
    with c3:
        st.caption(f"Stop loss: {rp.get('stop_loss_pct', 50)}% of max loss")
        st.caption(f"Min cash reserve: {rp.get('min_cash_pct', 40)}%")
        st.caption(f"Max delta: ±{rp.get('max_portfolio_delta_pct', 15)}% of account")
    st.caption(f"Watchlist: {', '.join(acct.get('watchlist', []))}")
