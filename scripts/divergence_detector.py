"""TradingView-style confirmed regular RSI divergence detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd


@dataclass
class RsiDivergence:
    symbol: str
    timeframe: str
    direction: str
    first_pivot_time: str
    pivot_time: str
    confirmed_time: str
    first_price: float
    price: float
    first_rsi: float
    rsi: float
    kind: str = "regular"

    @property
    def id(self) -> str:
        return f"{self.symbol}-{self.timeframe}-rsi-tv-{self.kind}-{self.direction}-{self.first_pivot_time}-{self.pivot_time}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["id"] = self.id
        return data


def _timestamp_label(value: object, timeframe: str) -> str:
    """Return a stable signal timestamp without changing existing D1 IDs."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    if timeframe.upper() == "1D":
        return timestamp.date().isoformat()
    return timestamp.isoformat().replace("+00:00", "Z")


def _rsi(close: pd.Series, period: int) -> pd.Series:
    """Match ta.rsi(close, period): Wilder RMA of gains and losses."""
    change = close.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)
    average_gain = pd.Series(float("nan"), index=close.index, dtype=float)
    average_loss = pd.Series(float("nan"), index=close.index, dtype=float)
    if len(close) <= period:
        return average_gain

    average_gain.iloc[period] = gains.iloc[1:period + 1].mean()
    average_loss.iloc[period] = losses.iloc[1:period + 1].mean()
    for index in range(period + 1, len(close)):
        average_gain.iloc[index] = (average_gain.iloc[index - 1] * (period - 1) + gains.iloc[index]) / period
        average_loss.iloc[index] = (average_loss.iloc[index - 1] * (period - 1) + losses.iloc[index]) / period

    relative_strength = average_gain / average_loss
    result = 100 - (100 / (1 + relative_strength))
    result = result.mask(average_loss == 0, 100.0)
    result = result.mask((average_gain == 0) & (average_loss > 0), 0.0)
    return result


def _pivot_indices(values: pd.Series, left: int, right: int, kind: str) -> list[int]:
    """Return only pivots that have enough candles to their right to be final."""
    pivots: list[int] = []
    for index in range(left, len(values) - right):
        value = values.iloc[index]
        window_left = values.iloc[index - left:index]
        window_right = values.iloc[index + 1:index + right + 1]
        if pd.isna(value) or window_left.isna().any() or window_right.isna().any():
            continue
        if kind == "low" and value < window_left.min() and value <= window_right.min():
            pivots.append(index)
        if kind == "high" and value > window_left.max() and value >= window_right.max():
            pivots.append(index)
    return pivots


def find_regular_rsi_divergences(
    frame: pd.DataFrame,
    symbol: str,
    *,
    timeframe: str = "1D",
    rsi_period: int = 14,
    pivot_left: int = 5,
    pivot_right: int = 5,
    min_bars_between: int = 5,
    max_bars_between: int = 60,
) -> list[RsiDivergence]:
    """Find regular divergences using the supplied TradingView RSI rules.

    Pivots are formed on RSI itself, then the corresponding candle high/low is
    compared. A pivot is emitted only once its right-side candles are closed.
    """
    df = frame.copy().reset_index(drop=True)
    for column in ("high", "low", "close"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna(subset=["ts", "high", "low", "close"]).reset_index(drop=True)
    if len(df) < rsi_period + pivot_left + pivot_right + min_bars_between + 1:
        return []

    df["rsi"] = _rsi(df["close"], rsi_period)
    divergences: list[RsiDivergence] = []

    for kind, direction, column in (("low", "bullish", "low"), ("high", "bearish", "high")):
        pivots = _pivot_indices(df["rsi"], pivot_left, pivot_right, kind)
        for previous, current in zip(pivots, pivots[1:]):
            # Pine uses ta.barssince(plFound[1] / phFound[1]), hence -1.
            distance = current - previous - 1
            if distance < min_bars_between or distance > max_bars_between:
                continue
            first_price = float(df.at[previous, column])
            price = float(df.at[current, column])
            first_rsi = float(df.at[previous, "rsi"])
            rsi = float(df.at[current, "rsi"])
            if pd.isna(first_rsi) or pd.isna(rsi):
                continue

            bullish = direction == "bullish" and price < first_price and rsi > first_rsi
            bearish = direction == "bearish" and price > first_price and rsi < first_rsi
            if not (bullish or bearish):
                continue

            divergences.append(RsiDivergence(
                symbol=symbol,
                timeframe=timeframe,
                direction=direction,
                first_pivot_time=_timestamp_label(df.at[previous, "ts"], timeframe),
                pivot_time=_timestamp_label(df.at[current, "ts"], timeframe),
                confirmed_time=_timestamp_label(df.at[current + pivot_right, "ts"], timeframe),
                first_price=first_price,
                price=price,
                first_rsi=first_rsi,
                rsi=rsi,
            ))
    return divergences
