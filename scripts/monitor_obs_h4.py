"""Hourly price monitor for active 4H Order Blocks."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import requests

from scan_obs_h4 import format_price, load_state, save_state, tradingview_url
from telegram_client import send_message

API_BASE_URL = "https://www.okx.com"
MAX_ZONE_AGE_HOURS = int(os.getenv("MAX_ZONE_AGE_HOURS", str(45 * 24)))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"


def fetch_price(symbol: str) -> float | None:
    inst_id = f"{symbol.removesuffix('USDT')}-USDT-SWAP"
    response = requests.get(f"{API_BASE_URL}/api/v5/market/ticker", params={"instId": inst_id}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "0" or not payload.get("data"):
        print(f"Skip {symbol}: unavailable on OKX Swap")
        return None
    return float(payload["data"][0]["last"])


def message(kind: str, zone: dict, price: float | None = None) -> str:
    icon = {"approach": "👀", "touch": "⚡", "invalidated": "⛔", "expired": "⌛"}[kind]
    text = (
        f"{icon} {kind.upper()} — {zone['symbol']} (OKX Swap, 4H)\n"
        f"{zone['direction']} OB: {format_price(zone['bottom'])} – {format_price(zone['top'])}"
    )
    if price is not None:
        text += f"\nТекущая цена: {format_price(price)}"
    text += f"\n📈 {tradingview_url(zone['symbol'])}"
    descriptions = {
        "approach": "Цена подошла к зоне. Это не сигнал на вход.",
        "touch": "Первое попадание цены в зону OB.",
        "invalidated": "Зона пробита и больше не отслеживается.",
        "expired": "Зона не была отработана в заданный срок и больше не отслеживается.",
    }
    return text + f"\n{descriptions[kind]}"


def is_invalidated(zone: dict, price: float) -> bool:
    return (zone["direction"] == "bullish" and price < zone["bottom"]) or (
        zone["direction"] == "bearish" and price > zone["top"]
    )


def main() -> None:
    state = load_state()
    changed = False
    now = datetime.now(timezone.utc)
    for zone in state["zones"].values():
        if zone["status"] not in {"armed", "touched"}:
            continue
        age_hours = (now - datetime.fromisoformat(zone["bos_time"].replace("Z", "+00:00"))).total_seconds() / 3600
        if zone["status"] == "armed" and age_hours > MAX_ZONE_AGE_HOURS:
            send_message(message("expired", zone), DRY_RUN)
            zone["status"] = "expired"
            zone["expired_at"] = now.isoformat()
            changed = True
            continue
        try:
            price = fetch_price(zone["symbol"])
        except (KeyError, ValueError, requests.RequestException) as error:
            print(f"Price request failed for {zone['symbol']}: {error}")
            continue
        if price is None:
            continue
        zone["last_price"] = price
        zone["last_checked_at"] = now.isoformat()
        if is_invalidated(zone, price):
            send_message(message("invalidated", zone, price), DRY_RUN)
            zone["status"] = "invalidated"
            zone["invalidated_at"] = now.isoformat()
            changed = True
            continue
        inside = zone["bottom"] <= price <= zone["top"]
        notifications = zone.setdefault("notifications", {})
        if inside and not notifications.get("touch"):
            send_message(message("touch", zone, price), DRY_RUN)
            zone["status"] = "touched"
            zone["touched_at"] = now.isoformat()
            notifications["touch"] = True
            changed = True
            continue
        distance = max(zone["bottom"] - price, price - zone["top"], 0.0)
        proximity = max(float(zone.get("atr") or 0.0) * 0.5, zone["top"] * 0.002)
        if zone["status"] == "armed" and distance <= proximity and not notifications.get("approach"):
            send_message(message("approach", zone, price), DRY_RUN)
            notifications["approach"] = True
            changed = True
    if changed and not DRY_RUN:
        save_state(state)
        print("H4 Order Block state updated")
    elif changed:
        print("DRY RUN: H4 OB state was not saved")
    else:
        print("No active H4 Order Block changes")


if __name__ == "__main__":
    main()
