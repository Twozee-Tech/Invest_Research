"""Integration test: actual LLM interaction via llama-swap.

Requires llama-swap running at 192.168.0.169:8080/v1.
"""

import pytest
from src.llm_client import LLMClient
from src.prompt_builder import build_pass1_messages, build_pass2_messages
from src.decision_parser import parse_analysis, parse_decision
from src.portfolio_state import PortfolioState, Position
from src.technical_indicators import TechnicalSignals

LLM_BASE_URL = "http://192.168.0.169:8080/v1"


@pytest.fixture(scope="module")
def llm():
    client = LLMClient(base_url=LLM_BASE_URL)
    yield client
    client.close()


class TestLLMConnectivity:
    def test_list_models(self, llm):
        models = llm.list_models()
        assert len(models) > 0, "No models available from llama-swap"
        print(f"\nAvailable models: {models}")

    def test_simple_chat(self, llm):
        response = llm.chat(
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Reply briefly."},
                {"role": "user", "content": "What is 2+2? Reply with just the number."},
            ],
            model="Qwen3-Next",
            temperature=0.1,
            max_tokens=64,
        )
        print(f"\nSimple chat response: '{response.strip()}'")
        assert len(response.strip()) > 0, "LLM returned empty response"
        assert "4" in response, f"Expected '4' in response, got: {response}"

    def test_json_extraction(self, llm):
        result = llm.chat_json(
            messages=[
                {"role": "system", "content": "You must reply with valid JSON only."},
                {"role": "user", "content": 'Reply with: {"status": "ok", "value": 42}'},
            ],
            model="Qwen3-Next",
            temperature=0.1,
            max_tokens=64,
        )
        assert isinstance(result, dict)
        assert result.get("status") == "ok"
        print(f"\nJSON response: {result}")


class TestLLMPass1Analysis:
    def test_market_analysis(self, llm):
        """Full Pass 1: send portfolio + market data, get structured analysis."""
        portfolio = PortfolioState(
            account_id="test",
            account_name="Test Weekly",
            total_value=10000,
            cash=4000,
            invested=6000,
            positions=[
                Position(
                    symbol="SPY", name="SPDR S&P 500", quantity=10,
                    avg_cost=500, current_price=520, market_value=5200,
                    unrealized_pl=200, unrealized_pl_pct=4.0, sector="ETF",
                    weight_pct=52.0,
                ),
                Position(
                    symbol="AAPL", name="Apple Inc", quantity=3,
                    avg_cost=220, current_price=240, market_value=720,
                    unrealized_pl=60, unrealized_pl_pct=9.1, sector="Technology",
                    weight_pct=7.2,
                ),
            ],
        )

        market_data = {
            "SPY": {"price": 520, "change_pct": 0.3, "pe": 22, "sector": "ETF"},
            "QQQ": {"price": 450, "change_pct": 0.5, "pe": 28, "sector": "ETF"},
            "AAPL": {"price": 240, "change_pct": -0.2, "pe": 30, "sector": "Technology"},
            "NVDA": {"price": 130, "change_pct": 1.2, "pe": 55, "sector": "Technology"},
        }

        tech_signals = {
            "SPY": TechnicalSignals(symbol="SPY", price=520, sma_50=510, sma_200=490, rsi_14=58),
            "AAPL": TechnicalSignals(symbol="AAPL", price=240, sma_50=235, rsi_14=62, macd_histogram=0.5),
            "NVDA": TechnicalSignals(symbol="NVDA", price=130, sma_50=125, rsi_14=71, macd_histogram=1.2),
        }

        news_text = (
            "== RECENT NEWS ==\n"
            "1. [cnbc] Fed signals potential rate cut in Q2\n"
            "2. [yahoo] NVIDIA beats earnings expectations, AI demand surges\n"
            "3. [reuters] US GDP growth slows to 2.1% in Q4"
        )

        strategy_config = {
            "strategy": "core_satellite",
            "strategy_description": "Core-Satellite: 60% ETF + 30% stocks + 10% cash",
            "horizon": "weeks to months",
            "preferred_metrics": ["SMA", "RSI", "PE"],
        }

        messages = build_pass1_messages(
            portfolio=portfolio,
            market_data=market_data,
            technical_signals=tech_signals,
            news_text=news_text,
            decision_history="== YOUR PREVIOUS DECISIONS ==\n(No previous decisions - this is your first cycle)",
            strategy_config=strategy_config,
        )

        # Call LLM
        raw = llm.chat_json(
            messages=messages,
            model="Qwen3-Next",
            fallback_model="Nemotron",
            temperature=0.7,
            max_tokens=2048,
        )

        print(f"\nPass 1 raw response: {raw}")

        # Parse and validate
        analysis = parse_analysis(raw)
        assert analysis.market_regime in ("BULL_TREND", "BEAR_TREND", "SIDEWAYS", "HIGH_VOLATILITY")
        assert isinstance(analysis.portfolio_health.diversification, str)
        print(f"\nMarket regime: {analysis.market_regime}")
        print(f"Portfolio health: {analysis.portfolio_health}")
        print(f"Opportunities: {[o.symbol for o in analysis.opportunities]}")
        print(f"Threats: {[t.description[:60] for t in analysis.threats]}")


class TestLLMPass2Decision:
    def test_trading_decision(self, llm):
        """Full Pass 2: send analysis + portfolio, get trade decisions."""
        portfolio = PortfolioState(
            account_id="test",
            account_name="Test Weekly",
            total_value=10000,
            cash=4000,
            invested=6000,
            positions=[
                Position(
                    symbol="SPY", name="SPDR S&P 500", quantity=10,
                    avg_cost=500, current_price=520, market_value=5200,
                    unrealized_pl=200, unrealized_pl_pct=4.0, sector="ETF",
                    weight_pct=52.0,
                ),
            ],
        )

        analysis_json = {
            "market_regime": "BULL_TREND",
            "regime_reasoning": "Major indices trending up with positive momentum",
            "sector_analysis": {
                "Technology": "OVERWEIGHT - AI demand driving growth",
                "Healthcare": "NEUTRAL - mixed earnings",
            },
            "portfolio_health": {
                "diversification": "POOR",
                "risk_level": "MEDIUM",
                "issues": ["concentrated in SPY", "no individual stock exposure"],
            },
            "opportunities": [
                {"symbol": "NVDA", "signal": "Strong earnings, AI momentum"},
                {"symbol": "VTI", "signal": "Broad market exposure at fair value"},
            ],
            "threats": [
                {"description": "Fed meeting next week could impact sentiment"},
            ],
        }

        strategy_config = {
            "strategy": "core_satellite",
            "prompt_style": "Balance risk and return. Prefer broad ETF exposure as core.",
            "horizon": "weeks to months",
            "watchlist": ["SPY", "QQQ", "VTI", "AAPL", "MSFT", "NVDA", "META", "JPM"],
        }

        risk_profile = {
            "max_trades_per_cycle": 5,
            "max_position_pct": 20,
            "min_cash_pct": 10,
            "stop_loss_pct": -15,
        }

        messages = build_pass2_messages(
            analysis_json=analysis_json,
            portfolio=portfolio,
            strategy_config=strategy_config,
            risk_profile=risk_profile,
        )

        # Call LLM
        raw = llm.chat_json(
            messages=messages,
            model="Qwen3-Next",
            fallback_model="Nemotron",
            temperature=0.5,
            max_tokens=2048,
        )

        print(f"\nPass 2 raw response: {raw}")

        # Parse and validate
        decision = parse_decision(raw)
        assert decision.portfolio_outlook in (
            "BULLISH", "CAUTIOUSLY_BULLISH", "NEUTRAL", "CAUTIOUSLY_BEARISH", "BEARISH"
        )
        assert 0.0 <= decision.confidence <= 1.0
        assert isinstance(decision.actions, list)

        print(f"\nOutlook: {decision.portfolio_outlook} (confidence: {decision.confidence})")
        print(f"Reasoning: {decision.reasoning[:200]}...")
        for a in decision.actions:
            print(f"  {a.type} {a.symbol} ${a.amount_usd:.0f} [{a.urgency}] - {a.thesis[:80]}")
        print(f"Next focus: {decision.next_cycle_focus}")

        # Validate trade actions make sense
        for action in decision.actions:
            assert action.type in ("BUY", "SELL")
            assert action.amount_usd > 0
            assert action.symbol in strategy_config["watchlist"], \
                f"Model suggested {action.symbol} which is not in watchlist"
