"""Find new confirmed TradingView-style regular RSI divergences on OKX 4H data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from divergence_detector import find_regular_rsi_divergences
from telegram_client import send_message

RAW_DATA_DIR = Path("data/raw_h4")
STATE_PATH = Path("data/state/rsi_divergences_h4.json")
TIMEFRAME = "4H"
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
LOOKBACK_HOURS = int(os.getenv("DIVERGENCE_LOOKBACK_HOURS", "12"))
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
PIVOT_LEFT = int(os.getenv("RSI_PIVOT_LEFT", "5"))
PIVOT_RIGHT = int(os.getenv("RSI_PIVOT_RIGHT", "5"))
MIN_BARS = int(os.getenv("RSI_MIN_BARS", "5"))
MAX_BARS = int(os.getenv("RSI_MAX_BARS", "60"))


def as_utc(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 1, "divergences": {}}
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state.setdefault("version", 1)
    state.setdefault("divergences", {})
    return state


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_price(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")


def tradingview_url(symbol: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol={quote(f'OKX:{symbol}.P')}&interval=240"


def display_time(value: str) -> str:
    return as_utc(value).strftime("%d.%m %H:%M UTC")


def format_digest(signals: list[dict]) -> str:
    lines = [f"🟣 Новые regular RSI-дивергенции (OKX Swap, 4H): {len(signals)}"]
    for signal in signals[:10]:
        icon = "🟢" if signal["direction"] == "bullish" else "🔴"
        direction = "Bull" if signal["direction"] == "bullish" else "Bear"
        lines.append(f"{icon} {signal['symbol']} {direction} | цена {format_price(signal['first_price'])} → {format_price(signal['price'])} | RSI {signal['first_rsi']:.1f} → {signal['rsi']:.1f}")
        lines.append(f"Подтверждена: {display_time(signal['confirmed_time'])}")
        lines.append(f"📈 {tradingview_url(signal['symbol'])}")
    if len(signals) > 10:
        lines.append(f"…ещё {len(signals) - 10} сигналов сохранены без повторного уведомления.")
    return "\n".join(lines)


def main() -> None:
    state = load_state()
    known = state["divergences"]
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=LOOKBACK_HOURS)
    new_signals: list[dict] = []
    for csv_path in sorted(RAW_DATA_DIR.glob("*_4h.csv")):
        symbol = csv_path.name.removesuffix("_4h.csv")
        frame = pd.read_csv(csv_path, parse_dates=["ts"])
        if "confirm" in frame.columns:
            frame = frame[frame["confirm"].astype(int) == 1].reset_index(drop=True)
        if len(frame) < RSI_PERIOD + PIVOT_LEFT + PIVOT_RIGHT + MAX_BARS:
            print(f"Skip {symbol}: not enough 4H candles")
            continue
        for divergence in find_regular_rsi_divergences(
            frame, symbol=symbol, timeframe=TIMEFRAME, rsi_period=RSI_PERIOD,
            pivot_left=PIVOT_LEFT, pivot_right=PIVOT_RIGHT,
            min_bars_between=MIN_BARS, max_bars_between=MAX_BARS,
        ):
            signal = divergence.to_dict()
            if as_utc(signal["confirmed_time"]) < cutoff or signal["id"] in known:
                continue
            known[signal["id"]] = signal
            new_signals.append(signal)
            print(f"NEW {signal['id']}")
    if new_signals:
        send_message(format_digest(new_signals), DRY_RUN)
    else:
        print("No new confirmed 4H RSI divergences")
    if not DRY_RUN:
        save_state(state)


if __name__ == "__main__":
    main()
