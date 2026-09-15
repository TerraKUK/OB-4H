"""Create and score fresh daily Order Blocks, then notify Telegram once."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from divergence_detector import find_regular_rsi_divergences
from ob_detector import find_all_obs, has_been_touched_or_invalidated
from state_store import load_state, save_state
from telegram_client import send_message


RAW_DATA_DIR = Path("data/raw")
MIN_SCORE = int(os.getenv("MIN_OB_SCORE", "4"))
LOOKBACK_DAYS = int(os.getenv("NEW_OB_LOOKBACK_DAYS", "3"))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
RSI_PERIOD = int(os.getenv("RSI_PERIOD", "14"))
DIVERGENCE_PIVOT_LEFT = int(os.getenv("DIVERGENCE_PIVOT_LEFT", "5"))
DIVERGENCE_PIVOT_RIGHT = int(os.getenv("DIVERGENCE_PIVOT_RIGHT", "5"))
DIVERGENCE_MIN_BARS = int(os.getenv("DIVERGENCE_MIN_BARS", "5"))
DIVERGENCE_MAX_BARS = int(os.getenv("DIVERGENCE_MAX_BARS", "60"))
DIVERGENCE_LOOKBACK_DAYS = int(os.getenv("DIVERGENCE_LOOKBACK_DAYS", "3"))


def format_price(value: float) -> str:
    return f"{value:,.8f}".rstrip("0").rstrip(".")


def tradingview_url(symbol: str) -> str:
    """Return the D1 TradingView chart for the same OKX perpetual used by the bot."""
    return f"https://www.tradingview.com/chart/?symbol=OKX%3A{symbol}.P&interval=D"


def format_new_message(zone: dict) -> str:
    emoji = "🟢" if zone["direction"] == "bullish" else "🔴"
    return (
        f"{emoji} Новый {zone['direction']} OB — {zone['symbol']} (OKX Swap, 1D)\n"
        f"Зона: {format_price(zone['bottom'])} – {format_price(zone['top'])}\n"
        f"OB: {zone['ob_time']} | BOS: {zone['bos_time']}\n"
        f"Оценка: {zone['score']}/5\n"
        f"Причины: {', '.join(zone['reasons'])}\n"
        f"График OKX D1: {tradingview_url(zone['symbol'])}\n"
        "Статус: ожидание первого касания"
    )


def format_new_digest(zones: list[dict]) -> str:
    lines = [f"📊 Новые сильные OB (OKX Swap, 1D): {len(zones)}"]
    for zone in zones[:10]:
        arrow = "🟢" if zone["direction"] == "bullish" else "🔴"
        lines.append(
            f"{arrow} {zone['symbol']} {zone['direction']} | "
            f"{format_price(zone['bottom'])}–{format_price(zone['top'])} | {zone['score']}/5\n"
            f"📈 {tradingview_url(zone['symbol'])}"
        )
    if len(zones) > 10:
        lines.append(f"…ещё {len(zones) - 10} зон сохранено для мониторинга.")
    lines.append("Новые зоны добавлены в мониторинг первого касания.")
    return "\n".join(lines)


def format_divergence_digest(divergences: list[dict]) -> str:
    lines = [f"🟣 Новые regular RSI-дивергенции (OKX Swap, 1D): {len(divergences)}"]
    for divergence in divergences[:10]:
        icon = "🟢" if divergence["direction"] == "bullish" else "🔴"
        price_label = "LL" if divergence["direction"] == "bullish" else "HH"
        rsi_label = "HL" if divergence["direction"] == "bullish" else "LH"
        lines.append(
            f"{icon} {divergence['symbol']} {divergence['direction']} | "
            f"цена {price_label}: {format_price(divergence['first_price'])}→{format_price(divergence['price'])} | "
            f"RSI {rsi_label}: {divergence['first_rsi']:.1f}→{divergence['rsi']:.1f}\n"
            f"Пивот: {divergence['pivot_time']} | подтверждён: {divergence['confirmed_time']}\n"
            f"📈 {tradingview_url(divergence['symbol'])}"
        )
    if len(divergences) > 10:
        lines.append(f"…ещё {len(divergences) - 10} дивергенций сохранено без повторных уведомлений.")
    lines.append("Это раннее наблюдение, а не сигнал на вход. OB-подтверждение будет отслеживаться отдельно.")
    return "\n".join(lines)


def format_confirmation_message(zone: dict, close: float) -> str:
    emoji = "✅"
    return (
        f"{emoji} Подтверждение реакции — {zone['symbol']} (OKX Swap, 1D)\n"
        f"{zone['direction']} OB: {format_price(zone['bottom'])} – {format_price(zone['top'])}\n"
        f"Закрытие D1: {format_price(close)}\n"
        f"График OKX D1: {tradingview_url(zone['symbol'])}\n"
        "Зона уже была протестирована, закрытие дня произошло в ожидаемую сторону."
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
        raise RuntimeError("No downloaded candles found in data/raw")

    state = load_state()
    changed = False
    new_zones: list[dict] = []
    new_divergences: list[dict] = []
    frames: dict[str, pd.DataFrame] = {}
    cutoff = date.today() - timedelta(days=LOOKBACK_DAYS)

    for path in sorted(RAW_DATA_DIR.glob("*_1d.csv")):
        symbol = path.name.removesuffix("_1d.csv")
        frame = pd.read_csv(path, parse_dates=["ts"])
        if len(frame) < 205:
            print(f"Skip {symbol}: less than 205 candles")
            continue
        frames[symbol] = frame

        divergence_cutoff = date.today() - timedelta(days=DIVERGENCE_LOOKBACK_DAYS)
        for divergence in find_regular_rsi_divergences(
            frame,
            symbol,
            rsi_period=RSI_PERIOD,
            pivot_left=DIVERGENCE_PIVOT_LEFT,
            pivot_right=DIVERGENCE_PIVOT_RIGHT,
            min_bars_between=DIVERGENCE_MIN_BARS,
            max_bars_between=DIVERGENCE_MAX_BARS,
        ):
            signal = divergence.to_dict()
            confirmed_date = date.fromisoformat(signal["confirmed_time"])
            if confirmed_date < divergence_cutoff or signal["id"] in state["divergences"]:
                continue
            signal["created_at"] = signal["confirmed_time"]
            state["divergences"][signal["id"]] = signal
            new_divergences.append(signal)
            print(f"DIVERGENCE {signal['id']}")
            changed = True

        for block in find_all_obs(frame, symbol):
            zone = block.to_dict()
            bos_date = date.fromisoformat(zone["bos_time"])
            if bos_date < cutoff or zone["score"] < MIN_SCORE or zone["id"] in state["zones"]:
                continue
            if has_been_touched_or_invalidated(frame, block) != "armed":
                continue
            zone["status"] = "armed"
            zone["created_at"] = zone["bos_time"]
            zone["notifications"] = {"new": True, "approach": False, "touch": False, "confirmed": False}
            state["zones"][zone["id"]] = zone
            new_zones.append(zone)
            print(f"NEW {zone['id']}")
            changed = True

    if new_zones:
        send_message(format_new_digest(new_zones), DRY_RUN)
    if new_divergences:
        send_message(format_divergence_digest(new_divergences), DRY_RUN)

    changed = update_confirmations(state, frames) or changed
    if changed and not DRY_RUN:
        save_state(state)
        print(f"Saved {len(state['zones'])} active zones")
    elif changed:
        print("DRY RUN: active zone state was not saved")
    else:
        print("No new or updated active zones")


if __name__ == "__main__":
    main()
