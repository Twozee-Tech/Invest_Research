"""Tests for the prompt builder module."""

from src.prompt_builder import (
    build_pass1_messages,
    build_pass2_messages,
    format_decision_history,
)
from src.portfolio_state import PortfolioState, Position
from src.technical_indicators import TechnicalSignals


class TestBuildPass1:
    def test_basic_structure(self):
        portfolio = PortfolioState(
            account_id="test",
            account_name="Test Account",
            total_value=10000,
            cash=5000,
            invested=5000,
        )
        messages = build_pass1_messages(
            portfolio=portfolio,
            market_data={"SPY": {"price": 500}},
            technical_signals={"SPY": TechnicalSignals(symbol="SPY", rsi_14=55, price=500)},
            news_text="== RECENT NEWS ==\n1. Market rallies",
            decision_history="== YOUR PREVIOUS DECISIONS ==\n(none)",
            strategy_config={
                "strategy": "core_satellite",
                "strategy_description": "Core-Satellite",
                "horizon": "weeks to months",
                "preferred_metrics": ["SMA", "RSI"],
            },
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "financial analyst" in messages[0]["content"].lower()
        assert "Test Account" in messages[1]["content"]
        assert "SPY" in messages[1]["content"]

    def test_json_schema_in_system(self):
        portfolio = PortfolioState(
            account_id="t", account_name="T", total_value=10000, cash=5000, invested=5000,
        )
        messages = build_pass1_messages(
            portfolio=portfolio,
            market_data={},
            technical_signals={},
            news_text="",
            decision_history="",
            strategy_config={},
        )
        system = messages[0]["content"]
        assert "market_regime" in system
        assert "BULL_TREND" in system
        assert "JSON" in system


class TestBuildPass2:
    def test_basic_structure(self):
        portfolio = PortfolioState(
            account_id="test",
            account_name="Test",
            total_value=10000,
            cash=3000,
            invested=7000,
        )
        messages = build_pass2_messages(
            analysis_json={"market_regime": "BULL_TREND"},
            portfolio=portfolio,
            strategy_config={
                "strategy": "core_satellite",
                "prompt_style": "Balance risk and return",
                "horizon": "weeks to months",
                "watchlist": ["SPY", "QQQ"],
            },
            risk_profile={
                "max_trades_per_cycle": 5,
                "max_position_pct": 20,
                "min_cash_pct": 10,
                "stop_loss_pct": -15,
            },
        )
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert "portfolio manager" in messages[0]["content"].lower()
        assert "SPY" in messages[0]["content"]
        assert "BULL_TREND" in messages[1]["content"]

    def test_cash_calculation(self):
        portfolio = PortfolioState(
            account_id="test",
            account_name="Test",
            total_value=10000,
            cash=2000,
            invested=8000,
        )
        messages = build_pass2_messages(
            analysis_json={},
            portfolio=portfolio,
            strategy_config={"watchlist": []},
            risk_profile={"min_cash_pct": 10, "max_trades_per_cycle": 5, "max_position_pct": 20, "stop_loss_pct": -15},
        )
        user_content = messages[1]["content"]
        # min cash = 10% of 10000 = 1000, investable = 2000 - 1000 = 1000
        assert "$1,000.00" in user_content


class TestDecisionHistory:
    def test_empty_history(self):
        result = format_decision_history([])
        assert "first cycle" in result.lower()

    def test_with_actions(self):
        history = [
            {
                "date": "2026-02-09",
                "outlook": "BULLISH",
                "confidence": 0.68,
                "actions": [
                    {"type": "BUY", "symbol": "SPY", "amount_usd": 2000, "thesis": "core position"},
                ],
            },
        ]
        result = format_decision_history(history)
        assert "2026-02-09" in result
        assert "BULLISH" in result
        assert "SPY" in result
        assert "core position" in result

    def test_hold_entry(self):
        history = [
            {
                "date": "2026-02-02",
                "outlook": "NEUTRAL",
                "confidence": 0.55,
                "actions": [],
                "hold_reason": "Waiting for clarity",
            },
        ]
        result = format_decision_history(history)
        assert "HOLD" in result
        assert "Waiting for clarity" in result

    def test_max_entries(self):
        history = [{"date": f"2026-01-{i:02d}", "outlook": "N", "confidence": 0.5, "actions": []} for i in range(10)]
        result = format_decision_history(history, max_entries=3)
        # Should only include last 3 (indices 7, 8, 9)
        assert "2026-01-06" not in result
        assert "2026-01-07" in result
        assert "2026-01-09" in result
