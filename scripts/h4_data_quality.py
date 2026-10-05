"""Reject stale H4 files, including files left by interrupted downloads."""
import pandas as pd


def validate_h4_frame(frame, now=None):
    if frame.empty or not {"ts", "confirm", "open", "high", "low", "close"}.issubset(frame.columns):
        raise ValueError("Missing H4 candle data")
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    timestamps = pd.to_datetime(frame["ts"], utc=True, errors="raise")
    closed = timestamps[frame["confirm"].astype(int) == 1]
    # Allow the exchange 30 minutes after the H4 boundary to finalize a candle.
    expected = (now - pd.Timedelta(minutes=30)).floor("4h") - pd.Timedelta(hours=4)
    if closed.empty or closed.max() < expected:
        raise ValueError("Stale H4 candles: latest closed candle is too old")
    if timestamps.max() > now or (not closed.empty and closed.max() + pd.Timedelta(hours=4) > now):
        raise ValueError("Invalid H4 candle timestamps")
    return frame
