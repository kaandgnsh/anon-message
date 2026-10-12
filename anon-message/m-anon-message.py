#!/usr/bin/env python3
"""Consent-based SMS notifications through the Textbelt API.

Real SMS sending is disabled unless --send is supplied. Recipients must be
listed in a local consent registry before previewing or sending a message.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

API_URL = "https://textbelt.com/text"
MAX_MESSAGE_LENGTH = 1000
MAX_SENDS_PER_24_HOURS = 5
MIN_SECONDS_BETWEEN_SENDS = 60
PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def normalize_phone(value: str) -> str:
    """Return an E.164 number, accepting common display punctuation."""
    phone = re.sub(r"[\s().-]", "", value)
    if not PHONE_PATTERN.fullmatch(phone):
        raise ValueError(
            "Use an international E.164 number, e.g. +12025550123 "
            "(8–15 digits, starting with + and a non-zero country code)."
        )
    return phone


def validate_message(value: str) -> str:
    """Validate and normalize the SMS body."""
    message = value.strip()
    if not message:
        raise ValueError("Message must not be empty.")
    if len(message) > MAX_MESSAGE_LENGTH:
        raise ValueError(f"Message must be {MAX_MESSAGE_LENGTH} characters or fewer.")
    return message


def validate_sender(value: str) -> str:
    sender = value.strip()
    if not sender:
        raise ValueError("Provide a sender name with --sender.")
    if len(sender) > 50 or any(ord(char) < 32 for char in sender):
        raise ValueError("Sender name must be 1–50 printable characters.")
    return sender


def load_consent_registry(path: Path) -> set[str]:
    """Load the local list of E.164 recipients who explicitly opted in."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ValueError(
            f"Consent registry not found: {path}. Copy "
            "consented_recipients.example.txt to consented_recipients.txt "
            "and add only recipients who explicitly opted in."
        ) from exc

    recipients: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        try:
            recipients.add(normalize_phone(entry))
        except ValueError as exc:
            raise ValueError(
                f"Invalid phone number in consent registry on line {line_number}: {exc}"
            ) from exc
    return recipients


def recipient_fingerprint(phone: str) -> str:
    """Hash recipient numbers so the local rate-limit file avoids storing them."""
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()


def load_state(path: Path) -> dict[str, Any]:
    """Load rate-limit state; fail closed rather than resetting corrupt state."""
    if not path.exists():
        return {"attempts": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Could not read rate-limit state at {path}; keep the file and inspect it."
        ) from exc

    if not isinstance(state, dict) or not isinstance(state.get("attempts"), list):
        raise ValueError("Rate-limit state has an invalid format; refusing to send.")
    for item in state["attempts"]:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("at"), str)
            or not isinstance(item.get("recipient_hash"), str)
            or not FINGERPRINT_PATTERN.fullmatch(item["recipient_hash"])
        ):
            raise ValueError("Rate-limit state has an invalid entry; refusing to send.")
        try:
            datetime.fromisoformat(item["at"])
        except ValueError as exc:
            raise ValueError("Rate-limit state contains an invalid timestamp.") from exc
    return state


def enforce_rate_limits(
    state: dict[str, Any], phone: str, now: datetime
) -> list[dict[str, str]]:
    """Return recent attempts if global, recipient, and cooldown limits pass."""
    if now.tzinfo is None:
        raise ValueError("Rate-limit clock must be timezone-aware.")
    cutoff = now - timedelta(hours=24)
    attempts: list[dict[str, str]] = []
    for item in state["attempts"]:
        recorded_at = datetime.fromisoformat(item["at"])
        if recorded_at.tzinfo is None:
            raise ValueError("Rate-limit state contains a timezone-naive timestamp.")
        if recorded_at > now:
            raise ValueError("System clock is earlier than the recorded send time.")
        if recorded_at > cutoff:
            attempts.append(item)

    fingerprint = recipient_fingerprint(phone)
    if any(item["recipient_hash"] == fingerprint for item in attempts):
        raise ValueError("Rate limit: only one send per recipient is allowed every 24 hours.")
    if len(attempts) >= MAX_SENDS_PER_24_HOURS:
        raise ValueError(f"Rate limit: maximum {MAX_SENDS_PER_24_HOURS} send attempts per 24 hours.")
    if attempts:
        most_recent = max(datetime.fromisoformat(item["at"]) for item in attempts)
        if (now - most_recent).total_seconds() < MIN_SECONDS_BETWEEN_SENDS:
            raise ValueError(
                f"Rate limit: wait at least {MIN_SECONDS_BETWEEN_SENDS} seconds between sends."
            )
    return attempts


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Write state atomically and restrict permissions where the OS supports it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    try:
        os.chmod(temporary_path, 0o600)
    except OSError:
        pass
    temporary_path.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def send_sms(phone: str, message: str, api_key: str, sender: str) -> dict[str, Any]:
    """Send one request to Textbelt and validate its JSON response."""
    response = requests.post(
        API_URL,
        data={"phone": phone, "message": message, "key": api_key, "sender": sender},
        timeout=10,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("SMS provider returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("SMS provider returned an unexpected response.")
    if payload.get("success") is not True:
        reason = payload.get("error") or payload.get("message") or "Request rejected."
        raise RuntimeError(f"SMS provider rejected the request: {reason}")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send a consent-based SMS notification through Textbelt."
    )
    parser.add_argument("--phone", required=True, help="Recipient number in E.164 format.")
    parser.add_argument("--message", required=True, help="SMS body (maximum 1000 characters).")
    parser.add_argument("--sender", help="Your real name or organization name; required with --send.")
    parser.add_argument(
        "--consent-file",
        type=Path,
        default=Path("consented_recipients.txt"),
        help="Local registry of recipients who explicitly opted in.",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(".anon-message-state.json"),
        help="Local rate-limit state file (do not delete to bypass limits).",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Actually contact Textbelt. Without this flag, the tool only previews.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        phone = normalize_phone(args.phone)
        message = validate_message(args.message)
        recipients = load_consent_registry(args.consent_file)
        if phone not in recipients:
            raise ValueError(
                "Recipient is not in the consent registry. Add the number only after "
                "the recipient has explicitly agreed to receive your messages."
            )

        if not args.send:
            print("DRY RUN: no SMS was sent and no network request was made.")
            print(f"Recipient: {phone}")
            print(f"Message preview: {message}")
            print("To send, review consent and limits, provide --sender, and add --send.")
            return 0

        sender = validate_sender(args.sender or "")
        api_key = os.environ.get("TEXTBELT_API_KEY", "").strip()
        if not api_key:
            raise ValueError("Set TEXTBELT_API_KEY in your environment before using --send.")

        now = datetime.now(timezone.utc)
        state = load_state(args.state_file)
        recent_attempts = enforce_rate_limits(state, phone, now)
        recent_attempts.append(
            {"at": now.isoformat(), "recipient_hash": recipient_fingerprint(phone)}
        )
        state["attempts"] = recent_attempts
        # Record the attempt before the request so network errors cannot bypass limits.
        save_state(args.state_file, state)

        payload = send_sms(phone, message, api_key, sender)
        text_id = payload.get("textId")
        print("Textbelt accepted the SMS request; delivery is not guaranteed.")
        if text_id is not None:
            print(f"Text ID: {text_id}")
        if "quotaRemaining" in payload:
            print(f"Remaining quota: {payload['quotaRemaining']}")
        return 0
    except (OSError, ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
