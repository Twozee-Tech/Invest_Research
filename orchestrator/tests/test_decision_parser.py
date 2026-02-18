"""Tests for the decision parser module."""

import pytest
from src.decision_parser import (
    AnalysisResult,
    DecisionResult,
    TradeAction,
    parse_analysis,
    parse_decision,
)


class TestParseAnalysis:
    def test_valid_analysis(self):
        raw = {
            "market_regime": "BULL_TREND",
            "regime_reasoning": "Strong uptrend in major indices",
            "sector_analysis": {
                "Technology": "OVERWEIGHT - earnings momentum",
                "Healthcare": "NEUTRAL - mixed signals",
            },
            "portfolio_health": {
                "diversification": "GOOD",
                "risk_level": "MEDIUM",
                "issues": ["slight tech overweight"],
            },
            "opportunities": [
                {"symbol": "NVDA", "signal": "RSI oversold after pullback"},
            ],
            "threats": [
                {"description": "Fed rate decision upcoming"},
            ],
        }
        result = parse_analysis(raw)
        assert result.market_regime == "BULL_TREND"
        assert len(result.opportunities) == 1
        assert result.opportunities[0].symbol == "NVDA"
        assert result.portfolio_health.diversification == "GOOD"

    def test_invalid_regime_defaults_sideways(self):
        raw = {"market_regime": "CRAZY_MARKET"}
        result = parse_analysis(raw)
        assert result.market_regime == "SIDEWAYS"

    def test_empty_analysis(self):
        result = parse_analysis({})
        assert result.market_regime == "SIDEWAYS"
        assert result.opportunities == []
        assert result.threats == []

    def test_malformed_input(self):
        result = parse_analysis({"garbage": True, "market_regime": 123})
        assert isinstance(result, AnalysisResult)

    def test_threats_as_strings(self):
        """Qwen3-Next returns threats as plain strings instead of dicts."""
        raw = {
            "market_regime": "BULL_TREND",
            "threats": [
                "Cash drag: 40% cash allocation is eroding returns",
                "Overconcentration risk: 59% in SPY and AAPL",
                "NVDA RSI at 71 suggests near-term pullback risk",
            ],
        }
        result = parse_analysis(raw)
        assert result.market_regime == "BULL_TREND"
        assert len(result.threats) == 3
        assert "Cash drag" in result.threats[0].description
        assert "Overconcentration" in result.threats[1].description

    def test_threats_mixed_formats(self):
        raw = {
            "market_regime": "SIDEWAYS",
            "threats": [
                {"description": "Fed hawkish tone"},
                "Inflation rising above target",
            ],
        }
        result = parse_analysis(raw)
        assert len(result.threats) == 2
        assert result.threats[0].description == "Fed hawkish tone"
        assert "Inflation" in result.threats[1].description

    def test_opportunities_as_strings(self):
        raw = {
            "market_regime": "BULL_TREND",
            "opportunities": [
                "NVDA: Strong AI momentum and earnings beat",
                "QQQ: Tech sector rotation continuing",
            ],
        }
        result = parse_analysis(raw)
        assert len(result.opportunities) == 2
        assert result.opportunities[0].symbol == "NVDA"
        assert "AI momentum" in result.opportunities[0].signal

    def test_opportunities_as_single_key_dicts(self):
        raw = {
            "market_regime": "BULL_TREND",
            "opportunities": [
                {"NVDA": "Strong earnings, AI demand surges"},
                {"QQQ": "Broad tech momentum"},
            ],
        }
        result = parse_analysis(raw)
        assert len(result.opportunities) == 2
        assert result.opportunities[0].symbol == "NVDA"
        assert result.opportunities[1].symbol == "QQQ"

    def test_sector_analysis_nested_values(self):
        raw = {
            "market_regime": "SIDEWAYS",
            "sector_analysis": {
                "Technology": {"rating": "OVERWEIGHT", "reason": "strong"},
                "Healthcare": "NEUTRAL - mixed",
            },
        }
        result = parse_analysis(raw)
        assert "Technology" in result.sector_analysis
        assert isinstance(result.sector_analysis["Technology"], str)
        assert result.sector_analysis["Healthcare"] == "NEUTRAL - mixed"

    def test_full_qwen_style_response(self):
        """Reproduce the actual Qwen3-Next response format from integration test."""
        raw = {
            "market_regime": "BULL_TREND",
            "regime_reasoning": "SPY above SMAs, positive momentum",
            "sector_analysis": {
                "Technology": "OVERWEIGHT - AAPL and NVDA above key SMAs",
                "ETF": "NEUTRAL - SPY in bull trend",
                "Cash": "UNDERWEIGHT - 40% exceeds target",
            },
            "portfolio_health": {
                "diversification": "POOR",
                "risk_level": "MEDIUM",
                "issues": [
                    "Concentrated in SPY (52%)",
                    "Cash at 40% is above 10% target",
                ],
            },
            "opportunities": [
                {"symbol": "NVDA", "signal": "Strong earnings, AI demand"},
                {"symbol": "QQQ", "signal": "Tech momentum play"},
            ],
            "threats": [
                "Cash drag eroding returns in bull market",
                "NVDA RSI at 71 suggests pullback risk",
            ],
        }
        result = parse_analysis(raw)
        assert result.market_regime == "BULL_TREND"
        assert result.portfolio_health.diversification == "POOR"
        assert len(result.opportunities) == 2
        assert len(result.threats) == 2
        assert result.threats[0].description == "Cash drag eroding returns in bull market"


class TestParseDecision:
    def test_valid_decision(self):
        raw = {
            "reasoning": "Tech sector looks strong",
            "actions": [
                {
                    "type": "BUY",
                    "symbol": "VTI",
                    "amount_usd": 1500,
                    "urgency": "HIGH",
                    "thesis": "Broad market exposure",
                    "exit_condition": "Sell if RSI > 75",
                },
                {
                    "type": "SELL",
                    "symbol": "AAPL",
                    "amount_usd": 800,
                    "urgency": "MEDIUM",
                    "thesis": "Taking profit",
                    "exit_condition": "N/A",
                },
            ],
            "portfolio_outlook": "CAUTIOUSLY_BULLISH",
            "confidence": 0.72,
            "next_cycle_focus": "Watch Fed minutes",
        }
        result = parse_decision(raw)
        assert len(result.actions) == 2
        assert result.actions[0].type == "BUY"
        assert result.actions[0].amount_usd == 1500
        assert result.portfolio_outlook == "CAUTIOUSLY_BULLISH"
        assert result.confidence == 0.72

    def test_hold_decision(self):
        raw = {
            "reasoning": "No clear opportunities",
            "actions": [],
            "portfolio_outlook": "NEUTRAL",
            "confidence": 0.45,
            "next_cycle_focus": "Earnings season",
        }
        result = parse_decision(raw)
        assert len(result.actions) == 0
        assert result.portfolio_outlook == "NEUTRAL"

    def test_confidence_clamped(self):
        raw = {"confidence": 1.5}
        result = parse_decision(raw)
        assert result.confidence == 1.0

        raw2 = {"confidence": -0.5}
        result2 = parse_decision(raw2)
        assert result2.confidence == 0.0

    def test_invalid_trade_type(self):
        raw = {
            "actions": [
                {"type": "HOLD", "symbol": "SPY", "amount_usd": 100},
            ],
        }
        # Should fail validation for invalid type
        result = parse_decision(raw)
        # The parser catches the error and returns empty DecisionResult
        assert len(result.actions) == 0

    def test_invalid_outlook_defaults(self):
        raw = {"portfolio_outlook": "VERY_BULLISH"}
        result = parse_decision(raw)
        assert result.portfolio_outlook == "NEUTRAL"

    def test_action_key_alias(self):
        """Model returns 'action' instead of 'actions'."""
        raw = {
            "action": [{"type": "BUY", "symbol": "SPY", "amount_usd": 1000}],
            "portfolio_outlook": "BULLISH",
            "confidence": 0.7,
        }
        result = parse_decision(raw)
        assert len(result.actions) == 1
        assert result.actions[0].symbol == "SPY"

    def test_trades_key_alias(self):
        """Model returns 'trades' instead of 'actions'."""
        raw = {
            "trades": [{"type": "BUY", "symbol": "VTI", "amount_usd": 500}],
            "confidence": 0.6,
        }
        result = parse_decision(raw)
        assert len(result.actions) == 1
        assert result.actions[0].symbol == "VTI"

    def test_outlook_key_alias(self):
        """Model returns 'outlook' instead of 'portfolio_outlook'."""
        raw = {"outlook": "BEARISH", "confidence": 0.3}
        result = parse_decision(raw)
        assert result.portfolio_outlook == "BEARISH"

    def test_action_field_aliases(self):
        """Model uses 'ticker' instead of 'symbol', 'amount' instead of 'amount_usd'."""
        raw = {
            "actions": [
                {"action": "BUY", "ticker": "NVDA", "amount": 2000, "thesis": "AI play"},
            ],
            "confidence": 0.8,
        }
        result = parse_decision(raw)
        assert len(result.actions) == 1
        assert result.actions[0].symbol == "NVDA"
        assert result.actions[0].type == "BUY"
        assert result.actions[0].amount_usd == 2000

    def test_incomplete_actions_filtered(self):
        """Actions missing required fields are silently dropped."""
        raw = {
            "actions": [
                {"type": "BUY", "symbol": "VTI", "amount_usd": 1000},
                {"type": "BUY"},  # missing symbol and amount
                {"symbol": "AAPL"},  # missing type and amount
                {"type": "SELL", "symbol": "MSFT", "amount_usd": 500},
            ],
            "confidence": 0.7,
        }
        result = parse_decision(raw)
        assert len(result.actions) == 2
        assert result.actions[0].symbol == "VTI"
        assert result.actions[1].symbol == "MSFT"

    def test_single_action_dict_wrapped(self):
        """Model returns single action dict instead of list."""
        raw = {
            "actions": {"type": "BUY", "symbol": "SPY", "amount_usd": 1500},
            "confidence": 0.6,
        }
        result = parse_decision(raw)
        assert len(result.actions) == 1
        assert result.actions[0].symbol == "SPY"


class TestTradeAction:
    def test_valid_action(self):
        action = TradeAction(
            type="BUY",
            symbol="SPY",
            amount_usd=1000,
            urgency="HIGH",
            thesis="test",
        )
        assert action.type == "BUY"
        assert action.symbol == "SPY"

    def test_type_normalized_to_upper(self):
        action = TradeAction(type="buy", symbol="SPY", amount_usd=100)
        assert action.type == "BUY"

    def test_negative_amount_rejected(self):
        with pytest.raises(ValueError):
            TradeAction(type="BUY", symbol="SPY", amount_usd=-100)

    def test_urgency_normalized(self):
        action = TradeAction(type="BUY", symbol="SPY", amount_usd=100, urgency="low")
        assert action.urgency == "LOW"

    def test_invalid_urgency_defaults(self):
        action = TradeAction(type="BUY", symbol="SPY", amount_usd=100, urgency="ASAP")
        assert action.urgency == "MEDIUM"
