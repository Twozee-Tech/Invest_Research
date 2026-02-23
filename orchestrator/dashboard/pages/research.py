"""Daily Research Agent dashboard page."""

import json
from pathlib import Path
from datetime import datetime, timezone

import streamlit as st
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dashboard.pages.overview import cron_to_human, next_run_time
from dashboard.config_utils import load_config

st.title("Daily Research Agent")

# ── Load brief ─────────────────────────────────────────────────────────────

BRIEF_PATH = Path("data/daily_research.json")

brief = None
if BRIEF_PATH.exists():
    try:
        with open(BRIEF_PATH) as f:
            brief = json.load(f)
    except Exception:
        brief = None

config = load_config()
research_cfg = config.get("accounts", {}).get("research", {})
cron = research_cfg.get("cron", "0 14 * * 0-4")
model = research_cfg.get("model", "Nemotron")

col_meta1, col_meta2, col_meta3 = st.columns(3)
with col_meta1:
    st.caption(f"Model: **{model}**")
    st.caption(f"🕐 {cron_to_human(cron)}")
with col_meta2:
    nxt = next_run_time(cron)
    if nxt:
        st.caption(f"📅 {nxt}")
with col_meta3:
    if brief:
        brief_date = brief.get("date", "")
        today = datetime.now().strftime("%Y-%m-%d")
        if brief_date == today:
            st.success(f"Brief: today {brief_date}")
        else:
            st.warning(f"Brief: {brief_date} (stale)")
    else:
        st.info("No brief yet — runs at 14:00 CET")

if not brief:
    st.stop()

st.divider()

# ── Regime + themes ────────────────────────────────────────────────────────

regime = brief.get("market_regime", "")
themes = brief.get("key_themes", [])
macro = brief.get("macro_events_today", "")

col_r1, col_r2 = st.columns([1, 2])
with col_r1:
    regime_color = {
        "BULL_TREND": "🟢",
        "BEAR_TREND": "🔴",
        "SIDEWAYS": "🟡",
        "HIGH_VOLATILITY": "🟠",
    }.get(regime, "⚪")
    st.metric("Market Regime", f"{regime_color} {regime}")
with col_r2:
    if themes:
        st.markdown("**Key Themes**")
        st.markdown("  ·  ".join(f"`{t}`" for t in themes))
    if macro:
        st.markdown(f"**Macro events today:** {macro}")

st.divider()

# ── Top research picks ──────────────────────────────────────────────────────

st.subheader("Top Research Picks")

symbols = brief.get("top_symbols", [])
if symbols:
    for s in symbols:
        sym = s.get("symbol", "?")
        direction = s.get("direction", "")
        conviction = s.get("conviction", "")
        sector = s.get("sector", "")
        thesis = s.get("thesis", "")
        catalyst = s.get("catalyst", "")

        dir_icon = {"BULLISH": "▲", "BEARISH": "▼", "NEUTRAL": "◆"}.get(direction, "")
        conv_color = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(conviction, "⚪")

        with st.expander(
            f"{dir_icon} **{sym}** — {sector}  {conv_color} {conviction}",
            expanded=True,
        ):
            col_t, col_c = st.columns([2, 1])
            with col_t:
                st.markdown(f"**Thesis:** {thesis}")
            with col_c:
                st.markdown(f"**Catalyst:** {catalyst}")
else:
    st.info("No symbols in today's brief.")

st.divider()

# ── Sectors + geopolitical risks side by side ───────────────────────────────

col_sec, col_geo = st.columns(2)

with col_sec:
    st.subheader("Sector Biases")
    sectors = brief.get("sectors", [])
    if sectors:
        for sec in sectors:
            name = sec.get("name", "")
            bias = sec.get("bias", "")
            reason = sec.get("reason", "")
            bias_icon = {
                "OVERWEIGHT": "▲",
                "NEUTRAL": "◆",
                "UNDERWEIGHT": "▼",
            }.get(bias, "")
            st.markdown(f"{bias_icon} **{name}** — {bias}")
            if reason:
                st.caption(reason)
    else:
        st.caption("No sector data.")

with col_geo:
    st.subheader("Geopolitical Risks")
    geo = brief.get("geopolitical_risks", [])
    if geo:
        for risk in geo:
            event = risk.get("event", "")
            impact = risk.get("market_impact", "")
            affected = ", ".join(risk.get("affected_sectors", []))
            st.markdown(f"⚠ **{event}**")
            if impact:
                st.caption(f"Impact: {impact}")
            if affected:
                st.caption(f"Sectors: {affected}")
    else:
        st.caption("No geopolitical risks flagged.")

st.divider()

# ── Avoid today ─────────────────────────────────────────────────────────────

avoid = brief.get("avoid_today", [])
if avoid:
    st.subheader("Avoid Today")
    for item in avoid:
        st.warning(str(item))

# ── Raw JSON expander ────────────────────────────────────────────────────────

with st.expander("Raw brief JSON"):
    st.json(brief)
