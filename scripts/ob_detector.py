"""Daily Order Block detection shared by tests and production workflows."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import pandas as pd


FVG_DISTANCE = 3
ATR_PERIOD = 20


@dataclass
class OrderBlock:
    symbol: str
    timeframe: str
    direction: str
    top: float
    bottom: float
    ob_time: str
    bos_time: str
    fractal_time: str
    atr: float
    score: int
    reasons: list[str]

    @property
    def id(self) -> str:
        return f"{self.symbol}-{self.timeframe}-{self.direction}-{self.ob_time}-{self.bos_time}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["id"] = self.id
        return data


def _timestamp_label(value: object, timeframe: str) -> str:
    """Use exact times on intraday charts while preserving D1 signal IDs."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    if timeframe.upper() == "1D":
        return timestamp.date().isoformat()
    return timestamp.isoformat().replace("+00:00", "Z")


def prepare_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add confirmed 3-bar fractals, EMA100/200 and ATR(20)."""
    result = df.copy().reset_index(drop=True)
    for column in ("open", "high", "low", "close"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["ts", "open", "high", "low", "close"]).reset_index(drop=True)

    result["fractal_high"] = False
    result["fractal_low"] = False
    for index in range(1, len(result) - 1):
        high = result.at[index, "high"]
        low = result.at[index, "low"]
        result.at[index, "fractal_high"] = high > result.at[index - 1, "high"] and high > result.at[index + 1, "high"]
        result.at[index, "fractal_low"] = low < result.at[index - 1, "low"] and low < result.at[index + 1, "low"]

    result["ema100"] = result["close"].ewm(span=100, adjust=False).mean()
    result["ema200"] = result["close"].ewm(span=200, adjust=False).mean()
    previous_close = result["close"].shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    result["atr"] = true_range.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()
    return result


def _has_fvg_after_ob(df: pd.DataFrame, ob_index: int, bos_index: int, direction: str) -> bool:
    """Require an FVG beginning at most FVG_DISTANCE candles after the OB.

    The three-candle definition matches the original Pine conditions.  The OB
    must be at or before the older (left) candle of the FVG.
    """
    first_end = ob_index + 2
    last_end = min(bos_index, ob_index + FVG_DISTANCE + 2)
    for end_index in range(first_end, last_end + 1):
        left = end_index - 2
        middle = end_index - 1
        if direction == "bullish":
            found = (
                df.at[middle, "close"] > df.at[left, "high"]
                and df.at[end_index, "low"] > df.at[left, "high"]
            )
        else:
            found = (
                df.at[middle, "close"] < df.at[left, "low"]
                and df.at[end_index, "high"] < df.at[left, "low"]
            )
        if found:
            return True
    return False


def _score_ob(df: pd.DataFrame, ob_index: int, bos_index: int, direction: str, top: float, bottom: float) -> tuple[int, list[str], float]:
    row = df.iloc[bos_index]
    atr = float(row["atr"]) if pd.notna(row["atr"]) else 0.0
    reasons = ["BOS", "FVG"]
    score = 2

    if direction == "bullish":
        trend_ok = row["close"] > row["ema100"] > row["ema200"]
    else:
        trend_ok = row["close"] < row["ema100"] < row["ema200"]
    if trend_ok:
        score += 1
        reasons.append("EMA trend")

    body = abs(float(row["close"]) - float(row["open"]))
    if atr > 0 and body >= atr:
        score += 1
        reasons.append("strong BOS")
    if atr > 0 and (top - bottom) <= 1.5 * atr:
        score += 1
        reasons.append("compact zone")
    return score, reasons, atr


def find_all_obs(df: pd.DataFrame, symbol: str, timeframe: str = "1D") -> list[OrderBlock]:
    """Find OBs using confirmed fractals, close-based BOS and mandatory FVG."""
    df = prepare_indicators(df)
    fractal_highs: list[dict] = []
    fractal_lows: list[dict] = []
    blocks: list[OrderBlock] = []

    # A fractal is known only after the candle to its right has closed.
    for index in range(len(df)):
        confirmed_index = index - 1
        if confirmed_index >= 1:
            if df.at[confirmed_index, "fractal_high"]:
                fractal_highs.append({"value": df.at[confirmed_index, "high"], "index": confirmed_index})
            if df.at[confirmed_index, "fractal_low"]:
                fractal_lows.append({"value": df.at[confirmed_index, "low"], "index": confirmed_index})

        close = float(df.at[index, "close"])

        for fractal in list(reversed(fractal_highs)):
            if close <= fractal["value"]:
                continue
            start = fractal["index"]
            candidates = [
                candidate for candidate in range(start, index + 1)
                if df.at[candidate, "close"] < df.at[candidate, "open"]
            ]
            if candidates:
                ob_index = min(candidates, key=lambda candidate: df.at[candidate, "low"])
                top = float(df.at[ob_index, "open"])
                bottom = float(df.at[ob_index, "low"])
                if _has_fvg_after_ob(df, ob_index, index, "bullish") and top > bottom:
                    score, reasons, atr = _score_ob(df, ob_index, index, "bullish", top, bottom)
                    blocks.append(OrderBlock(
                        symbol, timeframe, "bullish", top, bottom,
                        _timestamp_label(df.at[ob_index, "ts"], timeframe),
                        _timestamp_label(df.at[index, "ts"], timeframe),
                        _timestamp_label(df.at[fractal["index"], "ts"], timeframe), atr, score, reasons,
                    ))
            fractal_highs.remove(fractal)

        for fractal in list(reversed(fractal_lows)):
            if close >= fractal["value"]:
                continue
            start = fractal["index"]
            candidates = [
                candidate for candidate in range(start, index + 1)
                if df.at[candidate, "close"] > df.at[candidate, "open"]
            ]
            if candidates:
                ob_index = max(candidates, key=lambda candidate: df.at[candidate, "high"])
                top = float(df.at[ob_index, "high"])
                bottom = float(df.at[ob_index, "open"])
                if _has_fvg_after_ob(df, ob_index, index, "bearish") and top > bottom:
                    score, reasons, atr = _score_ob(df, ob_index, index, "bearish", top, bottom)
                    blocks.append(OrderBlock(
                        symbol, timeframe, "bearish", top, bottom,
                        _timestamp_label(df.at[ob_index, "ts"], timeframe),
                        _timestamp_label(df.at[index, "ts"], timeframe),
                        _timestamp_label(df.at[fractal["index"], "ts"], timeframe), atr, score, reasons,
                    ))
            fractal_lows.remove(fractal)
    return blocks


def has_been_touched_or_invalidated(df: pd.DataFrame, block: OrderBlock) -> str:
    """Return armed, touched or invalidated after the BOS candle."""
    timestamps = pd.to_datetime(df["ts"], utc=True)
    bos_timestamp = pd.Timestamp(block.bos_time)
    if bos_timestamp.tzinfo is None:
        bos_timestamp = bos_timestamp.tz_localize("UTC")
    else:
        bos_timestamp = bos_timestamp.tz_convert("UTC")
    bos_indices = df.index[timestamps == bos_timestamp].tolist()
    if not bos_indices:
        return "armed"
    for index in range(bos_indices[0] + 1, len(df)):
        high, low = float(df.at[index, "high"]), float(df.at[index, "low"])
        if block.direction == "bullish" and low <= block.bottom:
            return "invalidated"
        if block.direction == "bearish" and high >= block.top:
            return "invalidated"
        if low <= block.top and high >= block.bottom:
            return "touched"
    return "armed"
