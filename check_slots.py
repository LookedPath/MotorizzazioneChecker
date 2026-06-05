#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_BOOKING_URL = (
    "https://bookings.cloud.microsoft/BookingsService/api/V1/"
    "bookingBusinessesc2/MotorizzazioneVerona@mitgov.onmicrosoft.com/GetStaffAvailability"
)
DEFAULT_SERVICE_ID = "bf3d1cb6-95d3-4996-a1f7-3d9ba808c594"
DEFAULT_STAFF_IDS = ["ee78c5a6-5146-43a1-b8ac-3508836445f2"]
DEFAULT_TIME_ZONE = "W. Europe Standard Time"
DEFAULT_STATE_FILE = ".state/availability_state.json"
DEFAULT_BOOKING_LINK = (
    "https://www.dgtne.it/uffici-motorizzazione-civile/veneto/"
    "ufficio-motorizzazione-civile-verona/prenotazione/"
)
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)
AVAILABLE_STATUSES = {"available", "slotsavailable"}


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
            value = value[1:-1]

        os.environ[key] = value


def env_list(name: str, default: List[str]) -> List[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    return datetime(dt.year + month_index // 12, month_index % 12 + 1, 1)


def build_payload() -> Dict[str, Any]:
    now = datetime.now()
    start_dt = datetime(now.year, now.month, 1)
    end_dt = add_months(start_dt, 3)
    time_zone = os.getenv("REQUEST_TIME_ZONE", DEFAULT_TIME_ZONE)

    return {
        "serviceId": os.getenv("SERVICE_ID", DEFAULT_SERVICE_ID),
        "staffIds": env_list("STAFF_IDS", DEFAULT_STAFF_IDS),
        "startDateTime": {
            "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": time_zone,
        },
        "endDateTime": {
            "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": time_zone,
        },
    }


def post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str]) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset("utf-8")
        body = response.read().decode(charset)
    return json.loads(body)


def iter_slot_candidates(node):
    if isinstance(node, dict):
        normalized = {str(key).lower(): value for key, value in node.items()}
        if any(key in normalized for key in ("startdatetime", "starttime", "sdt", "start")):
            if any(key in normalized for key in ("enddatetime", "endtime", "edt", "end")):
                yield node

        for value in node.values():
            yield from iter_slot_candidates(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_slot_candidates(item)


def extract_value(slot: Dict[str, Any], *names: str):
    normalized = {str(key).lower(): value for key, value in slot.items()}
    for name in names:
        if name.lower() in normalized:
            return normalized[name.lower()]
    return None


def extract_datetime_value(value):
    if isinstance(value, dict):
        inner = {str(key).lower(): item for key, item in value.items()}
        if "datetime" in inner:
            return str(inner["datetime"])
    if value is None:
        return None
    return str(value)


def normalize_status(value) -> str:
    if value is None:
        return ""

    text = str(value).strip().lower()
    if text.startswith("bookingsavailabilitystatus_"):
        text = text.removeprefix("bookingsavailabilitystatus_")
    return text.replace("_", "")


def normalize_slot(slot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    start = extract_datetime_value(
        extract_value(slot, "startDateTime", "startTime", "sdt", "start")
    )
    end = extract_datetime_value(
        extract_value(slot, "endDateTime", "endTime", "edt", "end")
    )
    if not start or not end:
        return None

    status = normalize_status(extract_value(slot, "status"))
    if status:
        is_bookable = status in AVAILABLE_STATUSES
    else:
        is_bookable = extract_value(slot, "isBookable", "bookable", "availability")
        if isinstance(is_bookable, str):
            is_bookable = is_bookable.lower() not in {
                "false",
                "0",
                "busy",
                "unavailable",
                "outofoffice",
            }
        elif is_bookable is None:
            is_bookable = True
        else:
            is_bookable = bool(is_bookable)

    if not is_bookable:
        return None

    return {
        "start": str(start),
        "end": str(end),
        "status": status or "unknown",
        "raw": slot,
    }


def extract_slots(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("staffAvailabilityResponse"), list):
        slots = []
        seen = set()
        for staff_entry in data["staffAvailabilityResponse"]:
            items = staff_entry.get("availabilityItems")
            if not isinstance(items, list):
                continue

            for item in items:
                normalized = normalize_slot(item)
                if not normalized:
                    continue

                key = (normalized["start"], normalized["end"])
                if key in seen:
                    continue

                seen.add(key)
                slots.append(normalized)

        slots.sort(key=lambda item: (item["start"], item["end"]))
        return slots

    slots = []
    seen = set()
    for candidate in iter_slot_candidates(data):
        normalized = normalize_slot(candidate)
        if not normalized:
            continue

        key = (normalized["start"], normalized["end"])
        if key in seen:
            continue

        seen.add(key)
        slots.append(normalized)

    slots.sort(key=lambda item: (item["start"], item["end"]))
    return slots


def state_file_path() -> Path:
    configured = os.getenv("STATE_FILE", DEFAULT_STATE_FILE)
    return Path(configured)


def load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def fingerprint_slots(slots: List[Dict[str, Any]]) -> str:
    payload = [{"start": slot["start"], "end": slot["end"]} for slot in slots]
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def scalar_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def infer_json_model(node: Any) -> Any:
    if isinstance(node, dict):
        sorted_items = sorted(node.items(), key=lambda item: str(item[0]))
        return {str(key): infer_json_model(value) for key, value in sorted_items}
    if isinstance(node, list):
        item_models = []
        seen = set()
        for item in node:
            model = infer_json_model(item)
            encoded = json.dumps(model, separators=(",", ":"), sort_keys=True)
            if encoded in seen:
                continue
            seen.add(encoded)
            item_models.append((encoded, model))

        item_models.sort(key=lambda item: item[0])
        return [item for _, item in item_models]
    return scalar_type_name(node)


def fingerprint_response_model(data: Dict[str, Any]) -> Tuple[str, Any]:
    model = infer_json_model(data)
    encoded = json.dumps(model, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest(), model


def current_timestamp() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def summarize_model(model: Any, max_length: int = 600) -> str:
    summary = json.dumps(model, sort_keys=True, separators=(",", ":"))
    if len(summary) <= max_length:
        return summary
    return f"{summary[: max_length - 3]}..."


def format_slot_day(value: str) -> str:
    date_text = value.split("T", 1)[0]
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return date_text
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B')} {day.year}"


def summarize_slot_days(slots: List[Dict[str, Any]]) -> List[Tuple[str, int]]:
    counts: Dict[str, int] = {}
    for slot in slots:
        date_text = str(slot["start"]).split("T", 1)[0]
        counts[date_text] = counts.get(date_text, 0) + 1
    return [(date_text, counts[date_text]) for date_text in sorted(counts)]


def format_message(
    slots: List[Dict[str, Any]],
    model_changed: bool,
    previous_model_fingerprint: Optional[str],
    current_model_fingerprint: str,
    current_model: Any,
) -> str:
    lines = [
        "Motorizzazione Verona update detected.",
        "",
    ]

    if model_changed:
        lines.extend(
            [
                "API response model changed.",
                (
                    "Previous model fingerprint: "
                    f"{previous_model_fingerprint if previous_model_fingerprint is not None else 'n/a'}"
                ),
                f"Current model fingerprint: {current_model_fingerprint}",
                f"Current model: {summarize_model(current_model)}",
                "",
            ]
        )

    if slots:
        lines.extend(
            [
                "Bookable slots detected.",
                "",
            ]
        )
    else:
        lines.append("No bookable slots extracted from the current response.")

    lines.extend(
        [
            f"Booking link: {DEFAULT_BOOKING_LINK}",
        ]
    )

    if not slots:
        return "\n".join(lines)

    lines.extend(["", "Available days:"])

    for date_text, count in summarize_slot_days(slots):
        suffix = "slot" if count == 1 else "slots"
        lines.append(f"- {format_slot_day(date_text)}: {count} {suffix}")

    return "\n".join(lines)


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def send_telegram_messages(token: str, chat_ids: List[str], text: str) -> None:
    for chat_id in chat_ids:
        send_telegram_message(token, chat_id, text)


def unique_strings(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def validate_telegram_config() -> Tuple[str, List[str]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids = env_list("TELEGRAM_CHAT_ID", [])
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")
    chat_ids = unique_strings(chat_ids)
    if not chat_ids:
        raise ValueError("TELEGRAM_CHAT_ID is required")
    return token, chat_ids


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check Motorizzazione Verona slots and send Telegram notifications."
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="Send a test Telegram notification and exit.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    load_env_file(Path(__file__).with_name(".env"))
    args = parse_args(argv)
    checked_at = current_timestamp()

    try:
        if args.test_notification:
            telegram_token, telegram_chat_ids = validate_telegram_config()
            send_telegram_messages(
                telegram_token,
                telegram_chat_ids,
                (
                    "Test notification from MotorizzazioneChecker "
                    f"at {checked_at}.\n\nBooking link: {DEFAULT_BOOKING_LINK}"
                ),
            )
            print(json.dumps({"checkedAt": checked_at, "notified": True, "notificationTest": True}))
            return 0

        booking_url = os.getenv("BOOKING_URL", DEFAULT_BOOKING_URL)
        request_payload = build_payload()
        response_json = post_json(
            booking_url,
            request_payload,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Origin": "https://bookings.office.com",
                "Referer": "https://bookings.office.com/",
                "User-Agent": os.getenv("USER_AGENT", DEFAULT_USER_AGENT),
            },
        )
        slots = extract_slots(response_json)
        current_fingerprint = fingerprint_slots(slots)
        current_model_fingerprint, current_model = fingerprint_response_model(response_json)
        state_path = state_file_path()
        state = load_state(state_path)
        previous_slot_count = int(state.get("slotCount") or 0)
        previous_model_fingerprint = state.get("responseModelFingerprint")
        model_changed = (
            previous_model_fingerprint is not None
            and current_model_fingerprint != previous_model_fingerprint
        )
        slot_available_after_zero = bool(slots) and previous_slot_count == 0
        should_notify = model_changed or slot_available_after_zero

        if should_notify:
            telegram_token, telegram_chat_ids = validate_telegram_config()
            send_telegram_messages(
                telegram_token,
                telegram_chat_ids,
                format_message(
                    slots,
                    model_changed,
                    previous_model_fingerprint,
                    current_model_fingerprint,
                    current_model,
                ),
            )

        save_state(
            state_path,
            {
                "checkedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "fingerprint": current_fingerprint,
                "responseModelFingerprint": current_model_fingerprint,
                "responseModel": current_model,
                "slotCount": len(slots),
                "slots": [{"start": slot["start"], "end": slot["end"]} for slot in slots],
            },
        )

        print(
            json.dumps(
                {
                    "checkedAt": checked_at,
                    "windowStart": request_payload["startDateTime"]["dateTime"],
                    "windowEnd": request_payload["endDateTime"]["dateTime"],
                    "modelChanged": model_changed,
                    "responseModelFingerprint": current_model_fingerprint,
                    "slotCount": len(slots),
                    "notified": should_notify,
                }
            )
        )
        return 0
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP error {exc.code}: {error_body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
