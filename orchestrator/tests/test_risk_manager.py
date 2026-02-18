"""Tests for the risk manager module."""

import pytest
from src.decision_parser import DecisionResult, TradeAction
from src.portfolio_state import PortfolioState, Position
from src.market_data import StockQuote
from src.risk_manager import RiskManager


def make_portfolio(
    cash: float = 5000,
    positions: list[Position] | None = None,
    total_value: float | None = None,
) -> PortfolioState:
    positions = positions or []
    total_market = sum(p.market_value for p in positions)
    tv = total_value or (total_market + cash)
    for p in positions:
        p.weight_pct = (p.market_value / tv * 100) if tv > 0 else 0
    return PortfolioState(
        account_id="test-id",
        account_name="Test",
        total_value=tv,
        cash=cash,
        invested=total_market,
        positions=positions,
    )


def make_quote(symbol: str, price: float = 100.0, avg_volume: int = 500000) -> StockQuote:
    return StockQuote(
        symbol=symbol,
        price=price,
        change_pct=0,
        volume=100000,
        avg_volume_10d=avg_volume,
        market_cap=1e9,
        pe_ratio=20,
        forward_pe=18,
        pb_ratio=3,
        dividend_yield=0.02,
        week52_high=120,
        week52_low=80,
        sector="Technology",
        industry="Software",
        name=f"{symbol} Inc",
    )


RISK_PROFILE = {
    "max_position_pct": 20,
    "min_cash_pct": 10,
    "max_trades_per_cycle": 3,
    "stop_loss_pct": -15,
    "min_holding_days": 14,
    "max_sector_exposure_pct": 40,
}


class TestRiskManagerBuy:
    def test_approve_simple_buy(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[
            TradeAction(type="BUY", symbol="AAPL", amount_usd=500, urgency="MEDIUM", thesis="test"),
        ])
        portfolio = make_portfolio(cash=5000, total_value=10000)
        quotes = {"AAPL": make_quote("AAPL")}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 1
        assert result.approved_actions[0].symbol == "AAPL"
        assert result.approved_actions[0].amount_usd == 500

    def test_reject_buy_insufficient_cash(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[
            TradeAction(type="BUY", symbol="AAPL", amount_usd=5000, urgency="MEDIUM", thesis="test"),
        ])
        # Cash = 1000, total = 10000, min_cash = 10% = 1000 -> no room to buy
        portfolio = make_portfolio(cash=1000, total_value=10000)
        quotes = {"AAPL": make_quote("AAPL")}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 0
        assert len(result.rejected_actions) == 1

    def test_trim_buy_to_cash_limit(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[
            TradeAction(type="BUY", symbol="AAPL", amount_usd=3000, urgency="MEDIUM", thesis="test"),
        ])
        # Cash = 2500, total = 10000, min_cash = 1000 -> max investable = 1500
        portfolio = make_portfolio(cash=2500, total_value=10000)
        quotes = {"AAPL": make_quote("AAPL")}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 1
        assert result.approved_actions[0].amount_usd == 1500

    def test_reject_buy_penny_stock(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[
            TradeAction(type="BUY", symbol="PENNY", amount_usd=500, urgency="MEDIUM", thesis="test"),
        ])
        portfolio = make_portfolio(cash=5000, total_value=10000)
        quotes = {"PENNY": make_quote("PENNY", price=3.0)}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 0
        assert "below" in result.rejected_actions[0].rejection_reason.lower()

    def test_reject_buy_low_volume(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[
            TradeAction(type="BUY", symbol="ILLIQ", amount_usd=500, urgency="MEDIUM", thesis="test"),
        ])
        portfolio = make_portfolio(cash=5000, total_value=10000)
        quotes = {"ILLIQ": make_quote("ILLIQ", price=10.0, avg_volume=5000)}  # $50K/day < $100K

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 0

    def test_trim_buy_max_position(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[
            TradeAction(type="BUY", symbol="AAPL", amount_usd=1500, urgency="MEDIUM", thesis="test"),
        ])
        # Already have $1500 AAPL, max position = 20% of $10000 = $2000
        existing = Position(
            symbol="AAPL", name="Apple", quantity=15, avg_cost=100,
            current_price=100, market_value=1500, unrealized_pl=0,
            unrealized_pl_pct=0, sector="Tech", weight_pct=15,
        )
        portfolio = make_portfolio(cash=5000, positions=[existing], total_value=10000)
        quotes = {"AAPL": make_quote("AAPL")}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 1
        assert result.approved_actions[0].amount_usd == 500  # 2000 - 1500


class TestRiskManagerSell:
    def test_approve_simple_sell(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[
            TradeAction(type="SELL", symbol="AAPL", amount_usd=500, urgency="MEDIUM", thesis="test"),
        ])
        existing = Position(
            symbol="AAPL", name="Apple", quantity=10, avg_cost=100,
            current_price=100, market_value=1000, unrealized_pl=0,
            unrealized_pl_pct=0, sector="Tech", first_buy_date="2020-01-01T00:00:00Z",
        )
        portfolio = make_portfolio(cash=5000, positions=[existing])
        quotes = {"AAPL": make_quote("AAPL")}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 1

    def test_reject_sell_no_position(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[
            TradeAction(type="SELL", symbol="MSFT", amount_usd=500, urgency="MEDIUM", thesis="test"),
        ])
        portfolio = make_portfolio(cash=5000)
        quotes = {"MSFT": make_quote("MSFT")}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 0
        assert "no position" in result.rejected_actions[0].rejection_reason.lower()


class TestRiskManagerStopLoss:
    def test_stop_loss_trigger(self):
        rm = RiskManager(RISK_PROFILE)
        decision = DecisionResult(actions=[])
        losing = Position(
            symbol="TSLA", name="Tesla", quantity=5, avg_cost=200,
            current_price=160, market_value=800, unrealized_pl=-200,
            unrealized_pl_pct=-20, sector="Auto",
        )
        portfolio = make_portfolio(cash=5000, positions=[losing])
        quotes = {}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.forced_actions) == 1
        assert result.forced_actions[0].symbol == "TSLA"
        assert result.forced_actions[0].type == "SELL"


class TestRiskManagerMaxTrades:
    def test_trim_to_max_trades(self):
        rm = RiskManager(RISK_PROFILE)
        actions = [
            TradeAction(type="BUY", symbol=f"SYM{i}", amount_usd=200, urgency="LOW", thesis="t")
            for i in range(5)
        ]
        decision = DecisionResult(actions=actions)
        portfolio = make_portfolio(cash=5000, total_value=10000)
        quotes = {f"SYM{i}": make_quote(f"SYM{i}") for i in range(5)}

        result = rm.validate(decision, portfolio, quotes)
        assert len(result.approved_actions) == 3  # max_trades_per_cycle = 3
        assert len(result.rejected_actions) == 2
