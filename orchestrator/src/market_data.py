"""Market data fetcher using yfinance with caching."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import structlog
import yfinance as yf
import pandas as pd

logger = structlog.get_logger()

QUOTE_CACHE_TTL = 60  # seconds
INFO_CACHE_TTL = 3600  # seconds


@dataclass
class StockQuote:
    symbol: str
    price: float
    change_pct: float
    volume: int
    avg_volume_10d: int
    market_cap: float
    pe_ratio: float | None
    forward_pe: float | None
    pb_ratio: float | None
    dividend_yield: float | None
    week52_high: float
    week52_low: float
    sector: str
    industry: str
    name: str


@dataclass
class _CacheEntry:
    data: object
    timestamp: float


class MarketDataProvider:
    """Fetches stock quotes and history from Yahoo Finance with caching."""

    def __init__(self, quote_ttl: int = QUOTE_CACHE_TTL, info_ttl: int = INFO_CACHE_TTL):
        self._quote_cache: dict[str, _CacheEntry] = {}
        self._info_cache: dict[str, _CacheEntry] = {}
        self._quote_ttl = quote_ttl
        self._info_ttl = info_ttl

    def _get_cached(self, cache: dict, key: str, ttl: int):
        entry = cache.get(key)
        if entry and (time.time() - entry.timestamp) < ttl:
            return entry.data
        return None

    def get_quote(self, symbol: str) -> StockQuote:
        """Get current quote with fundamentals for a symbol."""
        cached = self._get_cached(self._quote_cache, symbol, self._quote_ttl)
        if cached:
            return cached

        ticker = yf.Ticker(symbol)
        info = ticker.info
        fast = ticker.fast_info

        quote = StockQuote(
            symbol=symbol,
            price=fast.get("lastPrice", 0) or info.get("currentPrice", 0) or info.get("regularMarketPrice", 0),
            change_pct=info.get("regularMarketChangePercent", 0) or 0,
            volume=info.get("regularMarketVolume", 0) or 0,
            avg_volume_10d=info.get("averageDailyVolume10Day", 0) or 0,
            market_cap=info.get("marketCap", 0) or 0,
            pe_ratio=info.get("trailingPE"),
            forward_pe=info.get("forwardPE"),
            pb_ratio=info.get("priceToBook"),
            dividend_yield=info.get("dividendYield"),
            week52_high=info.get("fiftyTwoWeekHigh", 0) or 0,
            week52_low=info.get("fiftyTwoWeekLow", 0) or 0,
            sector=info.get("sector", "Unknown"),
            industry=info.get("industry", "Unknown"),
            name=info.get("shortName", symbol),
        )

        self._quote_cache[symbol] = _CacheEntry(data=quote, timestamp=time.time())
        logger.debug("market_data_quote_fetched", symbol=symbol, price=quote.price)
        return quote

    def get_quotes_batch(self, symbols: list[str]) -> dict[str, StockQuote]:
        """Get quotes for multiple symbols."""
        results = {}
        for symbol in symbols:
            try:
                results[symbol] = self.get_quote(symbol)
            except Exception as e:
                logger.warning("market_data_quote_failed", symbol=symbol, error=str(e))
        return results

    def get_history(
        self,
        symbol: str,
        period: str = "6mo",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Get historical OHLCV data."""
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            logger.warning("market_data_empty_history", symbol=symbol, period=period)
        return df

    def get_current_price(self, symbol: str) -> float:
        """Get just the current price (lightweight)."""
        ticker = yf.Ticker(symbol)
        fast = ticker.fast_info
        return fast.get("lastPrice", 0) or 0

    def validate_symbol(self, symbol: str) -> bool:
        """Check if a symbol is valid and tradeable."""
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            return bool(info.get("regularMarketPrice") or info.get("currentPrice"))
        except Exception:
            return False

    def get_market_overview(self) -> dict:
        """Get broad market indicators (S&P 500, VIX, 10Y yield)."""
        benchmarks = {
            "SPY": "S&P 500 ETF",
            "QQQ": "Nasdaq 100 ETF",
            "^VIX": "VIX Volatility",
            "^TNX": "10Y Treasury Yield",
        }
        overview = {}
        for sym, label in benchmarks.items():
            try:
                ticker = yf.Ticker(sym)
                fast = ticker.fast_info
                overview[sym] = {
                    "label": label,
                    "price": fast.get("lastPrice", 0),
                    "change_pct": fast.get("regularMarketChangePercent", 0) if hasattr(fast, "regularMarketChangePercent") else 0,
                }
            except Exception as e:
                logger.warning("market_overview_failed", symbol=sym, error=str(e))
        return overview
