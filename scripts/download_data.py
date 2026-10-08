"""Download closed daily USDT perpetual candles from OKX Swap."""

import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests


API_BASE_URL = "https://www.okx.com"
RAW_DATA_DIR = "data/raw"
BAR = "1Dutc"
FILE_INTERVAL = "1d"
DAYS_BACK = 365
DAY_MS = 24 * 60 * 60 * 1000
REQUEST_DELAY_SECONDS = 0.12

PAIRS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "XRPUSDT", "SOLUSDT",
    "TRXUSDT", "HYPEUSDT", "ZECUSDT", "DOGEUSDT", "LINKUSDT",
    "ADAUSDT", "XLMUSDT", "BCHUSDT", "UNIUSDT", "LTCUSDT",
    "HBARUSDT", "AVAXUSDT", "SUIUSDT", "SHIBUSDT", "NEARUSDT",
    "AAVEUSDT", "ONDOUSDT", "WLFIUSDT", "ENAUSDT", "DOTUSDT",
    "SKYUSDT", "PUMPUSDT", "WLDUSDT", "ICPUSDT", "PEPEUSDT",
    "ARBUSDT", "ETCUSDT", "POLUSDT", "JUPUSDT", "ALGOUSDT",
    "ATOMUSDT", "RENDERUSDT", "QNTUSDT", "FILUSDT", "VETUSDT",
    "XDCUSDT", "CRVUSDT", "APTUSDT", "INJUSDT", "TIAUSDT",
    "FETUSDT", "SEIUSDT", "LDOUSDT", "IMXUSDT", "OPUSDT",
    "ENSUSDT", "FLOKIUSDT", "JASMYUSDT", "WIFUSDT", "GRTUSDT",
    "COMPUSDT", "THETAUSDT", "AXSUSDT", "NEOUSDT", "MANAUSDT",
    "CHZUSDT", "APEUSDT", "EGLDUSDT", "1INCHUSDT", "SANDUSDT",
    "GALAUSDT", "QTUMUSDT", "SUSDT", "TWTUSDT", "KSMUSDT",
    "SNXUSDT", "BONKUSDT", "ASTERUSDT", "USELESSUSDT",
    "BLURUSDT", "GRAMUSDT",
]

os.makedirs(RAW_DATA_DIR, exist_ok=True)


def instrument_id(pair: str) -> str:
    """Convert BTCUSDT to the OKX perpetual instrument BTC-USDT-SWAP."""
    return f"{pair.removesuffix('USDT')}-USDT-SWAP"


def fetch_klines(pair: str) -> pd.DataFrame | None:
    """Fetch one year of confirmed, closed UTC daily candles from OKX."""
    inst_id = instrument_id(pair)
    cutoff_ms = int((datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).timestamp() * 1000)
    rows: list[list[str]] = []
    after: int | None = None

    while True:
        params = {"instId": inst_id, "bar": BAR, "limit": "300"}
        if after is not None:
            params["after"] = str(after)
        response = requests.get(
            f"{API_BASE_URL}/api/v5/market/history-candles", params=params, timeout=30
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "0":
            print(f"Skip {pair}: OKX error {payload.get('msg', 'unknown error')}")
            return None

        batch = payload.get("data", [])
        if not batch:
            print(f"Skip {pair}: OKX returned no candles")
            return None
        rows.extend(batch)
        oldest = min(int(row[0]) for row in batch)
        if oldest <= cutoff_ms or len(batch) < 300 or oldest == after:
            break
        after = oldest
        time.sleep(REQUEST_DELAY_SECONDS)

    # OKX: ts, open, high, low, close, volume, volumeCcy, quoteVolume, confirm.
    df = pd.DataFrame(
        rows,
        columns=["ts", "open", "high", "low", "close", "volume", "volume_ccy", "quote_asset_volume", "confirm"],
    )
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    for column in ("open", "high", "low", "close", "volume", "quote_asset_volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.dropna().sort_values("ts").drop_duplicates("ts")
    # confirm="0" is the still-forming day, so it must never reach the detector.
    df = df[df["confirm"].astype(str) == "1"].copy()
    df = df[df["ts"] >= cutoff_ms].copy()
    if df.empty:
        print(f"Skip {pair}: no confirmed daily candles")
        return None

    df["close_time"] = df["ts"] + DAY_MS - 1
    df["num_trades"] = None
    df["taker_buy_base"] = None
    df["taker_buy_quote"] = None
    df["ignore"] = None
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.reset_index(drop=True)


def remove_stale_file(pair: str) -> None:
    """Prevent old candles from another exchange being scanned after a failed symbol."""
    output = f"{RAW_DATA_DIR}/{pair}_{FILE_INTERVAL}.csv"
    if os.path.exists(output):
        os.remove(output)
        print(f"Removed stale candles for {pair}")


def main() -> None:
    saved = 0
    for pair in PAIRS:
        try:
            df = fetch_klines(pair)
            if df is None:
                remove_stale_file(pair)
                continue
            output = f"{RAW_DATA_DIR}/{pair}_{FILE_INTERVAL}.csv"
            df.to_csv(output, index=False)
            saved += 1
            print(f"Saved {pair} ({instrument_id(pair)}): {len(df)} OKX Swap candles -> {output}")
        except requests.RequestException as error:
            print(f"Failed {pair}: {error}")
            remove_stale_file(pair)
        except (KeyError, TypeError, ValueError) as error:
            print(f"Failed {pair}: invalid OKX response ({error})")
            remove_stale_file(pair)
        finally:
            time.sleep(REQUEST_DELAY_SECONDS)

    if saved == 0:
        raise SystemExit("OKX returned no data; stopping to avoid using stale CSV files.")


if __name__ == "__main__":
    main()
