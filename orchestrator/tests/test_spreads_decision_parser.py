"""Tests for options/spreads_decision_parser.py."""

from orchestrator.src.options.spreads_decision_parser import (
    SpreadAction,
    SpreadDecision,
    parse_spreads_decision,
)


class TestParseSpreadsDecision:
    """Test parse_spreads_decision() with various LLM outputs."""

    def test_valid_full_response(self):
        raw = {
            "market_comment": "Sideways market, high IV",
            "outlook": "NEUTRAL",
            "confidence": 0.8,
            "actions": [
                {
                    "type": "OPEN_SPREAD",
                    "symbol": "SPY",
                    "spread_type": "iron_condor",
                    "contracts": 2,
                    "reason": "Range-bound, IV at 70th pct",
                },
                {
                    "type": "CLOSE",
                    "symbol": "AAPL",
                    "position_id": 5,
                    "reason": "Captured 65% of max premium",
                },
                {
                    "type": "SKIP",
                    "symbol": "TSLA",
                    "reason": "Earnings in 3 days",
                },
            ],
        }
        d = parse_spreads_decision(raw)
        assert len(d.actions) == 3
        assert len(d.open_new) == 1
        assert len(d.close_positions) == 1
        assert d.outlook == "NEUTRAL"
        assert d.confidence == 0.8
        assert d.market_comment == "Sideways market, high IV"

        open_action = d.open_new[0]
        assert open_action.symbol == "SPY"
        assert open_action.spread_type == "iron_condor"
        assert open_action.contracts == 2

        close_action = d.close_positions[0]
        assert close_action.position_id == 5

    def test_all_spread_types_accepted(self):
        valid_types = [
            "iron_condor", "bull_call", "bear_put",
            "bull_put", "bear_call", "butterfly",
        ]
        for st in valid_types:
            raw = {
                "actions": [
                    {"type": "OPEN_SPREAD", "symbol": "SPY", "spread_type": st}
                ]
            }
            d = parse_spreads_decision(raw)
            assert len(d.open_new) == 1, f"spread_type '{st}' should be accepted"
            assert d.open_new[0].spread_type == st

    def test_invalid_spread_type_rejected(self):
        raw = {
            "actions": [
                {"type": "OPEN_SPREAD", "symbol": "SPY", "spread_type": "straddle"},
                {"type": "OPEN_SPREAD", "symbol": "AAPL", "spread_type": ""},
                {"type": "OPEN_SPREAD", "symbol": "MSFT"},  # missing spread_type
            ]
        }
        d = parse_spreads_decision(raw)
        assert len(d.actions) == 0

    def test_close_requires_position_id(self):
        raw = {
            "actions": [
                {"type": "CLOSE", "symbol": "SPY", "reason": "take profit"},
                {"type": "CLOSE", "symbol": "AAPL", "position_id": 3, "reason": "ok"},
            ]
        }
        d = parse_spreads_decision(raw)
        assert len(d.close_positions) == 1
        assert d.close_positions[0].symbol == "AAPL"

    def test_skip_no_symbol_ok(self):
        """SKIP actions can have empty symbol."""
        raw = {
            "actions": [
                {"type": "SKIP", "symbol": "", "reason": "market uncertain"},
            ]
        }
        d = parse_spreads_decision(raw)
        # SKIP with empty symbol is allowed
        assert len(d.actions) == 1

    def test_open_spread_requires_symbol(self):
        raw = {
            "actions": [
                {"type": "OPEN_SPREAD", "symbol": "", "spread_type": "bull_call"},
            ]
        }
        d = parse_spreads_decision(raw)
        assert len(d.actions) == 0

    def test_symbol_uppercased(self):
        raw = {
            "actions": [
                {"type": "OPEN_SPREAD", "symbol": "spy", "spread_type": "bull_call"},
            ]
        }
        d = parse_spreads_decision(raw)
        assert d.actions[0].symbol == "SPY"

    def test_contracts_defaults_to_1(self):
        raw = {
            "actions": [
                {"type": "OPEN_SPREAD", "symbol": "SPY", "spread_type": "bull_call"},
            ]
        }
        d = parse_spreads_decision(raw)
        assert d.actions[0].contracts == 1

    def test_contracts_clamped_to_min_1(self):
        raw = {
            "actions": [
                {"type": "OPEN_SPREAD", "symbol": "SPY", "spread_type": "bull_call", "contracts": -5},
            ]
        }
        d = parse_spreads_decision(raw)
        assert d.actions[0].contracts == 1

    def test_outlook_normalization(self):
        for raw_outlook, expected in [
            ("bullish", "BULLISH"),
            ("cautiously bullish", "CAUTIOUSLY_BULLISH"),
            ("BEARISH", "BEARISH"),
            ("garbage", "NEUTRAL"),  # defaults
            ("", "NEUTRAL"),
        ]:
            d = parse_spreads_decision({"outlook": raw_outlook, "actions": []})
            assert d.outlook == expected, f"{raw_outlook!r} → {d.outlook}, expected {expected}"

    def test_confidence_clamped(self):
        d = parse_spreads_decision({"confidence": 1.5, "actions": []})
        assert d.confidence == 1.0

        d = parse_spreads_decision({"confidence": -0.3, "actions": []})
        assert d.confidence == 0.0

    def test_confidence_defaults(self):
        d = parse_spreads_decision({"actions": []})
        assert d.confidence == 0.7

    def test_non_dict_input(self):
        d = parse_spreads_decision("not a dict")
        assert len(d.actions) == 0
        assert d.outlook == "NEUTRAL"

    def test_empty_dict(self):
        d = parse_spreads_decision({})
        assert len(d.actions) == 0

    def test_actions_not_a_list(self):
        d = parse_spreads_decision({"actions": "not a list"})
        assert len(d.actions) == 0

    def test_action_not_a_dict(self):
        d = parse_spreads_decision({"actions": ["string", 42, None]})
        assert len(d.actions) == 0

    def test_unknown_action_type_skipped(self):
        raw = {
            "actions": [
                {"type": "SELL_CSP", "symbol": "SPY"},  # wheel action, not valid here
                {"type": "BUY", "symbol": "AAPL"},
                {"type": "OPEN_SPREAD", "symbol": "MSFT", "spread_type": "bull_call"},
            ]
        }
        d = parse_spreads_decision(raw)
        assert len(d.actions) == 1
        assert d.actions[0].symbol == "MSFT"

    def test_market_comment_alias(self):
        """Falls back to 'reasoning' key."""
        d = parse_spreads_decision({"reasoning": "test comment", "actions": []})
        assert d.market_comment == "test comment"

    def test_outlook_alias(self):
        """Falls back to 'portfolio_outlook' key."""
        d = parse_spreads_decision({"portfolio_outlook": "BEARISH", "actions": []})
        assert d.outlook == "BEARISH"

    def test_position_id_string_parsed(self):
        """position_id can be passed as string."""
        raw = {
            "actions": [
                {"type": "CLOSE", "symbol": "SPY", "position_id": "7"},
            ]
        }
        d = parse_spreads_decision(raw)
        assert d.close_positions[0].position_id == 7

    def test_compatibility_properties(self):
        d = SpreadDecision(
            actions=[
                SpreadAction(type="OPEN_SPREAD", symbol="SPY", spread_type="iron_condor"),
                SpreadAction(type="CLOSE", symbol="AAPL", position_id=1),
                SpreadAction(type="SKIP", symbol="TSLA"),
            ],
            outlook="BULLISH",
        )
        assert len(d.open_new) == 1
        assert len(d.close_positions) == 1
        assert d.roll_positions == []
        assert d.portfolio_outlook == "BULLISH"
