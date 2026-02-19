"""Black-Scholes Greeks via py_vollib with scipy fallback."""

from __future__ import annotations

import math
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# Risk-free rate (annualized, decimal)
RISK_FREE_RATE = 0.05

# Try py_vollib first; fall back to manual BS if unavailable
try:
    from py_vollib.black_scholes.greeks.analytical import (
        delta as _vol_delta,
        gamma as _vol_gamma,
        theta as _vol_theta,
        vega as _vol_vega,
    )
    _USE_PYVOLLIB = True
    logger.info("greeks_using_pyvollib")
except ImportError:
    _USE_PYVOLLIB = False
    logger.info("greeks_using_scipy_fallback")


@dataclass
class Greeks:
    delta: float      # dimensionless ±1
    gamma: float      # per $1 underlying move
    theta: float      # per day (dollar, per contract × 100 shares)
    vega: float       # per 1% IV change (dollar, per contract × 100 shares)


@dataclass
class SpreadGreeks:
    net_delta: float
    net_gamma: float
    net_theta: float      # $/day per spread (positive = collecting theta)
    net_vega: float       # $ per 1% IV change per spread
    max_profit: float     # $ per spread (contracts × 100 × credit/profit potential)
    max_loss: float       # $ per spread (always positive)
    breakeven: float      # underlying price at breakeven


@dataclass
class PortfolioGreeks:
    total_delta: float    # Net $ change per 1% move in underlying
    total_gamma: float
    total_theta: float    # $/day total across all positions
    total_vega: float     # $ per 1% IV change
    position_count: int


# ──────────────────────────────────────────────────────────────────────────────
# Core calculation
# ──────────────────────────────────────────────────────────────────────────────

def calculate_greeks(
    option_type: str,    # "call" or "put"
    S: float,            # underlying price
    K: float,            # strike
    t: float,            # time to expiry in years
    sigma: float,        # implied volatility (annualized, decimal)
    r: float = RISK_FREE_RATE,
) -> Greeks | None:
    """Calculate Black-Scholes Greeks for one option leg.

    Returns raw (per-share, per-contract) Greeks.
    Multiply by 100 for per-contract dollar impact.
    """
    if t <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return None

    flag = "c" if option_type == "call" else "p"

    try:
        if _USE_PYVOLLIB:
            # py_vollib already returns: theta per-day, vega per-1% IV change
            d = _vol_delta(flag, S, K, t, r, sigma)
            g = _vol_gamma(flag, S, K, t, r, sigma)
            th = _vol_theta(flag, S, K, t, r, sigma)
            v = _vol_vega(flag, S, K, t, r, sigma)
        else:
            d, g, th, v = _bs_greeks(flag, S, K, t, r, sigma)

        return Greeks(
            delta=round(d, 4),
            gamma=round(g, 6),
            theta=round(th, 4),
            vega=round(v, 4),
        )
    except Exception as e:
        logger.error("greeks_calculation_failed", error=str(e), S=S, K=K, t=t, sigma=sigma)
        return None


def calculate_spread_greeks(
    spread_type: str,       # "BULL_CALL" or "BEAR_PUT"
    underlying_price: float,
    buy_strike: float,
    sell_strike: float,
    expiration_date: str,   # YYYY-MM-DD
    buy_iv: float,          # IV for buy leg (decimal)
    sell_iv: float,         # IV for sell leg (decimal)
    buy_premium: float,     # mid-price of buy leg
    sell_premium: float,    # mid-price of sell leg
    contracts: int = 1,
) -> SpreadGreeks | None:
    """Calculate net Greeks and P&L limits for a vertical spread."""
    from datetime import date, datetime
    today = date.today()
    exp = datetime.strptime(expiration_date, "%Y-%m-%d").date()
    t = max((exp - today).days / 365.0, 0.001)

    if spread_type == "BULL_CALL":
        buy_type, sell_type = "call", "call"
    elif spread_type == "BEAR_PUT":
        buy_type, sell_type = "put", "put"
    else:
        return None

    g_buy = calculate_greeks(buy_type, underlying_price, buy_strike, t, buy_iv)
    g_sell = calculate_greeks(sell_type, underlying_price, sell_strike, t, sell_iv)
    if g_buy is None or g_sell is None:
        return None

    # Net premium (debit paid, positive = we paid)
    net_debit = round((buy_premium - sell_premium), 2)
    spread_width = abs(buy_strike - sell_strike)
    max_profit = round((spread_width - net_debit) * contracts * 100, 2)
    max_loss = round(net_debit * contracts * 100, 2)

    if spread_type == "BULL_CALL":
        breakeven = buy_strike + net_debit
    else:
        breakeven = buy_strike - net_debit

    return SpreadGreeks(
        net_delta=round((g_buy.delta - g_sell.delta) * contracts * 100, 2),
        net_gamma=round((g_buy.gamma - g_sell.gamma) * contracts * 100, 4),
        net_theta=round((g_buy.theta - g_sell.theta) * contracts * 100, 2),
        net_vega=round((g_buy.vega - g_sell.vega) * contracts * 100, 2),
        max_profit=max_profit,
        max_loss=max_loss,
        breakeven=round(breakeven, 2),
    )


def calculate_portfolio_greeks(positions: list[dict]) -> PortfolioGreeks:
    """Aggregate Greeks across all open spread positions.

    Each position dict should have 'current_greeks' key with serialized Greeks.
    """
    total_delta = 0.0
    total_gamma = 0.0
    total_theta = 0.0
    total_vega = 0.0
    count = 0

    for pos in positions:
        g = pos.get("current_greeks")
        if not isinstance(g, dict):
            continue
        total_delta += g.get("net_delta", 0)
        total_gamma += g.get("net_gamma", 0)
        total_theta += g.get("net_theta", 0)
        total_vega += g.get("net_vega", 0)
        count += 1

    return PortfolioGreeks(
        total_delta=round(total_delta, 2),
        total_gamma=round(total_gamma, 4),
        total_theta=round(total_theta, 2),
        total_vega=round(total_vega, 2),
        position_count=count,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Fallback: manual Black-Scholes
# ──────────────────────────────────────────────────────────────────────────────

def _bs_greeks(
    flag: str, S: float, K: float, t: float, r: float, sigma: float
) -> tuple[float, float, float, float]:
    """Manual BS Greeks using scipy.stats.norm."""
    from scipy.stats import norm

    sqt = math.sqrt(t)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * t) / (sigma * sqt)
    d2 = d1 - sigma * sqt

    # Delta
    if flag == "c":
        d = norm.cdf(d1)
    else:
        d = norm.cdf(d1) - 1

    # Gamma (same for call and put)
    g = norm.pdf(d1) / (S * sigma * sqt)

    # Theta (per year → will be divided by 365 by caller)
    if flag == "c":
        th = (-(S * norm.pdf(d1) * sigma) / (2 * sqt)
              - r * K * math.exp(-r * t) * norm.cdf(d2))
    else:
        th = (-(S * norm.pdf(d1) * sigma) / (2 * sqt)
              + r * K * math.exp(-r * t) * norm.cdf(-d2))
    th_daily = th / 365

    # Vega per 1% (vega per unit = S * pdf(d1) * sqt; div by 100 for per 1%)
    v = S * norm.pdf(d1) * sqt / 100

    return d, g, th_daily, v
