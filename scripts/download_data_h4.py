"""Download confirmed OKX USDT perpetual 4-hour candles for the H4 bot."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from download_data import API_BASE_URL, PAIRS, REQUEST_DELAY_SECONDS, instrument_id

RAW_DATA_DIR = Path("data/raw_h4")
BAR = "4H"
FILE_INTERVAL = "4h"
BAR_MS = 4 * 60 * 60 * 1000
DAYS_BACK = int(os.getenv("H4_DAYS_BACK", "180"))
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)


def fetch_candles(symbol: str) -> list[list[str]]:
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).timestamp() * 1000)
    rows: list[list[str]] = []
    after: str | None = None
    while True:
        params = {"instId": instrument_id(symbol), "bar": BAR, "limit": "300"}
        if after is not None:
            params["after"] = after
        response = requests.get(f"{API_BASE_URL}/api/v5/market/history-candles", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "0":
            raise RuntimeError(f"OKX error for {symbol}: {payload.get('msg', payload)}")
        batch = payload.get("data", [])
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(item[0]) for item in batch)
        if oldest <= cutoff_ms or len(batch) < 300:
            break
        after = str(oldest)
        time.sleep(REQUEST_DELAY_SECONDS)
    return rows


def normalize(rows: list[list[str]]) -> pd.DataFrame:
    records = []
    for row in rows:
        if len(row) < 9 or row[8] != "1":
            continue
        timestamp_ms = int(row[0])
        records.append({
            "ts": pd.to_datetime(timestamp_ms, unit="ms", utc=True),
            "open": float(row[1]), "high": float(row[2]), "low": float(row[3]),
            "close": float(row[4]), "volume": float(row[5]),
            "close_time": pd.to_datetime(timestamp_ms + BAR_MS - 1, unit="ms", utc=True),
            "confirm": 1,
        })
    return pd.DataFrame(records).sort_values("ts").drop_duplicates("ts")


def main() -> None:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=DAYS_BACK)
    for symbol in PAIRS:
        try:
            frame = normalize(fetch_candles(symbol))
            frame = frame[frame["ts"] >= cutoff]
            if frame.empty:
                print(f"Skip {symbol}: no confirmed 4H candles")
                continue
            output = RAW_DATA_DIR / f"{symbol}_{FILE_INTERVAL}.csv"
            frame.to_csv(output, index=False)
            print(f"Saved {symbol}: {len(frame)} candles -> {output}")
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else "unknown"
            print(f"Skip {symbol}: OKX HTTP {status}")
        except Exception as error:
            print(f"Skip {symbol}: {error}")


if __name__ == "__main__":
    main()
