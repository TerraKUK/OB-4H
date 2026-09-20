"""Create and score fresh 4H Order Blocks, then notify Telegram once."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from ob_detector import find_all_obs, find_forming_obs, has_been_touched_or_invalidated
from telegram_client import send_message

RAW_DATA_DIR = Path("data/raw_h4")
STATE_PATH = Path("data/state/active_obs_h4.json")
TIMEFRAME = "4H"
MIN_SCORE = int(os.getenv("MIN_OB_SCORE", "4"))
LOOKBACK_HOURS = int(os.getenv("NEW_OB_LOOKBACK_HOURS", "12"))
FORMING_LOOKBACK_HOURS = int(os.getenv("FORMING_OB_LOOKBACK_HOURS", "8"))
FORMING_MAX_AGE_HOURS = int(os.getenv("FORMING_OB_MAX_AGE_HOURS", "16"))
FORMING_DISPLACEMENT_ATR = float(os.getenv("FORMING_OB_DISPLACEMENT_ATR", "1.0"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"


def as_utc(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {"version": 2, "zones": {}}
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    state["version"] = max(int(state.get("version", 1)), 2)
    state.setdefault("zones", {})
    return state


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def format_price(value: float) -> str:
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def tradingview_url(symbol: str) -> str:
    return f"https://www.tradingview.com/chart/?symbol={quote(f'OKX:{symbol}.P')}&interval=240"


def display_time(value: str) -> str:
    return as_utc(value).strftime("%d.%m %H:%M UTC")


def format_new_digest(zones: list[dict]) -> str:
    lines = [f"📊 Новые сильные OB (OKX Swap, 4H): {len(zones)}"]
    for zone in zones[:10]:
        icon = "🟢" if zone["direction"] == "bullish" else "🔴"
        lines.append(
            f"{icon} {zone['symbol']} {zone['direction']} | "
            f"{format_price(zone['bottom'])}–{format_price(zone['top'])} | {zone['score']}/5\n"
            f"OB: {display_time(zone['ob_time'])} | BOS: {display_time(zone['bos_time'])}\n"
            f"📈 {tradingview_url(zone['symbol'])}"
        )
    if len(zones) > 10:
        lines.append(f"…ещё {len(zones) - 10} зон сохранены для мониторинга.")
    lines.append("Зоны добавлены в почасовой мониторинг первого касания.")
    return "\n".join(lines)


def format_forming_message(zone: dict) -> str:
    icon = "🟢" if zone["direction"] == "bullish" else "🔴"
    return (
        f"⚡ FORMING OB — {zone['symbol']} (OKX Swap, 4H)\n"
        f"{icon} {zone['direction']} candidate: "
        f"{format_price(zone['bottom'])} – {format_price(zone['top'])}\n"
        f"Импульс: {zone['displacement_atr']:.2f} ATR | "
        f"BOS ещё не подтверждён: {format_price(zone['bos_level'])}\n"
        f"Базовая свеча: {display_time(zone['ob_time'])}\n"
        f"📈 {tradingview_url(zone['symbol'])}\n"
        "Раннее предупреждение, не готовый сигнал на вход."
    )


def format_confirmation_message(zone: dict, close: float) -> str:
    return (
        f"✅ Подтверждение реакции — {zone['symbol']} (OKX Swap, 4H)\n"
        f"{zone['direction']} OB: {format_price(zone['bottom'])} – {format_price(zone['top'])}\n"
        f"Закрытие H4: {format_price(close)}\n"
        f"📈 {tradingview_url(zone['symbol'])}\n"
        "Зона была протестирована, а закрытая H4-свеча завершилась в ожидаемую сторону."
    )


def update_confirmations(state: dict, frames: dict[str, pd.DataFrame]) -> bool:
    changed = False
    for zone in state["zones"].values():
        if zone["status"] != "touched" or zone.get("notifications", {}).get("confirmed"):
            continue
        frame = frames.get(zone["symbol"])
        if frame is None or frame.empty:
            continue
        close = float(frame.iloc[-1]["close"])
        confirmed = (zone["direction"] == "bullish" and close > zone["top"]) or (
            zone["direction"] == "bearish" and close < zone["bottom"]
        )
        if confirmed:
            send_message(format_confirmation_message(zone, close), DRY_RUN)
            zone["status"] = "confirmed"
            zone.setdefault("notifications", {})["confirmed"] = True
            changed = True
    return changed


def main() -> None:
    if not RAW_DATA_DIR.exists():
        raise RuntimeError("No downloaded H4 candles found in data/raw_h4")
    state = load_state()
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=LOOKBACK_HOURS)
    forming_cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=FORMING_LOOKBACK_HOURS)
    frames: dict[str, pd.DataFrame] = {}
    new_zones: list[dict] = []
    changed = False
    for path in sorted(RAW_DATA_DIR.glob("*_4h.csv")):
        symbol = path.name.removesuffix("_4h.csv")
        live_frame = pd.read_csv(path, parse_dates=["ts"])
        frame = live_frame
        if "confirm" in frame.columns:
            frame = frame[frame["confirm"].astype(int) == 1].reset_index(drop=True)
        if len(frame) < 205:
            print(f"Skip {symbol}: less than 205 H4 candles")
            continue
        frames[symbol] = frame
        confirmed_blocks = find_all_obs(frame, symbol, timeframe=TIMEFRAME)
        confirmed_keys = {(block.symbol, block.direction, block.ob_time) for block in confirmed_blocks}
        for block in confirmed_blocks:
            zone = block.to_dict()
            # Remove the early-stage record when this exact base candle gets BOS.
            for old_id, old_zone in list(state["zones"].items()):
                if old_zone.get("stage") != "forming":
                    continue
                old_key = (old_zone.get("symbol"), old_zone.get("direction"), old_zone.get("ob_time"))
                if old_key == (zone["symbol"], zone["direction"], zone["ob_time"]):
                    del state["zones"][old_id]
                    changed = True
            if as_utc(zone["bos_time"]) < cutoff or zone["score"] < MIN_SCORE or zone["id"] in state["zones"]:
                continue
            if has_been_touched_or_invalidated(frame, block) != "armed":
                continue
            zone["status"] = "armed"
            zone["stage"] = "confirmed"
            zone["created_at"] = zone["bos_time"]
            zone["notifications"] = {"new": True, "approach": False, "touch": False, "confirmed": False}
            state["zones"][zone["id"]] = zone
            new_zones.append(zone)
            changed = True
            print(f"NEW {zone['id']}")

        for block in find_forming_obs(
            live_frame,
            symbol,
            timeframe=TIMEFRAME,
            displacement_atr=FORMING_DISPLACEMENT_ATR,
        ):
            zone = block.to_dict()
            key = (zone["symbol"], zone["direction"], zone["ob_time"])
            if key in confirmed_keys or as_utc(zone["detected_at"]) < forming_cutoff:
                continue
            if zone["id"] in state["zones"]:
                continue
            zone["status"] = "forming"
            zone["created_at"] = zone["detected_at"]
            zone["notifications"] = {"forming": True, "confirmed": False}
            state["zones"][zone["id"]] = zone
            send_message(format_forming_message(zone), DRY_RUN)
            changed = True
            print(f"FORMING {zone['id']}")

    now = pd.Timestamp.now(tz="UTC")
    for zone in state["zones"].values():
        if zone.get("stage") != "forming" or zone.get("status") != "forming":
            continue
        if now - as_utc(zone["created_at"]) > pd.Timedelta(hours=FORMING_MAX_AGE_HOURS):
            zone["status"] = "expired"
            zone["expired_at"] = now.isoformat()
            changed = True
    if new_zones:
        send_message(format_new_digest(new_zones), DRY_RUN)
    changed = update_confirmations(state, frames) or changed
    if changed and not DRY_RUN:
        save_state(state)
        print(f"Saved {len(state['zones'])} H4 zones")
    elif changed:
        print("DRY RUN: H4 OB state was not saved")
    else:
        print("No new or updated H4 Order Blocks")


if __name__ == "__main__":
    main()
