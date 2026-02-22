"""Strike and expiry selector for the Wheel Strategy.

Provides:
  select_csp()  — pick OTM put strike for a cash-secured put
  select_cc()   — pick OTM call strike for a covered call

Both use yfinance option chains and target a specific delta (approximate,
derived from Black-Scholes when chain deltas are unavailable).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
import structlog

from .data import OptionChainData, get_option_chain
from .greeks import RISK_FREE_RATE, calculate_greeks

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SelectedCSP:
    """Result of select_csp()."""
    symbol: str
    expiration: str          # YYYY-MM-DD
    dte: int
    underlying_price: float
    strike: float            # OTM put strike sold
    premium: float           # mid-price collected per share
    iv: float                # implied volatility of the sold put
    delta: float             # put delta (negative; abs value shown)
    contract_symbol: str | None


@dataclass
class SelectedCC:
    """Result of select_cc()."""
    symbol: str
    expiration: str          # YYYY-MM-DD
    dte: int
    underlying_price: float
    strike: float            # OTM call strike sold (≥ cost_basis)
    premium: float           # mid-price collected per share
    iv: float
    delta: float             # call delta (positive)
    contract_symbol: str | None
    cost_basis: float        # stock cost basis passed in (for validation)


# ---------------------------------------------------------------------------
# Public selectors
# ---------------------------------------------------------------------------

def select_csp(
    symbol: str,
    contracts: int = 1,
    target_delta: float = 0.30,
    dte_min: int = 21,
    dte_max: int = 45,
) -> SelectedCSP | None:
    """Select an OTM put strike for a cash-secured put.

    Picks the expiration closest to the middle of [dte_min, dte_max] then
    finds the put with delta closest to -target_delta (OTM side).

    Args:
        symbol:       Underlying ticker.
        contracts:    Number of contracts (informational only; no effect on selection).
        target_delta: Absolute delta target (~0.25-0.35 for a typical CSP).
        dte_min:      Minimum DTE for expiration search.
        dte_max:      Maximum DTE for expiration search.

    Returns:
        SelectedCSP or None if no suitable chain found.
    """
    chain = get_option_chain(symbol, min_dte=dte_min, max_dte=dte_max)
    if chain is None:
        logger.warning("csp_selector_no_chain", symbol=symbol)
        return None

    puts = chain.puts
    if puts is None or puts.empty:
        logger.warning("csp_selector_no_puts", symbol=symbol, expiration=chain.expiration)
        return None

    today = date.today()
    exp_date = datetime.strptime(chain.expiration, "%Y-%m-%d").date()
    t = max((exp_date - today).days / 365.0, 0.001)
    S = chain.underlying_price

    # Only consider OTM puts (strike < underlying price)
    otm_puts = puts[puts["strike"] < S].copy()
    if otm_puts.empty:
        logger.warning("csp_selector_no_otm_puts", symbol=symbol, underlying=S)
        return None

    # Find put whose delta (negative) is closest to -target_delta
    best_row = _find_target_delta_row(otm_puts, "put", S, t, -abs(target_delta))
    if best_row is None:
        # Fallback: pick the strike closest to (S * (1 - 0.05)) — ~5% OTM
        approx_strike = S * (1 - 0.05)
        otm_puts["dist"] = (otm_puts["strike"] - approx_strike).abs()
        best_row = otm_puts.loc[otm_puts["dist"].idxmin()]

    strike = float(best_row["strike"])
    premium = _mid_price(best_row)

    if premium <= 0:
        logger.warning("csp_selector_zero_premium", symbol=symbol, strike=strike)
        return None

    iv = float(best_row.get("impliedVolatility", 0.25) or 0.25)
    greeks = calculate_greeks("put", S, strike, t, iv, RISK_FREE_RATE)
    delta = greeks.delta if greeks else -target_delta

    logger.info(
        "csp_selected",
        symbol=symbol, strike=strike, expiration=chain.expiration,
        dte=chain.dte, premium=premium, delta=round(delta, 3),
        contracts=contracts,
    )

    return SelectedCSP(
        symbol=symbol,
        expiration=chain.expiration,
        dte=chain.dte,
        underlying_price=S,
        strike=strike,
        premium=premium,
        iv=iv,
        delta=delta,
        contract_symbol=str(best_row.get("contractSymbol", "") or ""),
    )


def select_cc(
    symbol: str,
    contracts: int = 1,
    cost_basis: float = 0.0,
    target_delta: float = 0.25,
    dte_min: int = 14,
    dte_max: int = 30,
) -> SelectedCC | None:
    """Select an OTM call strike for a covered call.

    The call strike must be:
      1. Above the current underlying price (OTM).
      2. At or above cost_basis (so assignment = profit, not a loss-lock).

    Args:
        symbol:       Underlying ticker.
        contracts:    Number of contracts (informational).
        cost_basis:   Stock cost basis (strike price of assigned CSP).
        target_delta: Absolute delta target (~0.20-0.30 for a typical CC).
        dte_min:      Minimum DTE.
        dte_max:      Maximum DTE.

    Returns:
        SelectedCC or None if no suitable chain found.
    """
    chain = get_option_chain(symbol, min_dte=dte_min, max_dte=dte_max)
    if chain is None:
        logger.warning("cc_selector_no_chain", symbol=symbol)
        return None

    calls = chain.calls
    if calls is None or calls.empty:
        logger.warning("cc_selector_no_calls", symbol=symbol, expiration=chain.expiration)
        return None

    today = date.today()
    exp_date = datetime.strptime(chain.expiration, "%Y-%m-%d").date()
    t = max((exp_date - today).days / 365.0, 0.001)
    S = chain.underlying_price

    # Enforce strike ≥ max(S, cost_basis) so the call is OTM *and* profitable if called away
    min_strike = max(S, cost_basis) if cost_basis > 0 else S
    otm_calls = calls[calls["strike"] >= min_strike].copy()

    if otm_calls.empty:
        logger.warning(
            "cc_selector_no_otm_calls",
            symbol=symbol, underlying=S, cost_basis=cost_basis,
        )
        return None

    # Find call with delta closest to target_delta
    best_row = _find_target_delta_row(otm_calls, "call", S, t, abs(target_delta))
    if best_row is None:
        # Fallback: first OTM call above min_strike
        best_row = otm_calls.iloc[0]

    strike = float(best_row["strike"])
    premium = _mid_price(best_row)

    if premium <= 0:
        logger.warning("cc_selector_zero_premium", symbol=symbol, strike=strike)
        return None

    iv = float(best_row.get("impliedVolatility", 0.25) or 0.25)
    greeks = calculate_greeks("call", S, strike, t, iv, RISK_FREE_RATE)
    delta = greeks.delta if greeks else target_delta

    # Final safety check: strike must be ≥ cost_basis
    if cost_basis > 0 and strike < cost_basis:
        logger.warning(
            "cc_strike_below_cost_basis",
            symbol=symbol, strike=strike, cost_basis=cost_basis,
        )
        return None

    logger.info(
        "cc_selected",
        symbol=symbol, strike=strike, expiration=chain.expiration,
        dte=chain.dte, premium=premium, delta=round(delta, 3),
        cost_basis=cost_basis, contracts=contracts,
    )

    return SelectedCC(
        symbol=symbol,
        expiration=chain.expiration,
        dte=chain.dte,
        underlying_price=S,
        strike=strike,
        premium=premium,
        iv=iv,
        delta=delta,
        contract_symbol=str(best_row.get("contractSymbol", "") or ""),
        cost_basis=cost_basis,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _find_target_delta_row(
    df: pd.DataFrame,
    option_type: str,
    S: float,
    t: float,
    target_delta: float,
) -> pd.Series | None:
    """Return the row whose BS-calculated delta is closest to target_delta."""
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
    """Return bid/ask midpoint, falling back to lastPrice."""
    bid = float(row.get("bid", 0) or 0)
    ask = float(row.get("ask", 0) or 0)
    if bid > 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    last = float(row.get("lastPrice", 0) or 0)
    return round(last, 2)
