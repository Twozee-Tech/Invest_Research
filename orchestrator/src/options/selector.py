"""Rule-based strike and expiry selector for vertical spreads.

LLM decides direction (bullish/bearish) and spread type.
This module translates that decision into concrete strikes and expiry.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import structlog

from .data import OptionChainData, get_option_chain
from .greeks import RISK_FREE_RATE, calculate_greeks

logger = structlog.get_logger()

# Target delta for the long leg
TARGET_DELTA = 0.30
# Minimum spread width as fraction of underlying price
MIN_SPREAD_WIDTH_PCT = 0.015   # 1.5%
# Maximum spread width
MAX_SPREAD_WIDTH_PCT = 0.06    # 6%


@dataclass
class SelectedSpread:
    symbol: str
    spread_type: str           # BULL_CALL or BEAR_PUT
    expiration: str            # YYYY-MM-DD
    dte: int
    underlying_price: float

    buy_strike: float
    buy_option_type: str       # call or put
    buy_premium: float         # mid-price per share
    buy_iv: float
    buy_delta: float
    buy_contract_symbol: str | None

    sell_strike: float
    sell_option_type: str
    sell_premium: float
    sell_iv: float
    sell_delta: float
    sell_contract_symbol: str | None

    net_debit: float           # buy - sell (positive = debit)
    spread_width: float        # |buy_strike - sell_strike|
    max_profit_per_spread: float
    max_loss_per_spread: float  # = net_debit × 100


def select_spread(
    symbol: str,
    spread_type: str,           # "BULL_CALL" or "BEAR_PUT"
    risk_profile: dict,
    min_dte: int | None = None,
) -> SelectedSpread | None:
    """Select concrete strikes and expiry for a spread.

    Logic:
    1. Fetch option chain with DTE in [min_new_position_dte, 60]
    2. Find long leg with delta closest to TARGET_DELTA (0.30)
    3. Find short leg: next available strike further OTM
    4. Validate liquidity and spread economics
    """
    min_new_dte = min_dte or risk_profile.get("min_new_position_dte", 21)
    chain_data = get_option_chain(symbol, min_dte=min_new_dte, max_dte=60)
    if chain_data is None:
        logger.warning("selector_no_chain", symbol=symbol, spread_type=spread_type)
        return None

    if spread_type == "BULL_CALL":
        return _select_bull_call(chain_data, risk_profile)
    elif spread_type == "BEAR_PUT":
        return _select_bear_put(chain_data, risk_profile)
    else:
        logger.error("selector_unknown_spread_type", spread_type=spread_type)
        return None


# ── Bull Call Spread ──────────────────────────────────────────────────────────

def _select_bull_call(chain: OptionChainData, risk_profile: dict) -> SelectedSpread | None:
    """Buy lower-strike call + sell higher-strike call."""
    calls = chain.calls
    if calls.empty or len(calls) < 2:
        return None

    from datetime import datetime, date
    today = date.today()
    exp_date = datetime.strptime(chain.expiration, "%Y-%m-%d").date()
    t = max((exp_date - today).days / 365.0, 0.001)

    # Find long leg: call with delta closest to TARGET_DELTA
    long_row = _find_target_delta_row(calls, "call", chain.underlying_price, t, TARGET_DELTA)
    if long_row is None:
        return None

    long_strike = float(long_row["strike"])

    # Short leg: next strike above long leg (staying within spread width limits)
    min_width = chain.underlying_price * MIN_SPREAD_WIDTH_PCT
    max_width = chain.underlying_price * MAX_SPREAD_WIDTH_PCT

    higher_calls = calls[calls["strike"] > long_strike].copy()
    higher_calls = higher_calls[
        (higher_calls["strike"] - long_strike >= min_width) &
        (higher_calls["strike"] - long_strike <= max_width)
    ]

    if higher_calls.empty:
        # Relax: just take next strike
        higher_calls = calls[calls["strike"] > long_strike]
    if higher_calls.empty:
        return None

    short_row = higher_calls.iloc[0]
    short_strike = float(short_row["strike"])

    return _build_spread(
        chain=chain,
        spread_type="BULL_CALL",
        long_row=long_row,
        short_row=short_row,
        long_type="call",
        short_type="call",
        t=t,
    )


# ── Bear Put Spread ───────────────────────────────────────────────────────────

def _select_bear_put(chain: OptionChainData, risk_profile: dict) -> SelectedSpread | None:
    """Buy higher-strike put + sell lower-strike put."""
    puts = chain.puts
    if puts.empty or len(puts) < 2:
        return None

    from datetime import datetime, date
    today = date.today()
    exp_date = datetime.strptime(chain.expiration, "%Y-%m-%d").date()
    t = max((exp_date - today).days / 365.0, 0.001)

    # Find long leg: put with delta closest to -TARGET_DELTA
    long_row = _find_target_delta_row(puts, "put", chain.underlying_price, t, -TARGET_DELTA)
    if long_row is None:
        return None

    long_strike = float(long_row["strike"])

    # Short leg: next strike below long leg
    min_width = chain.underlying_price * MIN_SPREAD_WIDTH_PCT
    max_width = chain.underlying_price * MAX_SPREAD_WIDTH_PCT

    lower_puts = puts[puts["strike"] < long_strike].copy()
    lower_puts = lower_puts[
        (long_strike - lower_puts["strike"] >= min_width) &
        (long_strike - lower_puts["strike"] <= max_width)
    ]

    if lower_puts.empty:
        lower_puts = puts[puts["strike"] < long_strike]
    if lower_puts.empty:
        return None

    short_row = lower_puts.iloc[-1]   # closest strike below
    short_strike = float(short_row["strike"])

    return _build_spread(
        chain=chain,
        spread_type="BEAR_PUT",
        long_row=long_row,
        short_row=short_row,
        long_type="put",
        short_type="put",
        t=t,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_target_delta_row(
    df: pd.DataFrame,
    option_type: str,
    S: float,
    t: float,
    target_delta: float,
) -> pd.Series | None:
    """Find the row with delta closest to target_delta."""
    best_row = None
    best_dist = float("inf")

    for _, row in df.iterrows():
        iv = float(row.get("impliedVolatility", 0) or 0)
        strike = float(row["strike"])
        if iv <= 0:
            continue
        g = calculate_greeks(option_type, S, strike, t, iv, RISK_FREE_RATE)
        if g is None:
            continue
        dist = abs(g.delta - target_delta)
        if dist < best_dist:
            best_dist = dist
            best_row = row

    return best_row


def _mid_price(row: pd.Series) -> float:
    bid = float(row.get("bid", 0) or 0)
    ask = float(row.get("ask", 0) or 0)
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    last = float(row.get("lastPrice", 0) or 0)
    return round(last, 2)


def _build_spread(
    chain: OptionChainData,
    spread_type: str,
    long_row: pd.Series,
    short_row: pd.Series,
    long_type: str,
    short_type: str,
    t: float,
) -> SelectedSpread | None:
    S = chain.underlying_price
    long_strike = float(long_row["strike"])
    short_strike = float(short_row["strike"])
    long_premium = _mid_price(long_row)
    short_premium = _mid_price(short_row)

    if long_premium <= 0:
        return None

    long_iv = float(long_row.get("impliedVolatility", 0.25) or 0.25)
    short_iv = float(short_row.get("impliedVolatility", 0.25) or 0.25)

    long_g = calculate_greeks(long_type, S, long_strike, t, long_iv)
    short_g = calculate_greeks(short_type, S, short_strike, t, short_iv)

    net_debit = round(long_premium - short_premium, 2)
    spread_width = abs(long_strike - short_strike)
    max_profit = round((spread_width - net_debit) * 100, 2)
    max_loss = round(net_debit * 100, 2)

    # Reject if risk/reward is poor (max profit < max loss)
    if max_profit <= 0 or max_loss <= 0:
        logger.warning(
            "selector_poor_risk_reward",
            symbol=chain.symbol, spread_type=spread_type,
            net_debit=net_debit, spread_width=spread_width,
        )
        return None

    logger.info(
        "selector_spread_selected",
        symbol=chain.symbol, spread_type=spread_type,
        buy_strike=long_strike, sell_strike=short_strike,
        net_debit=net_debit, max_profit=max_profit, max_loss=max_loss,
        dte=chain.dte, expiration=chain.expiration,
    )

    return SelectedSpread(
        symbol=chain.symbol,
        spread_type=spread_type,
        expiration=chain.expiration,
        dte=chain.dte,
        underlying_price=S,
        buy_strike=long_strike,
        buy_option_type=long_type,
        buy_premium=long_premium,
        buy_iv=long_iv,
        buy_delta=long_g.delta if long_g else 0.0,
        buy_contract_symbol=str(long_row.get("contractSymbol", "")),
        sell_strike=short_strike,
        sell_option_type=short_type,
        sell_premium=short_premium,
        sell_iv=short_iv,
        sell_delta=short_g.delta if short_g else 0.0,
        sell_contract_symbol=str(short_row.get("contractSymbol", "")),
        net_debit=net_debit,
        spread_width=spread_width,
        max_profit_per_spread=max_profit,
        max_loss_per_spread=max_loss,
    )
