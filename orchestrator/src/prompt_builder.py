"""Build system and user prompts for the 2-pass LLM reasoning process."""

from __future__ import annotations

from .portfolio_state import PortfolioState
from .technical_indicators import TechnicalSignals


def build_pass1_messages(
    portfolio: PortfolioState,
    market_data: dict[str, dict],
    technical_signals: dict[str, TechnicalSignals],
    news_text: str,
    decision_history: str,
    strategy_config: dict,
) -> list[dict[str, str]]:
    """Build messages for Pass 1: Market Analysis.

    The model analyzes market conditions and portfolio health WITHOUT making trades.
    """
    system_prompt = (
        "You are a senior financial analyst. Your job is to analyze current market "
        "conditions, portfolio health, and identify opportunities and threats.\n\n"
        "IMPORTANT RULES:\n"
        "- Do NOT recommend specific trades yet. Only analyze.\n"
        "- Be specific and data-driven in your analysis.\n"
        "- Consider both technical indicators and fundamental news.\n"
        "- Assess the overall market regime (bull, bear, sideways, high volatility).\n"
        "- Evaluate portfolio diversification and risk concentration.\n\n"
        "You MUST respond with valid JSON matching this schema:\n"
        "{\n"
        '  "market_regime": "BULL_TREND" | "BEAR_TREND" | "SIDEWAYS" | "HIGH_VOLATILITY",\n'
        '  "regime_reasoning": "string explaining why",\n'
        '  "sector_analysis": { "sector_name": "OVERWEIGHT|NEUTRAL|UNDERWEIGHT - reason" },\n'
        '  "portfolio_health": {\n'
        '    "diversification": "GOOD" | "POOR" | "CONCENTRATED",\n'
        '    "risk_level": "LOW" | "MEDIUM" | "HIGH",\n'
        '    "issues": ["list of issues or empty"]\n'
        "  },\n"
        '  "opportunities": [\n'
        '    {"symbol": "X", "signal": "reason this is an opportunity"}\n'
        "  ],\n"
        '  "threats": [\n'
        '    {"description": "macro or specific threat"}\n'
        "  ]\n"
        "}\n"
    )

    # Build market data section
    market_lines = ["== MARKET DATA =="]
    for symbol, data in market_data.items():
        if isinstance(data, dict):
            price = data.get("price", "N/A")
            market_lines.append(f"{symbol}: price=${price}")
    market_text = "\n".join(market_lines)

    # Build technical indicators section
    tech_lines = ["== TECHNICAL INDICATORS =="]
    for symbol, signals in technical_signals.items():
        summary = signals.to_summary()
        parts = [f"{k}={v}" for k, v in summary.items()]
        tech_lines.append(f"{symbol}: {', '.join(parts)}")
    tech_text = "\n".join(tech_lines)

    # Strategy context
    strategy_text = (
        f"== ACCOUNT STRATEGY ==\n"
        f"Strategy: {strategy_config.get('strategy', 'balanced')}\n"
        f"Description: {strategy_config.get('strategy_description', '')}\n"
        f"Horizon: {strategy_config.get('horizon', 'weeks to months')}\n"
        f"Preferred metrics: {', '.join(strategy_config.get('preferred_metrics', []))}\n"
    )

    user_prompt = (
        f"{portfolio.to_prompt_text()}\n\n"
        f"{market_text}\n\n"
        f"{tech_text}\n\n"
        f"{news_text}\n\n"
        f"{decision_history}\n\n"
        f"{strategy_text}\n\n"
        "Analyze the current situation and respond with the JSON analysis."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_pass2_messages(
    analysis_json: dict,
    portfolio: PortfolioState,
    strategy_config: dict,
    risk_profile: dict,
) -> list[dict[str, str]]:
    """Build messages for Pass 2: Trading Decision.

    The model receives the analysis and decides specific trades.
    """
    import json

    strategy = strategy_config.get("strategy", "balanced")
    prompt_style = strategy_config.get("prompt_style", "")
    horizon = strategy_config.get("horizon", "weeks to months")
    max_trades = risk_profile.get("max_trades_per_cycle", 5)
    max_position_pct = risk_profile.get("max_position_pct", 20)
    min_cash_pct = risk_profile.get("min_cash_pct", 10)
    stop_loss_pct = risk_profile.get("stop_loss_pct", -15)
    watchlist = strategy_config.get("watchlist", [])

    system_prompt = (
        f"You are a portfolio manager executing a {strategy} strategy.\n"
        f"Style: {prompt_style}\n"
        f"Investment horizon: {horizon}\n\n"
        "Based on the market analysis below, decide specific trades.\n\n"
        "RULES:\n"
        f"- Maximum {max_trades} trades per cycle\n"
        f"- No single position > {max_position_pct}% of portfolio\n"
        f"- Keep minimum {min_cash_pct}% cash reserve\n"
        f"- Stop-loss at {stop_loss_pct}% per position\n"
        f"- Only trade symbols from the watchlist: {', '.join(watchlist)}\n"
        "- You MUST justify every action with a specific thesis\n"
        "- Consider position sizing carefully\n"
        "- If no good opportunities exist, it's OK to HOLD (empty actions list)\n\n"
        "You MUST respond with valid JSON matching this schema:\n"
        "{\n"
        '  "reasoning": "Detailed chain of thought explaining your overall approach",\n'
        '  "actions": [\n'
        "    {\n"
        '      "type": "BUY" | "SELL",\n'
        '      "symbol": "TICKER",\n'
        '      "amount_usd": 1000,\n'
        '      "urgency": "HIGH" | "MEDIUM" | "LOW",\n'
        '      "thesis": "Why this trade makes sense given the analysis",\n'
        '      "exit_condition": "When to exit this position"\n'
        "    }\n"
        "  ],\n"
        '  "portfolio_outlook": "BULLISH" | "CAUTIOUSLY_BULLISH" | "NEUTRAL" | "CAUTIOUSLY_BEARISH" | "BEARISH",\n'
        '  "confidence": 0.0 to 1.0,\n'
        '  "next_cycle_focus": "What to watch for in the next decision cycle"\n'
        "}\n"
    )

    user_prompt = (
        f"== MARKET ANALYSIS (from senior analyst) ==\n"
        f"{json.dumps(analysis_json, indent=2)}\n\n"
        f"{portfolio.to_prompt_text()}\n\n"
        f"Available cash for new BUYs: ${portfolio.cash:,.2f}\n"
        f"Minimum cash to maintain: ${portfolio.total_value * min_cash_pct / 100:,.2f}\n"
        f"Maximum investable: ${max(0, portfolio.cash - portfolio.total_value * min_cash_pct / 100):,.2f}\n\n"
        f"Decide your trades and respond with the JSON decision."
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def format_decision_history(history: list[dict], max_entries: int = 4) -> str:
    """Format past decisions for prompt injection (model memory)."""
    if not history:
        return "== YOUR PREVIOUS DECISIONS ==\n(No previous decisions - this is your first cycle)"

    lines = ["== YOUR PREVIOUS DECISIONS (last cycles) =="]
    for entry in history[-max_entries:]:
        date = entry.get("date", "Unknown")
        outlook = entry.get("outlook", "Unknown")
        confidence = entry.get("confidence", "N/A")
        lines.append(f"\n[{date}] Outlook: {outlook}, Confidence: {confidence}")

        actions = entry.get("actions", [])
        if actions:
            for action in actions:
                result_str = ""
                if action.get("result_pct") is not None:
                    result_str = f" | Result: {action['result_pct']:+.1f}%"
                lines.append(
                    f"  {action.get('type', '?')} {action.get('symbol', '?')} "
                    f"${action.get('amount_usd', 0):,.0f} "
                    f"(thesis: \"{action.get('thesis', '')}\")"
                    f"{result_str}"
                )
        else:
            lines.append(f"  HOLD (no trades)")
            reason = entry.get("hold_reason", "")
            if reason:
                lines.append(f"  Reason: \"{reason}\"")

    return "\n".join(lines)
