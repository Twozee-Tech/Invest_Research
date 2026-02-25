"""Tests for options/spreads_risk_manager.py."""

from orchestrator.src.options.spreads_decision_parser import SpreadAction, SpreadDecision
from orchestrator.src.options.spreads_risk_manager import SpreadsRiskManager
from orchestrator.src.options.positions import OptionsPosition
from orchestrator.src.portfolio_state import PortfolioState


def _make_portfolio(cash=5000, total_value=10000):
    return PortfolioState(
        account_id="test-id",
        account_name="test",
        cash=cash,
        total_value=total_value,
        invested=total_value - cash,
    )


def _make_position(
    id=1, symbol="SPY", spread_type="IRON_CONDOR",
    dte=30, current_pl=None, max_profit=100, max_loss=200,
    entry_debit=-1.0, profit_captured=None,
):
    pos = OptionsPosition(
        id=id, account_key="test", symbol=symbol,
        spread_type=spread_type, status="open",
        contracts=1, expiration_date="2026-04-01",
        buy_strike=550.0, buy_option_type="put",
        buy_premium=1.0, sell_strike=555.0,
        sell_option_type="put", sell_premium=2.0,
        max_profit=max_profit, max_loss=max_loss,
        entry_debit=entry_debit, entry_date="2026-02-01",
        dte=dte, current_pl=current_pl,
    )
    return pos


RISK_PROFILE = {
    "max_open_spreads": 3,
    "min_cash_pct": 20,
    "max_spread_width": 10,
    "take_profit_pct": 50,
    "stop_loss_pct": 100,
    "auto_close_dte": 3,
    "target_dte_min": 21,
    "target_dte_max": 45,
}


class TestSpreadsRiskManagerOpens:
    """Test OPEN_SPREAD validation."""

    def test_approve_single_open(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        decision = SpreadDecision(actions=[
            SpreadAction(type="OPEN_SPREAD", symbol="SPY", spread_type="iron_condor",
                         contracts=1, reason="Good setup"),
        ])
        result = mgr.validate(decision, [], _make_portfolio())
        assert len(result.approved_opens) == 1
        assert len(result.rejected_opens) == 0

    def test_reject_over_max_spreads(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        existing = [
            _make_position(id=i+1, symbol=f"SYM{i}") for i in range(3)
        ]
        decision = SpreadDecision(actions=[
            SpreadAction(type="OPEN_SPREAD", symbol="NEW", spread_type="bull_call",
                         contracts=1, reason="Should be rejected"),
        ])
        result = mgr.validate(decision, existing, _make_portfolio())
        assert len(result.approved_opens) == 0
        assert len(result.rejected_opens) == 1
        assert "Max open spreads" in result.rejected_opens[0]["reason"]

    def test_approve_after_close_frees_slot(self):
        """Closing a position should free a slot for a new open."""
        mgr = SpreadsRiskManager(RISK_PROFILE)
        existing = [
            _make_position(id=i+1, symbol=f"SYM{i}") for i in range(3)
        ]
        decision = SpreadDecision(actions=[
            SpreadAction(type="CLOSE", symbol="SYM0", position_id=1, reason="Take profit"),
            SpreadAction(type="OPEN_SPREAD", symbol="NEW", spread_type="bull_call",
                         contracts=1, reason="Replace closed position"),
        ])
        result = mgr.validate(decision, existing, _make_portfolio())
        assert len(result.approved_closes) == 1
        assert len(result.approved_opens) == 1

    def test_reject_insufficient_cash(self):
        """Should reject when estimated max loss exceeds available cash."""
        mgr = SpreadsRiskManager(RISK_PROFILE)
        # Cash=2500, total_value=10000 → 25% cash
        # max_spread_width=10 → estimated_max_loss = 10*100 = $1000
        # After: (2500-1000)/10000 = 15% < 20% min → reject
        portfolio = _make_portfolio(cash=2500, total_value=10000)
        decision = SpreadDecision(actions=[
            SpreadAction(type="OPEN_SPREAD", symbol="SPY", spread_type="iron_condor",
                         contracts=1, reason="test"),
        ])
        result = mgr.validate(decision, [], portfolio)
        assert len(result.rejected_opens) == 1
        assert "Insufficient cash" in result.rejected_opens[0]["reason"]

    def test_approve_with_enough_cash(self):
        """Cash=5000, total=10000 → 50%. After -$1000 → 40% > 20%."""
        mgr = SpreadsRiskManager(RISK_PROFILE)
        portfolio = _make_portfolio(cash=5000, total_value=10000)
        decision = SpreadDecision(actions=[
            SpreadAction(type="OPEN_SPREAD", symbol="SPY", spread_type="iron_condor",
                         contracts=1, reason="test"),
        ])
        result = mgr.validate(decision, [], portfolio)
        assert len(result.approved_opens) == 1

    def test_reject_earnings_flag(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        decision = SpreadDecision(actions=[
            SpreadAction(type="OPEN_SPREAD", symbol="TSLA", spread_type="bull_call",
                         contracts=1, reason="Earnings in 3 days, risky"),
        ])
        result = mgr.validate(decision, [], _make_portfolio())
        assert len(result.rejected_opens) == 1
        assert "near-earnings" in result.rejected_opens[0]["reason"]

    def test_safe_earnings_phrase_passes(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        decision = SpreadDecision(actions=[
            SpreadAction(type="OPEN_SPREAD", symbol="AAPL", spread_type="iron_condor",
                         contracts=1, reason="No earnings for 6 weeks, safe to sell premium"),
        ])
        result = mgr.validate(decision, [], _make_portfolio())
        assert len(result.approved_opens) == 1


class TestSpreadsRiskManagerCloses:
    """Test CLOSE validation."""

    def test_approve_valid_close(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        existing = [_make_position(id=5)]
        decision = SpreadDecision(actions=[
            SpreadAction(type="CLOSE", symbol="SPY", position_id=5, reason="take profit"),
        ])
        result = mgr.validate(decision, existing, _make_portfolio())
        assert len(result.approved_closes) == 1

    def test_reject_close_unknown_id(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        decision = SpreadDecision(actions=[
            SpreadAction(type="CLOSE", symbol="SPY", position_id=999, reason="unknown"),
        ])
        result = mgr.validate(decision, [], _make_portfolio())
        assert len(result.approved_closes) == 0
        assert any("unknown position ID 999" in w for w in result.warnings)


class TestSpreadsAutoClose:
    """Test auto-close rules (DTE, take-profit, stop-loss)."""

    def test_auto_close_low_dte(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        pos = _make_position(id=1, dte=2)  # below auto_close_dte=3
        decision = SpreadDecision(actions=[])
        result = mgr.validate(decision, [pos], _make_portfolio())
        assert len(result.forced_closes) == 1
        assert "DTE=2" in result.forced_closes[0].reason

    def test_no_auto_close_above_dte(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        pos = _make_position(id=1, dte=15)
        decision = SpreadDecision(actions=[])
        result = mgr.validate(decision, [pos], _make_portfolio())
        assert len(result.forced_closes) == 0

    def test_auto_close_take_profit(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        # max_profit=100, current_pl=60 → 60% captured ≥ 50%
        pos = _make_position(id=1, dte=20, max_profit=100, current_pl=60)
        decision = SpreadDecision(actions=[])
        result = mgr.validate(decision, [pos], _make_portfolio())
        assert len(result.forced_closes) == 1
        assert "Take-profit" in result.forced_closes[0].reason

    def test_no_take_profit_below_threshold(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        # max_profit=100, current_pl=30 → 30% < 50%
        pos = _make_position(id=1, dte=20, max_profit=100, current_pl=30)
        decision = SpreadDecision(actions=[])
        result = mgr.validate(decision, [pos], _make_portfolio())
        assert len(result.forced_closes) == 0

    def test_auto_close_stop_loss(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        # max_loss=200, current_pl=-200 → loss_pct = 200/200 = 100% ≥ 100%
        pos = _make_position(id=1, dte=20, max_loss=200, current_pl=-200)
        decision = SpreadDecision(actions=[])
        result = mgr.validate(decision, [pos], _make_portfolio())
        assert len(result.forced_closes) == 1
        assert "Stop-loss" in result.forced_closes[0].reason

    def test_no_stop_loss_below_threshold(self):
        mgr = SpreadsRiskManager(RISK_PROFILE)
        # max_loss=200, current_pl=-100 → 50% < 100%
        pos = _make_position(id=1, dte=20, max_loss=200, current_pl=-100)
        decision = SpreadDecision(actions=[])
        result = mgr.validate(decision, [pos], _make_portfolio())
        assert len(result.forced_closes) == 0

    def test_llm_close_not_duplicated_by_auto(self):
        """If LLM already requested CLOSE, auto-close should not duplicate."""
        mgr = SpreadsRiskManager(RISK_PROFILE)
        pos = _make_position(id=1, dte=2)  # would trigger auto-close
        decision = SpreadDecision(actions=[
            SpreadAction(type="CLOSE", symbol="SPY", position_id=1, reason="manual close"),
        ])
        result = mgr.validate(decision, [pos], _make_portfolio())
        assert len(result.approved_closes) == 1
        assert len(result.forced_closes) == 0  # not duplicated


class TestSpreadsRiskManagerCashAccounting:
    """Test cash accounting across multiple opens."""

    def test_sequential_opens_deplete_cash(self):
        """Each approved open reduces available cash for subsequent ones."""
        mgr = SpreadsRiskManager({
            **RISK_PROFILE,
            "max_open_spreads": 10,  # high limit
            "min_cash_pct": 20,
            "max_spread_width": 10,  # $1000 per spread
        })
        # Cash=4000, total=10000 → 40%
        # First open: 4000-1000=3000 → 30% OK
        # Second: 3000-1000=2000 → 20% OK (borderline)
        # Third: 2000-1000=1000 → 10% < 20% → REJECT
        portfolio = _make_portfolio(cash=4000, total_value=10000)
        decision = SpreadDecision(actions=[
            SpreadAction(type="OPEN_SPREAD", symbol="A", spread_type="bull_call", contracts=1, reason=""),
            SpreadAction(type="OPEN_SPREAD", symbol="B", spread_type="bear_put", contracts=1, reason=""),
            SpreadAction(type="OPEN_SPREAD", symbol="C", spread_type="iron_condor", contracts=1, reason=""),
        ])
        result = mgr.validate(decision, [], portfolio)
        assert len(result.approved_opens) == 2
        assert len(result.rejected_opens) == 1
