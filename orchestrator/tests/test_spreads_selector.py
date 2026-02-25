"""Tests for options/spreads_selector.py.

Uses mocked option chain data to test strike selection for all 6 spread types.
"""

import math
from unittest.mock import patch, MagicMock

import pandas as pd

from orchestrator.src.options.spreads_selector import select_spread, SelectedSpread


def _bs_price(option_type, S, K, t=0.1, sigma=0.25, r=0.05):
    """Simple BS price for realistic mock data."""
    from scipy.stats import norm
    sqt = math.sqrt(t)
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * t) / (sigma * sqt)
    d2 = d1 - sigma * sqt
    if option_type == "call":
        return max(0.05, round(S * norm.cdf(d1) - K * math.exp(-r * t) * norm.cdf(d2), 2))
    else:
        return max(0.05, round(K * math.exp(-r * t) * norm.cdf(-d2) - S * norm.cdf(-d1), 2))


def _make_chain(underlying_price=550.0, expiration="2026-04-01", dte=35):
    """Build a realistic mock OptionChainData with BS-priced calls and puts."""
    strikes = list(range(510, 600, 5))
    t = dte / 365.0

    def _make_df(option_type):
        rows = []
        for s in strikes:
            iv = 0.25
            mid = _bs_price(option_type, underlying_price, float(s), t, iv)
            bid = max(0.05, round(mid * 0.95, 2))
            ask = round(mid * 1.05, 2)
            rows.append({
                "strike": float(s),
                "bid": bid,
                "ask": ask,
                "lastPrice": round(mid, 2),
                "impliedVolatility": round(iv, 4),
                "volume": 100,
                "openInterest": 500,
                "contractSymbol": f"SPY260401{'C' if option_type == 'call' else 'P'}{s:08d}",
            })
        return pd.DataFrame(rows)

    mock_chain = MagicMock()
    mock_chain.symbol = "SPY"
    mock_chain.underlying_price = underlying_price
    mock_chain.expiration = expiration
    mock_chain.dte = dte
    mock_chain.calls = _make_df("call")
    mock_chain.puts = _make_df("put")
    return mock_chain


class TestSelectSpread:
    """Test select_spread() with mocked option chains."""

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_bull_call(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "bull_call", max_width=10)
        assert result is not None
        assert result.spread_type == "bull_call"
        assert result.symbol == "SPY"
        assert len(result.legs) == 2
        # Buy leg should be lower strike, sell leg higher
        buy_leg = next(l for l in result.legs if l.side == "buy")
        sell_leg = next(l for l in result.legs if l.side == "sell")
        assert buy_leg.strike < sell_leg.strike
        assert buy_leg.option_type == "call"
        assert sell_leg.option_type == "call"
        # Debit spread: net_debit > 0
        assert result.net_debit > 0
        assert result.max_profit > 0
        assert result.max_loss > 0

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_bear_put(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "bear_put", max_width=10)
        assert result is not None
        assert result.spread_type == "bear_put"
        assert len(result.legs) == 2
        buy_leg = next(l for l in result.legs if l.side == "buy")
        sell_leg = next(l for l in result.legs if l.side == "sell")
        assert buy_leg.strike > sell_leg.strike  # buy higher put
        assert buy_leg.option_type == "put"
        assert sell_leg.option_type == "put"
        assert result.net_debit > 0

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_bull_put(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "bull_put", max_width=10)
        assert result is not None
        assert result.spread_type == "bull_put"
        assert len(result.legs) == 2
        buy_leg = next(l for l in result.legs if l.side == "buy")
        sell_leg = next(l for l in result.legs if l.side == "sell")
        assert buy_leg.strike < sell_leg.strike  # buy further OTM (lower)
        assert buy_leg.option_type == "put"
        assert sell_leg.option_type == "put"
        # Credit spread: net_debit < 0
        assert result.net_debit < 0

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_bear_call(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "bear_call", max_width=10)
        assert result is not None
        assert result.spread_type == "bear_call"
        assert len(result.legs) == 2
        buy_leg = next(l for l in result.legs if l.side == "buy")
        sell_leg = next(l for l in result.legs if l.side == "sell")
        assert buy_leg.strike > sell_leg.strike  # buy further OTM (higher)
        assert buy_leg.option_type == "call"
        assert sell_leg.option_type == "call"
        assert result.net_debit < 0

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_iron_condor(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "iron_condor", max_width=10)
        assert result is not None
        assert result.spread_type == "iron_condor"
        assert len(result.legs) == 4
        # Should have 2 buy legs and 2 sell legs
        buy_legs = [l for l in result.legs if l.side == "buy"]
        sell_legs = [l for l in result.legs if l.side == "sell"]
        assert len(buy_legs) == 2
        assert len(sell_legs) == 2
        # One put side, one call side
        put_legs = [l for l in result.legs if l.option_type == "put"]
        call_legs = [l for l in result.legs if l.option_type == "call"]
        assert len(put_legs) == 2
        assert len(call_legs) == 2
        # Credit spread: net_debit < 0
        assert result.net_debit < 0
        assert result.max_profit > 0
        assert result.max_loss > 0

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_butterfly(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "butterfly", max_width=10)
        assert result is not None
        assert result.spread_type == "butterfly"
        assert len(result.legs) == 3  # lower buy, middle sell, upper buy
        buy_legs = [l for l in result.legs if l.side == "buy"]
        sell_legs = [l for l in result.legs if l.side == "sell"]
        assert len(buy_legs) == 2
        assert len(sell_legs) == 1
        # All calls
        assert all(l.option_type == "call" for l in result.legs)
        # Debit spread
        assert result.net_debit > 0

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_no_chain_returns_none(self, mock_get_chain):
        mock_get_chain.return_value = None
        result = select_spread("SPY", "bull_call")
        assert result is None

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_unknown_type_returns_none(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "straddle")
        assert result is None

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_empty_chain_returns_none(self, mock_get_chain):
        chain = _make_chain()
        chain.calls = pd.DataFrame()
        chain.puts = pd.DataFrame()
        mock_get_chain.return_value = chain
        result = select_spread("SPY", "bull_call")
        assert result is None

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_max_width_respected(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "bull_call", max_width=5)
        if result is not None:
            buy_leg = next(l for l in result.legs if l.side == "buy")
            sell_leg = next(l for l in result.legs if l.side == "sell")
            width = abs(sell_leg.strike - buy_leg.strike)
            assert width <= 5.0

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_contracts_passed_through(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain()
        result = select_spread("SPY", "bull_call", contracts=3, max_width=10)
        assert result is not None
        assert result.contracts == 3

    @patch("orchestrator.src.options.spreads_selector.get_option_chain")
    def test_expiration_from_chain(self, mock_get_chain):
        mock_get_chain.return_value = _make_chain(expiration="2026-04-15", dte=50)
        result = select_spread("SPY", "bull_call", max_width=10)
        if result is not None:
            assert result.expiration == "2026-04-15"
            assert result.dte == 50
