"""Half-hour monitor using minute candle history and the current price."""
from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
import requests
from scan_obs_h4 import format_price, load_state, save_state
from telegram_client import send_message, send_digest
API_BASE_URL = "https://www.okx.com"
MAX_ZONE_AGE_HOURS = int(os.getenv("MAX_ZONE_AGE_HOURS", str(45 * 24)))
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
MINUTE_MS = 60000
ALERT_MAX_AGE_MINUTES = int(os.getenv("ALERT_MAX_AGE_MINUTES", "60"))

def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

def monitoring_start(zone):
    # Never examine the BOS candle itself. Round down to recheck the boundary minute.
    activated = utc(zone["bos_time"]) + timedelta(hours=4)
    start = max(activated, utc(zone.get("last_checked_at", activated.isoformat())))
    return int(start.timestamp() * 1000) // MINUTE_MS * MINUTE_MS

def fetch_price(symbol):
    response = requests.get(f"{API_BASE_URL}/api/v5/market/ticker", params={"instId": f"{symbol.removesuffix('USDT')}-USDT-SWAP"}, timeout=20)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != "0" or not payload.get("data"):
        raise ValueError("Current OKX price unavailable")
    return float(payload["data"][0]["last"])

def fetch_history(symbol, start_ms, end_ms):
    """Fetch every minute since the persisted cursor; fail on missing coverage."""
    records = {}
    after = None
    for page in range(2000):
        params = {"instId": f"{symbol.removesuffix('USDT')}-USDT-SWAP", "bar": "1m", "limit": "300"}
        if after is not None:
            params["after"] = str(after)
        endpoint = "candles" if page == 0 else "history-candles"
        response = requests.get(f"{API_BASE_URL}/api/v5/market/{endpoint}", params=params, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != "0" or not payload.get("data"):
            raise ValueError("Minute candle history unavailable")
        batch = payload["data"]
        oldest = min(int(row[0]) for row in batch)
        for row in batch:
            timestamp = int(row[0])
            if start_ms <= timestamp <= end_ms:
                records[timestamp] = {"ts": timestamp, "high": float(row[2]), "low": float(row[3])}
        if oldest <= start_ms:
            break
        if after is not None and oldest >= after:
            raise ValueError("History pagination did not advance")
        after = oldest
    else:
        raise ValueError("History interval exceeds pagination limit")
    # The current minute may still be unavailable; all completed minutes are required.
    for timestamp in range(start_ms, end_ms, MINUTE_MS):
        if timestamp not in records:
            raise ValueError("Gap in minute candle history; cursor was not advanced")
    return [records[t] for t in sorted(records)]

def message(kind, zone, price=None):
    icon = {"approach": "👀", "touch": "⚡", "invalidated": "⛔", "expired": "⌛"}[kind]
    text = f"{icon} {kind.upper()} — {zone['symbol']} (OKX Swap, 4H)\n{zone['direction']} OB: {format_price(zone['bottom'])} – {format_price(zone['top'])}"
    if kind == "touch":
        text += f"\nВремя касания (UTC): {zone.get('touch_candle_at') or zone.get('touched_at')}"
    if kind == "invalidated":
        boundary = zone["bottom"] if zone["direction"] == "bullish" else zone["top"]
        text += f"\nВремя пробоя (UTC): {zone['invalidated_at']}\nПробитая граница: {format_price(boundary)}"
        text += f"\nЦена / экстремум при пробое: {format_price(zone['invalidation_price'])}"
    if price is not None:
        text += f"\nЦена при проверке ботом: {format_price(price)}"
    descriptions = {"approach": "Цена подошла к зоне. Это не сигнал на вход.", "touch": "Обнаружено первое касание зоны OB.", "invalidated": "Граница зоны пробита; наблюдение прекращено.", "expired": "Срок ожидания касания истёк; наблюдение прекращено."}
    return text + "\n" + descriptions[kind]


def deliver_events(zone, events, price, now, recovered):
    """Notify after replay: a terminal breach supersedes a pending touch."""
    if zone["status"] == "invalidated":
        events = [event for event in events if event[0] == "invalidated"]
    for kind, event_at in events:
        if now - event_at > timedelta(minutes=ALERT_MAX_AGE_MINUTES):
            if kind == "touch":
                zone.setdefault("notifications", {})["touch_suppressed"] = "historical"
            label = {"touch": "касание", "invalidated": "пробой", "expired": "истечение срока"}[kind]
            line = (f"{zone['symbol']} {zone['direction']} "
                    f"{format_price(zone['bottom'])}–{format_price(zone['top'])}: "
                    f"{label} {event_at.strftime('%d.%m %H:%M UTC')}")
            if kind == "invalidated":
                boundary = zone["bottom"] if zone["direction"] == "bullish" else zone["top"]
                line += f"; граница {format_price(boundary)}, экстремум {format_price(zone['invalidation_price'])}"
            recovered.append(line)
        else:
            send_message(message(kind, zone, price), DRY_RUN)


def recovery_digest(lines):
    return "🕘 Восстановлена история OB — это прошлые события, не новые сигналы.\n" + "\n".join(lines)


def is_invalidated(zone, price):
    return (zone["direction"] == "bullish" and price < zone["bottom"]) or (zone["direction"] == "bearish" and price > zone["top"])

def apply_observation(zone, candles, price, now, notify=True):
    """Replay fully before delivery; a boundary breach wins within a minute."""
    notifications = zone.setdefault("notifications", {})
    events = []
    if zone["status"] not in {"armed", "touched"}:
        return events
    for candle in sorted(candles, key=lambda item: item["ts"]):
        breach = (zone["direction"] == "bullish" and candle["low"] < zone["bottom"]) or (zone["direction"] == "bearish" and candle["high"] > zone["top"])
        event_at = datetime.fromtimestamp(candle["ts"] / 1000, timezone.utc)
        if breach:
            extreme = candle["low"] if zone["direction"] == "bullish" else candle["high"]
            zone.update(status="invalidated", invalidated_at=event_at.isoformat(), invalidation_price=extreme)
            events.append(("invalidated", event_at))
            break
        if candle["low"] <= zone["top"] and candle["high"] >= zone["bottom"] and not notifications.get("touch"):
            zone["touch_candle_at"] = event_at.isoformat()
            zone.update(status="touched", touched_at=min(now, event_at + timedelta(minutes=1)).isoformat())
            notifications["touch"] = True
            events.append(("touch", event_at))
    if zone["status"] != "invalidated" and price is not None:
        if is_invalidated(zone, price):
            zone.update(status="invalidated", invalidated_at=now.isoformat(), invalidation_price=price)
            events.append(("invalidated", now))
        elif zone["bottom"] <= price <= zone["top"] and not notifications.get("touch"):
            zone.update(status="touched", touched_at=now.isoformat())
            notifications["touch"] = True
            events.append(("touch", now))
        elif zone["status"] == "armed" and not notifications.get("approach"):
            distance = max(zone["bottom"] - price, price - zone["top"], 0.0)
            if distance <= max(float(zone.get("atr") or 0.0) * 0.5, zone["top"] * 0.002):
                notifications["approach"] = True
                events.append(("approach", now))
    if price is not None:
        zone["last_price"] = price
    zone["last_checked_at"] = now.replace(second=0, microsecond=0).isoformat()
    if notify:
        recovered = []
        deliver_events(zone, events, price, now, recovered)
        send_digest(recovered, recovery_digest, DRY_RUN)
    return events


def main():
    state = load_state()
    now = datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000) // MINUTE_MS * MINUTE_MS
    active = [z for z in state["zones"].values() if z["status"] in {"armed", "touched"}]
    by_symbol = {}
    for zone in active:
        by_symbol.setdefault(zone["symbol"], []).append(zone)
    changed = False
    recovered = []
    for symbol, zones in by_symbol.items():
        try:
            start_ms = min(monitoring_start(z) for z in zones)
            candles = fetch_history(symbol, start_ms, end_ms) if start_ms <= end_ms else []
            price = fetch_price(symbol)
        except (KeyError, ValueError, requests.RequestException) as error:
            print(f"Skip {symbol}: {error}")
            continue
        for zone in zones:
            events = []
            start = monitoring_start(zone)
            # Stop replay at expiry so historical pre-expiry touches still count.
            expiry = utc(zone["bos_time"]) + timedelta(hours=MAX_ZONE_AGE_HOURS)
            relevant = [c for c in candles if c["ts"] >= start and (zone["status"] != "armed" or c["ts"] < expiry.timestamp() * 1000)]
            if zone["status"] == "armed" and now > expiry:
                if relevant:
                    historical_now = min(now, expiry)
                    events.extend(apply_observation(zone, relevant, None, historical_now, notify=False))
                if zone["status"] == "armed":
                    zone.update(status="expired", expired_at=expiry.isoformat())
                    events.append(("expired", expiry))
                elif zone["status"] == "touched":
                    events.extend(apply_observation(zone, [c for c in candles if c["ts"] >= int(expiry.timestamp()*1000)], price, now, notify=False))
            else:
                events.extend(apply_observation(zone, relevant, price, now, notify=False))
            deliver_events(zone, events, price, now, recovered)
            changed = True
    send_digest(recovered, recovery_digest, DRY_RUN)
    if changed and not DRY_RUN:
        save_state(state)
        print("H4 monitor state and cursors saved")
    elif changed:
        print("DRY RUN: monitor state was not saved")
    else:
        print("No active zones or no successful observations")

if __name__ == "__main__":
    main()
