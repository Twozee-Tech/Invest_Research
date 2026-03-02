"""Tests for OptionsPositionTracker: P/L formulas, DRY_RUN filtering."""

import tempfile
from pathlib import Path

import pytest

from src.options.positions import OptionsPositionTracker


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tracker(tmp_path):
    """Fresh tracker backed by a temp SQLite DB."""
    return OptionsPositionTracker(db_path=tmp_path / "test_audit.db")


def _open_debit_spread(tracker: OptionsPositionTracker, account: str = "test_acct") -> int:
    """Open a bull call spread (debit trade, entry_debit > 0)."""
    return tracker.open_position(
        account_key=account,
        symbol="SPY",
        spread_type="BULL_CALL",
        contracts=1,
        expiration_date="2026-03-21",
        buy_strike=500.0,
        buy_option_type="call",
        buy_premium=10.00,   # paid $10/share
        sell_strike=510.0,
        sell_option_type="call",
        sell_premium=5.00,   # received $5/share
        max_profit=500.0,    # ($510-$500 - net_debit) × 100
        max_loss=500.0,      # net_debit × 100
        entry_debit=5.00,    # net debit = buy_premium - sell_premium
    )


def _open_credit_spread(tracker: OptionsPositionTracker, account: str = "test_acct") -> int:
    """Open a cash-secured put or credit spread (entry_debit < 0)."""
    return tracker.open_position(
        account_key=account,
        symbol="META",
        spread_type="BEAR_PUT",
        contracts=1,
        expiration_date="2026-03-21",
        buy_strike=590.0,
        buy_option_type="put",
        buy_premium=20.00,
        sell_strike=600.0,
        sell_option_type="put",
        sell_premium=34.53,  # sold at higher premium
        max_profit=1453.0,
        max_loss=547.0,
        entry_debit=-14.53,  # negative = credit received
    )


# ── open_position ─────────────────────────────────────────────────────────────

class TestOpenPosition:
    def test_returns_positive_id(self, tracker):
        pos_id = _open_debit_spread(tracker)
        assert isinstance(pos_id, int) and pos_id > 0

    def test_position_is_open(self, tracker):
        pos_id = _open_debit_spread(tracker)
        pos = tracker.get_position_by_id(pos_id)
        assert pos is not None
        assert pos.status == "open"

    def test_fields_stored_correctly(self, tracker):
        pos_id = _open_debit_spread(tracker)
        pos = tracker.get_position_by_id(pos_id)
        assert pos.symbol == "SPY"
        assert pos.spread_type == "BULL_CALL"
        assert pos.contracts == 1
        assert pos.buy_strike == pytest.approx(500.0)
        assert pos.entry_debit == pytest.approx(5.0)


# ── close_position — debit trade ──────────────────────────────────────────────

class TestClosePositionDebit:
    def test_profitable_close(self, tracker):
        """Debit spread: bought for $5, close at $8 → P/L = ($8-$5) × 1 × 100 = $300."""
        pos_id = _open_debit_spread(tracker)
        pl = tracker.close_position(pos_id, close_value=8.00, reason="TARGET")
        assert pl == pytest.approx(300.0, abs=0.01)

    def test_loss_close(self, tracker):
        """Debit spread: bought for $5, close at $2 → P/L = ($2-$5) × 100 = -$300."""
        pos_id = _open_debit_spread(tracker)
        pl = tracker.close_position(pos_id, close_value=2.00, reason="STOP_LOSS")
        assert pl == pytest.approx(-300.0, abs=0.01)

    def test_break_even_close(self, tracker):
        pos_id = _open_debit_spread(tracker)
        pl = tracker.close_position(pos_id, close_value=5.00, reason="BREAK_EVEN")
        assert pl == pytest.approx(0.0, abs=0.01)

    def test_status_becomes_closed(self, tracker):
        pos_id = _open_debit_spread(tracker)
        tracker.close_position(pos_id, close_value=8.00, reason="TARGET")
        pos = tracker.get_position_by_id(pos_id)
        assert pos.status == "closed"
        assert pos.realized_pl == pytest.approx(300.0, abs=0.01)
        assert pos.close_reason == "TARGET"


# ── close_position — credit trade ────────────────────────────────────────────

class TestClosePositionCredit:
    def test_profitable_credit_close(self, tracker):
        """Credit spread: received $14.53, close at $0.72 → P/L = ($14.53-$0.72) × 100 = $1381."""
        pos_id = _open_credit_spread(tracker)
        pl = tracker.close_position(pos_id, close_value=0.72, reason="TARGET_50PCT")
        # entry_debit = -14.53 → credit received = $14.53
        # close cost = $0.72
        # P/L = (14.53 - 0.72) × 1 × 100 = $1381
        assert pl == pytest.approx(1381.0, abs=0.01)

    def test_loss_credit_close(self, tracker):
        """Credit spread: received $14.53, close at $20 (adverse move) → loss."""
        pos_id = _open_credit_spread(tracker)
        pl = tracker.close_position(pos_id, close_value=20.00, reason="STOP_LOSS")
        # P/L = (14.53 - 20.00) × 1 × 100 = -$547
        assert pl == pytest.approx(-547.0, abs=0.01)

    def test_full_profit_credit_close(self, tracker):
        """Credit spread expires worthless: close at $0 → keep full premium."""
        pos_id = _open_credit_spread(tracker)
        pl = tracker.close_position(pos_id, close_value=0.0, reason="EXPIRED_WORTHLESS")
        # P/L = (14.53 - 0.0) × 1 × 100 = $1453
        assert pl == pytest.approx(1453.0, abs=0.01)

    def test_multiple_contracts(self, tracker):
        """2 contracts: P/L is doubled."""
        pos_id = tracker.open_position(
            account_key="test_acct",
            symbol="TSLA",
            spread_type="BEAR_PUT",
            contracts=2,
            expiration_date="2026-03-21",
            buy_strike=300.0,
            buy_option_type="put",
            buy_premium=10.0,
            sell_strike=310.0,
            sell_option_type="put",
            sell_premium=15.0,
            max_profit=1000.0,
            max_loss=1000.0,
            entry_debit=-5.0,  # $5 credit per share
        )
        pl = tracker.close_position(pos_id, close_value=1.0, reason="TARGET")
        # P/L = (5.0 - 1.0) × 2 contracts × 100 = $800
        assert pl == pytest.approx(800.0, abs=0.01)


# ── expire_position ──────────────────────────────────────────────────────────

class TestExpirePosition:
    def test_debit_expires_worthless(self, tracker):
        """Debit spread expires worthless → lose full premium paid."""
        pos_id = _open_debit_spread(tracker)
        tracker.expire_position(pos_id)
        pos = tracker.get_position_by_id(pos_id)
        assert pos.status == "expired"
        # P/L = -entry_debit × contracts × 100 = -5 × 1 × 100 = -$500
        assert pos.realized_pl == pytest.approx(-500.0, abs=0.01)

    def test_credit_expires_worthless(self, tracker):
        """Credit spread expires worthless → keep full premium received."""
        pos_id = _open_credit_spread(tracker)
        tracker.expire_position(pos_id)
        pos = tracker.get_position_by_id(pos_id)
        assert pos.status == "expired"
        # P/L = -entry_debit × contracts × 100 = -(-14.53) × 1 × 100 = +$1453
        assert pos.realized_pl == pytest.approx(1453.0, abs=0.01)


# ── get_total_realized_pl ────────────────────────────────────────────────────

class TestGetTotalRealizedPl:
    def test_no_closed_positions(self, tracker):
        _open_debit_spread(tracker)
        assert tracker.get_total_realized_pl("test_acct") == 0.0

    def test_counts_real_closed_positions(self, tracker):
        pos_id = _open_debit_spread(tracker)
        tracker.close_position(pos_id, 8.0, "TARGET", ghostfolio_order_id="gf-order-123")
        total = tracker.get_total_realized_pl("test_acct")
        assert total == pytest.approx(300.0, abs=0.01)

    def test_excludes_dry_run_closes(self, tracker):
        pos_id = _open_debit_spread(tracker)
        tracker.close_position(pos_id, 8.0, "TARGET", ghostfolio_order_id="DRY_RUN")
        total = tracker.get_total_realized_pl("test_acct")
        assert total == 0.0  # DRY_RUN must be excluded

    def test_excludes_null_order_id(self, tracker):
        pos_id = _open_debit_spread(tracker)
        tracker.close_position(pos_id, 8.0, "TARGET", ghostfolio_order_id=None)
        total = tracker.get_total_realized_pl("test_acct")
        assert total == 0.0  # NULL ghostfolio_order_id must be excluded

    def test_sums_multiple_real_positions(self, tracker):
        p1 = _open_debit_spread(tracker)
        p2 = _open_credit_spread(tracker)
        tracker.close_position(p1, 8.0, "TARGET", ghostfolio_order_id="gf-001")  # +$300
        tracker.close_position(p2, 0.72, "TARGET", ghostfolio_order_id="gf-002")  # +$1381
        total = tracker.get_total_realized_pl("test_acct")
        assert total == pytest.approx(300.0 + 1381.0, abs=0.01)

    def test_isolates_by_account_key(self, tracker):
        """P/L for one account doesn't bleed into another."""
        p1 = tracker.open_position(
            account_key="account_a", symbol="SPY", spread_type="BULL_CALL",
            contracts=1, expiration_date="2026-03-21",
            buy_strike=500, buy_option_type="call", buy_premium=10,
            sell_strike=510, sell_option_type="call", sell_premium=5,
            max_profit=500, max_loss=500, entry_debit=5.0,
        )
        tracker.close_position(p1, 8.0, "TARGET", ghostfolio_order_id="gf-001")  # +$300
        assert tracker.get_total_realized_pl("account_b") == 0.0
        assert tracker.get_total_realized_pl("account_a") == pytest.approx(300.0, abs=0.01)

    def test_excludes_open_positions(self, tracker):
        """Open positions (with unrealized P/L) are not counted."""
        pos_id = _open_debit_spread(tracker)
        tracker.update_position(pos_id, current_value=8.0, current_pl=300.0, greeks={}, dte=20)
        assert tracker.get_total_realized_pl("test_acct") == 0.0

    def test_mixed_real_and_dry_run(self, tracker):
        """Only real trades count; DRY_RUN excluded from sum."""
        p1 = _open_debit_spread(tracker)
        p2 = _open_credit_spread(tracker)
        tracker.close_position(p1, 8.0, "TARGET", ghostfolio_order_id="gf-real")  # +$300
        tracker.close_position(p2, 0.72, "TARGET", ghostfolio_order_id="DRY_RUN")  # should be excluded
        total = tracker.get_total_realized_pl("test_acct")
        assert total == pytest.approx(300.0, abs=0.01)
