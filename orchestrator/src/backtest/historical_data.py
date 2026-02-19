"""Historical data fetching and slicing for backtesting.

Data is fetched UPFRONT (batch before the simulation loop) to avoid
repeated API calls and ensure consistent data throughout the run.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf
import structlog

logger = structlog.get_logger()


def prefetch_history(
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> dict[str, pd.DataFrame]:
    """Fetch full OHLCV history for all symbols in one batch.

    Args:
        symbols: List of ticker symbols to fetch.
        start_date: Start date string (YYYY-MM-DD). Should include extra
            lookback (e.g. 6 months before sim start) for indicator warmup.
        end_date: End date string (YYYY-MM-DD).

    Returns:
        Dict mapping symbol -> full OHLCV DataFrame for the period.
    """
    result: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date, interval="1d")
            if df.empty:
                logger.warning("backtest_empty_history", symbol=symbol,
                               start=start_date, end=end_date)
            else:
                result[symbol] = df
                logger.debug("backtest_history_fetched", symbol=symbol, rows=len(df))
        except Exception as e:
            logger.warning("backtest_history_failed", symbol=symbol, error=str(e))
    return result


def get_quotes_at_date(
    symbol: str,
    date: str,
    full_history_df: pd.DataFrame,
) -> dict:
    """Extract a quote-compatible dict for a symbol at a given simulation date.

    Returns a dict with fields compatible with what prompt_builder expects
    from market_data (price, change_pct, volume, etc.).  Fundamental data
    (PE ratio, market cap, etc.) is not available historically and is omitted.

    Args:
        symbol: Ticker symbol.
        date: Simulation date string (YYYY-MM-DD).
        full_history_df: Full prefetched OHLCV DataFrame for this symbol.

    Returns:
        Dict with price, change_pct, volume, avg_volume_10d, and placeholder
        fields for fundamentals.
    """
    if full_history_df is None or full_history_df.empty:
        return {"symbol": symbol, "price": 0, "change_pct": 0, "volume": 0,
                "avg_volume_10d": 0, "market_cap": 0, "pe_ratio": None,
                "forward_pe": None, "pb_ratio": None, "dividend_yield": None,
                "week52_high": 0, "week52_low": 0, "sector": "Unknown",
                "industry": "Unknown", "name": symbol}

    target = pd.Timestamp(date)
    idx_naive = full_history_df.index.normalize()
    if idx_naive.tz is not None:
        idx_naive = idx_naive.tz_localize(None)
    df = full_history_df[idx_naive <= target]
    if df.empty:
        return {"symbol": symbol, "price": 0, "change_pct": 0, "volume": 0,
                "avg_volume_10d": 0, "market_cap": 0, "pe_ratio": None,
                "forward_pe": None, "pb_ratio": None, "dividend_yield": None,
                "week52_high": 0, "week52_low": 0, "sector": "Unknown",
                "industry": "Unknown", "name": symbol}

    row = df.iloc[-1]
    price = float(row["Close"])

    # Day-over-day change
    if len(df) >= 2:
        prev_close = float(df.iloc[-2]["Close"])
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
    else:
        change_pct = 0.0

    # 10-day average volume (for liquidity check in risk manager)
    vol_series = df["Volume"].tail(10)
    avg_volume_10d = int(vol_series.mean()) if len(vol_series) > 0 else 0

    return {
        "symbol": symbol,
        "price": price,
        "change_pct": change_pct,
        "volume": int(row.get("Volume", 0)),
        "avg_volume_10d": avg_volume_10d,
        "market_cap": 0,
        "pe_ratio": None,
        "forward_pe": None,
        "pb_ratio": None,
        "dividend_yield": None,
        "week52_high": 0,
        "week52_low": 0,
        "sector": "Unknown",
        "industry": "Unknown",
        "name": symbol,
    }


def get_history_up_to(
    symbol: str,
    as_of_date: str,
    full_history_df: pd.DataFrame,
    lookback_days: int = 200,
) -> pd.DataFrame:
    """Return a slice of OHLCV data ending on or before as_of_date.

    Critical for preventing look-ahead bias: only returns data that would
    have been available at the simulation date.

    Args:
        symbol: Ticker symbol (used for logging only).
        as_of_date: Simulation date (YYYY-MM-DD) — no data after this date.
        full_history_df: Full prefetched OHLCV DataFrame for this symbol.
        lookback_days: Maximum number of historical rows to return.

    Returns:
        DataFrame slice with up to lookback_days rows ending on as_of_date.
    """
    if full_history_df is None or full_history_df.empty:
        return pd.DataFrame()

    target = pd.Timestamp(as_of_date)
    idx_naive = full_history_df.index.normalize()
    if idx_naive.tz is not None:
        idx_naive = idx_naive.tz_localize(None)
    df = full_history_df[idx_naive <= target]
    if df.empty:
        logger.debug("backtest_no_history_before_date", symbol=symbol, date=as_of_date)
        return pd.DataFrame()

    return df.tail(lookback_days).copy()
