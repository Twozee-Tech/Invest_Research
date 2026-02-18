"""Technical indicator calculations using the `ta` library."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import ta

import structlog

logger = structlog.get_logger()


@dataclass
class TechnicalSignals:
    symbol: str
    sma_20: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    rsi_14: float | None = None
    macd_line: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    volume_ratio: float | None = None  # current volume / 20-day avg
    price: float | None = None

    def to_summary(self) -> dict:
        """Return a concise dict for LLM prompt injection."""
        signals = {}
        if self.sma_20 is not None:
            signals["SMA20"] = round(self.sma_20, 2)
        if self.sma_50 is not None:
            signals["SMA50"] = round(self.sma_50, 2)
        if self.sma_200 is not None:
            signals["SMA200"] = round(self.sma_200, 2)
        if self.rsi_14 is not None:
            signals["RSI14"] = round(self.rsi_14, 2)
        if self.macd_line is not None:
            signals["MACD"] = round(self.macd_line, 4)
            signals["MACD_signal"] = round(self.macd_signal or 0, 4)
            signals["MACD_hist"] = round(self.macd_histogram or 0, 4)
        if self.bb_upper is not None:
            signals["BB_upper"] = round(self.bb_upper, 2)
            signals["BB_lower"] = round(self.bb_lower or 0, 2)
        if self.volume_ratio is not None:
            signals["volume_ratio"] = round(self.volume_ratio, 2)
        if self.price is not None:
            signals["price"] = round(self.price, 2)

        # Add interpretations
        interpretations = []
        if self.rsi_14 is not None:
            if self.rsi_14 > 70:
                interpretations.append("RSI OVERBOUGHT")
            elif self.rsi_14 < 30:
                interpretations.append("RSI OVERSOLD")
        if self.price and self.sma_50:
            if self.price > self.sma_50:
                interpretations.append("Above SMA50")
            else:
                interpretations.append("Below SMA50")
        if self.price and self.sma_200:
            if self.price > self.sma_200:
                interpretations.append("Above SMA200")
            else:
                interpretations.append("Below SMA200")
        if self.macd_histogram is not None:
            if self.macd_histogram > 0:
                interpretations.append("MACD bullish")
            else:
                interpretations.append("MACD bearish")

        signals["interpretation"] = ", ".join(interpretations) if interpretations else "Neutral"
        return signals


def compute_indicators(df: pd.DataFrame, symbol: str) -> TechnicalSignals:
    """Compute technical indicators from OHLCV DataFrame.

    Args:
        df: DataFrame with columns: Open, High, Low, Close, Volume
        symbol: Ticker symbol for logging
    """
    if df.empty or len(df) < 20:
        logger.warning("insufficient_data_for_indicators", symbol=symbol, rows=len(df))
        return TechnicalSignals(symbol=symbol)

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    current_price = float(close.iloc[-1])

    signals = TechnicalSignals(symbol=symbol, price=current_price)

    # SMA
    if len(close) >= 20:
        signals.sma_20 = float(ta.trend.sma_indicator(close, window=20).iloc[-1])
    if len(close) >= 50:
        signals.sma_50 = float(ta.trend.sma_indicator(close, window=50).iloc[-1])
    if len(close) >= 200:
        signals.sma_200 = float(ta.trend.sma_indicator(close, window=200).iloc[-1])

    # RSI
    rsi = ta.momentum.rsi(close, window=14)
    if not rsi.empty and pd.notna(rsi.iloc[-1]):
        signals.rsi_14 = float(rsi.iloc[-1])

    # MACD
    macd = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = macd.macd()
    macd_signal = macd.macd_signal()
    macd_diff = macd.macd_diff()
    if not macd_line.empty and pd.notna(macd_line.iloc[-1]):
        signals.macd_line = float(macd_line.iloc[-1])
        signals.macd_signal = float(macd_signal.iloc[-1])
        signals.macd_histogram = float(macd_diff.iloc[-1])

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(close, window=20, window_dev=2)
    bb_high = bb.bollinger_hband()
    bb_low = bb.bollinger_lband()
    bb_mid = bb.bollinger_mavg()
    if not bb_high.empty and pd.notna(bb_high.iloc[-1]):
        signals.bb_upper = float(bb_high.iloc[-1])
        signals.bb_middle = float(bb_mid.iloc[-1])
        signals.bb_lower = float(bb_low.iloc[-1])

    # Volume ratio
    if len(volume) >= 20:
        avg_vol = float(volume.tail(20).mean())
        current_vol = float(volume.iloc[-1])
        if avg_vol > 0:
            signals.volume_ratio = current_vol / avg_vol

    logger.debug("indicators_computed", symbol=symbol, rsi=signals.rsi_14)
    return signals
