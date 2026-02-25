"""Overview page: account cards, performance summary, next run times."""

import streamlit as st
from pathlib import Path
from datetime import datetime, timezone
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.ghostfolio_client import GhostfolioClient
from src.audit_logger import AuditLogger
from dashboard.components.charts import render_performance_chart
from dashboard.config_utils import load_config


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

_DOW_ISO = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # 0=Mon … 6=Sun


def _dow_label(dow_str: str) -> str:
    """Convert APScheduler day_of_week field to readable label."""
    dow_str = dow_str.strip()
    if "-" in dow_str:
        a, b = dow_str.split("-", 1)
        return f"{_DOW_ISO[int(a)]}–{_DOW_ISO[int(b)]}"
    if "," in dow_str:
        return "/".join(_DOW_ISO[int(d)] for d in dow_str.split(","))
    if dow_str == "*":
        return "daily"
    return _DOW_ISO[int(dow_str)]


def cron_to_human(cron: str) -> str:
    """Convert a crontab string to a short human-readable description."""
    if not cron:
        return "no schedule"
    parts = cron.split()
    if len(parts) != 5:
        return cron

    minute, hour, dom, month, dow = parts

    # Build time string
    if "," in minute:
        mins = ", ".join(f":{m.zfill(2)}" for m in minute.split(","))
        time_str = f"{hour}:xx at {mins}"
    elif minute == "*/30":
        time_str = f"every 30 min"
    else:
        time_str = f"{hour}:{minute.zfill(2)}"

    # Build hour range for intraday
    if "-" in hour:
        h_start, h_end = hour.split("-")
        time_str = f"{h_start}:00–{h_end}:59 every 30 min"
        return f"{_dow_label(dow)} · {time_str} CET"

    # Build frequency string
    if dom != "*" and dom.isdigit():
        freq = f"Monthly (day {dom})"
    elif dow == "*":
        freq = "Daily"
    else:
        freq = _dow_label(dow)

    return f"{freq} · {time_str} CET"


def next_run_time(cron: str) -> str:
    """Return 'Next: Weekday Mon DD at HH:MM' using APScheduler."""
    if not cron:
        return ""
    try:
        from apscheduler.triggers.cron import CronTrigger
        parts = cron.split()
        if len(parts) != 5:
            return ""
        minute, hour, dom, month, dow = parts
        trigger = CronTrigger(
            minute=minute, hour=hour,
            day=dom, month=month, day_of_week=dow,
            timezone="Europe/Warsaw",
        )
        now = datetime.now(timezone.utc)
        nxt = trigger.get_next_fire_time(None, now)
        if nxt is None:
            return ""
        # Format as "Mon 25 Feb · 18:00"
        return "Next: " + nxt.strftime("%a %d %b · %H:%M")
    except Exception:
        return ""


st.title("AI Investment Orchestrator")

config = load_config()
accounts = config.get("accounts", {})

if not accounts:
    st.warning("No accounts configured. Go to Account Management to create one.")
    st.stop()

# Fetch live account values from Ghostfolio (valueInBaseCurrency = securities + balance)
_live_values: dict[str, dict] = {}
try:
    from src.ghostfolio_client import GhostfolioClient
    _gf = GhostfolioClient()
    _acct_list = _gf.list_accounts()
    if isinstance(_acct_list, dict):
        _acct_list = _acct_list.get("accounts", [])
    for _a in _acct_list:
        _aid = _a.get("id", "")
        if _aid:
            _live_values[_aid] = {
                "total": float(_a.get("valueInBaseCurrency", 0) or 0),
                "cash": float(_a.get("balance", 0) or 0),
            }
except Exception:
    pass  # Ghostfolio unavailable — fall back to audit log values

# Account cards — grouped by strategy
trading_accounts = {k: v for k, v in accounts.items() if v.get("cycle_type") != "research"}

audit = AuditLogger()
initial_budget = config.get("defaults", {}).get("initial_budget", 10000)

# Group accounts by strategy
_STRATEGY_LABELS = {
    "core_satellite": "Core-Satellite",
    "value_investing": "Value Investing",
    "momentum": "Momentum",
    "wheel": "Wheel Strategy",
    "vertical_spreads": "Options Spreads",
}
groups: dict[str, list[tuple[str, dict]]] = {}
for key, acct in trading_accounts.items():
    strategy = acct.get("strategy", "other")
    # Separate intraday momentum from daily momentum
    if strategy == "momentum" and acct.get("cycle_type") == "intraday":
        strategy = "intraday"
    groups.setdefault(strategy, []).append((key, acct))

_GROUP_ORDER = ["core_satellite", "value_investing", "momentum", "intraday", "wheel", "vertical_spreads"]
_GROUP_ICONS = {
    "core_satellite": "🏛️",
    "value_investing": "💎",
    "momentum": "🚀",
    "intraday": "⚡",
    "wheel": "🎡",
    "vertical_spreads": "📈",
}

for strategy_key in _GROUP_ORDER:
    acct_list = groups.get(strategy_key)
    if not acct_list:
        continue

    icon = _GROUP_ICONS.get(strategy_key, "📊")
    label = _STRATEGY_LABELS.get(strategy_key, strategy_key.replace("_", " ").title())
    if strategy_key == "intraday":
        label = "Intraday Momentum"
    st.subheader(f"{icon} {label}")

    cols = st.columns(len(acct_list))
    for i, (key, acct) in enumerate(acct_list):
        with cols[i]:
            name = acct.get("name", key)
            model = acct.get("model", "Unknown")
            cron = acct.get("cron", "")

            logs = audit.get_recent_logs(account_key=key, limit=1)
            latest = logs[0] if logs else {}

            last_regime = latest.get("market_regime", "N/A")
            success = latest.get("success", 1)

            acct_id = acct.get("ghostfolio_account_id", "")
            live = _live_values.get(acct_id, {})
            if live.get("total"):
                value = live["total"]
                pl_pct = (value - initial_budget) / initial_budget * 100 if initial_budget else None
            else:
                value = latest.get("portfolio_value")
                pl_pct = latest.get("portfolio_pl_pct")

            st.markdown(f"**{name}**")
            if value is not None:
                delta = f"{pl_pct:+.2f}%" if pl_pct is not None else None
                st.metric("Portfolio Value", f"${value:,.2f}", delta=delta)
            else:
                st.metric("Portfolio Value", f"${initial_budget:,.2f}", delta="New")

            st.caption(f"Model: **{model}**")
            human = cron_to_human(cron)
            nxt = next_run_time(cron)
            st.caption(f"🕐 {human}")
            if nxt:
                st.caption(f"📅 {nxt}")
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
