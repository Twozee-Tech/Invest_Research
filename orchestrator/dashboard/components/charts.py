"""Performance charts and comparison plots."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st

DB_PATH = Path("data/audit.db")


def render_performance_chart(config: dict, audit) -> None:
    """Render portfolio performance comparison chart."""
    if not DB_PATH.exists():
        st.info("No performance data yet. Charts will appear after decision cycles run.")
        return

    try:
        import plotly.graph_objects as go

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        accounts = config.get("accounts", {})
        fig = go.Figure()

        for key, acct in accounts.items():
            name = acct.get("name", key)
            rows = conn.execute(
                """SELECT timestamp, portfolio_value, portfolio_pl_pct
                FROM decision_log
                WHERE account_key = ? AND success = 1 AND portfolio_value IS NOT NULL
                ORDER BY timestamp""",
                (key,),
            ).fetchall()

            if rows:
                dates = [r["timestamp"][:10] for r in rows]
                values = [r["portfolio_value"] for r in rows]
                fig.add_trace(go.Scatter(
                    x=dates,
                    y=values,
                    mode="lines+markers",
                    name=name,
                ))

        conn.close()

        if fig.data:
            # Add initial budget line
            initial = config.get("defaults", {}).get("initial_budget", 10000)
            fig.add_hline(
                y=initial,
                line_dash="dash",
                line_color="gray",
                annotation_text=f"Initial ${initial:,}",
            )

            fig.update_layout(
                title="Portfolio Value Over Time",
                xaxis_title="Date",
                yaxis_title="Portfolio Value ($)",
                hovermode="x unified",
                height=400,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No performance data to chart yet.")

    except ImportError:
        st.warning("Install plotly for charts: pip install plotly")
    except Exception as e:
        st.error(f"Chart error: {e}")


def render_pl_comparison(config: dict) -> None:
    """Render P/L comparison bar chart."""
    if not DB_PATH.exists():
        return

    try:
        import plotly.graph_objects as go

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        accounts = config.get("accounts", {})
        names = []
        pl_values = []

        for key, acct in accounts.items():
            row = conn.execute(
                """SELECT portfolio_pl_pct FROM decision_log
                WHERE account_key = ? AND success = 1 AND portfolio_pl_pct IS NOT NULL
                ORDER BY timestamp DESC LIMIT 1""",
                (key,),
            ).fetchone()

            if row:
                names.append(acct.get("name", key))
                pl_values.append(row["portfolio_pl_pct"])

        conn.close()

        if names:
            colors = ["green" if v >= 0 else "red" for v in pl_values]
            fig = go.Figure(go.Bar(
                x=names,
                y=pl_values,
                marker_color=colors,
                text=[f"{v:+.2f}%" for v in pl_values],
                textposition="outside",
            ))
            fig.update_layout(
                title="Current P/L by Account",
                yaxis_title="P/L (%)",
                height=300,
            )
            st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"P/L chart error: {e}")
