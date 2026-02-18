"""Tests for the trade executor module."""

from unittest.mock import MagicMock, patch
import pytest

from src.decision_parser import TradeAction
from src.trade_executor import TradeExecutor, TradeResult


class TestTradeExecutor:
    def setup_method(self):
        self.mock_ghostfolio = MagicMock()
        self.mock_market = MagicMock()
        self.executor = TradeExecutor(
            ghostfolio=self.mock_ghostfolio,
            market_data=self.mock_market,
            dry_run=False,
        )
        self.dry_executor = TradeExecutor(
            ghostfolio=self.mock_ghostfolio,
            market_data=self.mock_market,
            dry_run=True,
        )

    def test_execute_buy(self):
        action = TradeAction(type="BUY", symbol="VTI", amount_usd=1000, thesis="test")
        self.mock_market.get_current_price.return_value = 250.0
        self.mock_ghostfolio.create_order.return_value = {"id": "order-123"}

        results = self.executor.execute_trades([action], "account-1")
        assert len(results) == 1
        assert results[0].success
        assert results[0].quantity == pytest.approx(4.0, abs=0.01)
        assert results[0].unit_price == 250.0
        assert results[0].ghostfolio_order_id == "order-123"

        self.mock_ghostfolio.create_order.assert_called_once()

    def test_execute_sell(self):
        action = TradeAction(type="SELL", symbol="AAPL", amount_usd=500, thesis="profit")
        self.mock_market.get_current_price.return_value = 200.0
        self.mock_ghostfolio.create_order.return_value = {"id": "order-456"}

        results = self.executor.execute_trades([action], "account-1")
        assert len(results) == 1
        assert results[0].success
        assert results[0].quantity == pytest.approx(2.5, abs=0.01)

    def test_dry_run_no_api_call(self):
        action = TradeAction(type="BUY", symbol="SPY", amount_usd=2000, thesis="test")
        self.mock_market.get_current_price.return_value = 500.0

        results = self.dry_executor.execute_trades([action], "account-1")
        assert len(results) == 1
        assert results[0].success
        assert results[0].ghostfolio_order_id == "DRY_RUN"
        self.mock_ghostfolio.create_order.assert_not_called()

    def test_zero_price_fails(self):
        action = TradeAction(type="BUY", symbol="BAD", amount_usd=1000, thesis="test")
        self.mock_market.get_current_price.return_value = 0.0

        results = self.executor.execute_trades([action], "account-1")
        assert len(results) == 1
        assert not results[0].success
        assert "price" in results[0].error.lower()

    def test_api_error_handled(self):
        action = TradeAction(type="BUY", symbol="ERR", amount_usd=500, thesis="test")
        self.mock_market.get_current_price.return_value = 100.0
        self.mock_ghostfolio.create_order.side_effect = Exception("API error")

        results = self.executor.execute_trades([action], "account-1")
        assert len(results) == 1
        assert not results[0].success
        assert "API error" in results[0].error

    def test_multiple_trades(self):
        actions = [
            TradeAction(type="BUY", symbol="VTI", amount_usd=1000, thesis="t1"),
            TradeAction(type="SELL", symbol="AAPL", amount_usd=500, thesis="t2"),
            TradeAction(type="BUY", symbol="NVDA", amount_usd=300, thesis="t3"),
        ]
        self.mock_market.get_current_price.side_effect = [250.0, 200.0, 150.0]
        self.mock_ghostfolio.create_order.return_value = {"id": "ok"}

        results = self.executor.execute_trades(actions, "account-1")
        assert len(results) == 3
        assert all(r.success for r in results)
        assert self.mock_ghostfolio.create_order.call_count == 3

    def test_verify_orders(self):
        results = [
            TradeResult(
                action=TradeAction(type="BUY", symbol="VTI", amount_usd=1000),
                success=True,
                quantity=4.0,
                unit_price=250.0,
                total_cost=1000.0,
                ghostfolio_order_id="order-123",
            ),
        ]
        self.mock_ghostfolio.list_orders.return_value = [{"id": "order-123"}]

        warnings = self.executor.verify_orders(results)
        assert len(warnings) == 0

    def test_verify_missing_order(self):
        results = [
            TradeResult(
                action=TradeAction(type="BUY", symbol="VTI", amount_usd=1000),
                success=True,
                quantity=4.0,
                unit_price=250.0,
                total_cost=1000.0,
                ghostfolio_order_id="order-999",
            ),
        ]
        self.mock_ghostfolio.list_orders.return_value = [{"id": "order-123"}]

        warnings = self.executor.verify_orders(results)
        assert len(warnings) == 1
        assert "order-999" in warnings[0]
