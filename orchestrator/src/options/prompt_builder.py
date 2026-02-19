"""Build LLM prompts for options trading decisions."""

from __future__ import annotations

import json

from ..portfolio_state import PortfolioState
from .greeks import PortfolioGreeks
from .positions import OptionsPosition


def build_options_pass1_messages(
    portfolio: PortfolioState,
    market_data: dict[str, dict],
    technical_signals: dict,
    news_text: str,
    strategy_config: dict,
    active_positions: list[OptionsPosition],
    iv_data: dict[str, float | None],       # symbol → IV percentile
    portfolio_greeks: PortfolioGreeks,
) -> list[dict]:
    """Pass 1: Market analysis with IV regime focus."""

    system = (
        "You are an options market analyst. Analyze market conditions, implied volatility "
        "regime, and directional bias for each underlying. "
        "Do NOT decide specific trades yet — that comes in Pass 2. "
        "Output valid JSON only, no markdown."
    )

    # Format active positions
    pos_text = _format_active_positions(active_positions, portfolio_greeks)

    # Format market data with IV
    mkt_text = _format_market_with_iv(market_data, technical_signals, iv_data)

    user = f"""{portfolio.to_prompt_text()}

{pos_text}

== WATCHLIST ANALYSIS ==
{mkt_text}

== RECENT NEWS ==
{news_text or "No recent news."}

Analyze the above and return JSON:
{{
  "market_regime": "BULL_TREND|BEAR_TREND|SIDEWAYS|HIGH_VOLATILITY",
  "regime_reasoning": "brief explanation",
  "iv_regime": "HIGH|NORMAL|LOW",
  "iv_reasoning": "is premium selling or buying favored?",
  "sector_analysis": {{"sector_name": "BULLISH|NEUTRAL|BEARISH - reason"}},
  "per_symbol": {{
    "SYMBOL": {{
      "bias": "BULLISH|NEUTRAL|BEARISH",
      "iv_percentile": 45,
      "action_suggestion": "SELL_PREMIUM|BUY_DEBIT|AVOID",
      "reason": "brief"
    }}
  }},
  "portfolio_health": {{
    "diversification": "GOOD|POOR|CONCENTRATED",
    "risk_level": "LOW|MEDIUM|HIGH",
    "issues": ["list"]
  }},
  "threats": [{{"description": "macro or specific threat"}}]
}}"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def build_options_pass2_messages(
    analysis_json: dict,
    portfolio: PortfolioState,
    strategy_config: dict,
    risk_profile: dict,
    active_positions: list[OptionsPosition],
    portfolio_greeks: PortfolioGreeks,
    decision_history: str = "",
) -> list[dict]:
    """Pass 2: Concrete open/close/roll decisions."""

    risk_text = _format_options_risk_rules(risk_profile)
    pos_text = _format_active_positions_detailed(active_positions)
    watchlist = strategy_config.get("watchlist", [])
    strategy_desc = strategy_config.get("strategy_description", "")

    system = (
        "You are an options portfolio manager running vertical spreads. "
        "Based on the market analysis, decide which positions to HOLD, CLOSE, ROLL, "
        "or which new spreads to OPEN. "
        "You decide the direction and spread type — the system will pick exact strikes. "
        "You do NOT pick strikes or expiration dates. "
        "Output valid JSON only, no markdown."
    )

    user = f"""== STRATEGY: {strategy_desc} ==

== MARKET ANALYSIS ==
{json.dumps(analysis_json, indent=2)}

== CURRENT PORTFOLIO GREEKS ==
Net Delta: {portfolio_greeks.total_delta:+.2f} | Theta: ${portfolio_greeks.total_theta:+.2f}/day | Vega: ${portfolio_greeks.total_vega:+.2f}/1%IV
Cash: ${portfolio.cash:,.2f} ({portfolio.cash_pct:.1f}% of account)
Open positions: {portfolio_greeks.position_count}

== ACTIVE OPTION POSITIONS ==
{pos_text}

== RISK RULES ==
{risk_text}

== AVAILABLE WATCHLIST ==
{", ".join(watchlist)}

== YOUR PREVIOUS DECISIONS ==
{decision_history or "No history yet."}

Decide what to do with each position and what new spreads to open.
Return JSON:
{{
  "reasoning": "detailed chain of thought about market direction and Greeks management",
  "hold_positions": [1, 2],
  "close_positions": [
    {{"id": 3, "reason": "DTE < 7, approaching expiry"}}
  ],
  "roll_positions": [
    {{"id": 4, "direction": "bearish", "spread_type": "BEAR_PUT"}}
  ],
  "open_new": [
    {{
      "symbol": "SPY",
      "direction": "bearish",
      "spread_type": "BEAR_PUT",
      "size": "small|medium|large",
      "thesis": "SPY showing weakness, IV at 65th percentile favors selling premium"
    }}
  ],
  "portfolio_outlook": "BULLISH|CAUTIOUSLY_BULLISH|NEUTRAL|CAUTIOUSLY_BEARISH|BEARISH",
  "confidence": 0.0
}}

Rules:
- close_positions and hold_positions: use the position IDs from active positions above
- size: "small" = 1 contract, "medium" = 2 contracts, "large" = 3 contracts
- Only open new spreads if you have a clear directional thesis
- NEUTRAL market = prefer HOLD over opening new positions
"""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ── Formatters ────────────────────────────────────────────────────────────────

def _format_active_positions(
    positions: list[OptionsPosition],
    portfolio_greeks: PortfolioGreeks,
) -> str:
    if not positions:
        return "== ACTIVE OPTION POSITIONS ==\n(none — cash only)"

    lines = [
        "== ACTIVE OPTION POSITIONS ==",
        f"Portfolio Greeks: Δ{portfolio_greeks.total_delta:+.2f} | "
        f"Θ${portfolio_greeks.total_theta:+.2f}/day | "
        f"Ν${portfolio_greeks.total_vega:+.2f}/1%IV",
        "",
    ]
    for p in positions:
        pl_str = f"${p.current_pl:+,.2f} ({p.pl_pct:+.1f}%)" if p.current_pl is not None else "N/A"
        captured = f"{p.profit_captured_pct:.0f}% of max profit" if p.profit_captured_pct is not None else ""
        g = p.current_greeks or {}
        lines.append(
            f"[{p.id}] {p.symbol} {p.spread_type} {p.buy_strike}/{p.sell_strike} "
            f"exp {p.expiration_date} DTE:{p.dte} | "
            f"P&L: {pl_str} {captured} | "
            f"Δ{g.get('net_delta', 0):+.2f} Θ${g.get('net_theta', 0):+.2f}/day"
        )
    return "\n".join(lines)


def _format_active_positions_detailed(positions: list[OptionsPosition]) -> str:
    if not positions:
        return "(none)"
    lines = []
    for p in positions:
        pl_pct = f"{p.profit_captured_pct:.0f}%" if p.profit_captured_pct is not None else "?"
        lines.append(
            f"ID:{p.id} | {p.symbol} {p.spread_type} | "
            f"Buy {p.buy_strike}{p.buy_option_type[0].upper()} / "
            f"Sell {p.sell_strike}{p.sell_option_type[0].upper()} | "
            f"Exp:{p.expiration_date} DTE:{p.dte} | "
            f"Entry debit:${p.entry_debit:.2f} | "
            f"Max profit:${p.max_profit:.2f} Max loss:${p.max_loss:.2f} | "
            f"Profit captured: {pl_pct}"
        )
    return "\n".join(lines)


def _format_market_with_iv(
    market_data: dict,
    technical_signals: dict,
    iv_data: dict,
) -> str:
    lines = []
    for sym, data in market_data.items():
        price = data.get("price", 0)
        chg = data.get("change_pct", 0)
        iv_pct = iv_data.get(sym)
        iv_str = f"IV-pct:{iv_pct:.0f}%" if iv_pct is not None else "IV:N/A"

        sig = technical_signals.get(sym)
        tech_str = ""
        if sig:
            summary = sig.to_summary() if hasattr(sig, "to_summary") else {}
            rsi = summary.get("rsi", {}).get("value", "N/A")
            trend = summary.get("sma", {}).get("trend", "?")
            tech_str = f"RSI:{rsi} Trend:{trend}"

        lines.append(f"  {sym}: ${price:.2f} ({chg:+.2f}%) {iv_str} | {tech_str}")
    return "\n".join(lines) if lines else "(no data)"


def _format_options_risk_rules(risk_profile: dict) -> str:
    return (
        f"Max portfolio delta: ±{risk_profile.get('max_portfolio_delta_pct', 15)}% of account\n"
        f"Max open spreads: {risk_profile.get('max_open_spreads', 5)}\n"
        f"Max per spread: {risk_profile.get('max_allocation_per_spread_pct', 10)}% of cash\n"
        f"Min cash reserve: {risk_profile.get('min_cash_pct', 40)}% of account\n"
        f"Auto-close if DTE ≤ {risk_profile.get('auto_close_dte', 7)}\n"
        f"Take profit at {risk_profile.get('take_profit_pct', 75)}% of max profit\n"
        f"Stop loss at {risk_profile.get('stop_loss_pct', 50)}% of max loss"
    )
