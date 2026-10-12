import contextlib
import importlib.util
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "anon-message" / "m-anon-message.py"
SPEC = importlib.util.spec_from_file_location("anon_message", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
anon_message = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(anon_message)


class PhoneAndMessageTests(unittest.TestCase):
    def test_normalize_phone_accepts_e164_and_display_punctuation(self):
        self.assertEqual(anon_message.normalize_phone(" +1 (202) 555-0123 "), "+12025550123")

    def test_normalize_phone_rejects_non_e164(self):
        for value in ("2025550123", "+012345678", "+123", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    anon_message.normalize_phone(value)

    def test_validate_message_strips_whitespace_and_rejects_empty_or_long(self):
        self.assertEqual(anon_message.validate_message("  Hello  "), "Hello")
        with self.assertRaises(ValueError):
            anon_message.validate_message("  ")
        with self.assertRaises(ValueError):
            anon_message.validate_message("x" * (anon_message.MAX_MESSAGE_LENGTH + 1))

    def test_sender_must_be_printable_and_bounded(self):
        self.assertEqual(anon_message.validate_sender(" Kaan DGN "), "Kaan DGN")
        for value in ("", "bad\nname", "x" * 51):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    anon_message.validate_sender(value)


class ConsentTests(unittest.TestCase):
    def test_registry_ignores_comments_and_normalizes_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consented_recipients.txt"
            path.write_text(
                "# explicit opt-ins only\n+12025550123\n+1 (202) 555-0124\n",
                encoding="utf-8",
            )
            self.assertEqual(
                anon_message.load_consent_registry(path),
                {"+12025550123", "+12025550124"},
            )

    def test_registry_rejects_invalid_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recipients.txt"
            path.write_text("not-a-phone\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                anon_message.load_consent_registry(path)

    def test_missing_registry_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Consent registry not found"):
                anon_message.load_consent_registry(Path(directory) / "missing.txt")


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_keyed_and_deterministic(self):
        phone = "+12025550123"
        key_a = b"a" * anon_message.FINGERPRINT_KEY_BYTES
        key_b = b"b" * anon_message.FINGERPRINT_KEY_BYTES
        fingerprint = anon_message.recipient_fingerprint(phone, key_a)
        self.assertEqual(fingerprint, anon_message.recipient_fingerprint(phone, key_a))
        self.assertNotEqual(fingerprint, anon_message.recipient_fingerprint(phone, key_b))
        self.assertNotEqual(fingerprint, __import__("hashlib").sha256(phone.encode()).hexdigest())

    def test_key_is_created_and_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            key1 = anon_message.load_or_create_fingerprint_key(path)
            key2 = anon_message.load_or_create_fingerprint_key(path)
            self.assertEqual(len(key1), anon_message.FINGERPRINT_KEY_BYTES)
            self.assertEqual(key1, key2)
            self.assertEqual(path.read_text(encoding="ascii").strip(), key1.hex())

    def test_corrupt_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_text("not-a-valid-key", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "Fingerprint key.*invalid"):
                anon_message.load_or_create_fingerprint_key(path)


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.key = b"x" * anon_message.FINGERPRINT_KEY_BYTES

    def attempt(self, phone, seconds_ago=120):
        return {
            "at": (self.now - timedelta(seconds=seconds_ago)).isoformat(),
            "recipient_hash": anon_message.recipient_fingerprint(phone, self.key),
        }

    def test_one_send_per_recipient_per_24_hours(self):
        state = {"attempts": [self.attempt("+12025550123")]}
        with self.assertRaisesRegex(ValueError, "one send per recipient"):
            anon_message.enforce_rate_limits(state, "+12025550123", self.now, self.key)

    def test_global_cooldown(self):
        state = {"attempts": [self.attempt("+12025550123", seconds_ago=20)]}
        with self.assertRaisesRegex(ValueError, "wait at least"):
            anon_message.enforce_rate_limits(state, "+12025550124", self.now, self.key)

    def test_global_24_hour_cap(self):
        phones = [f"+120255501{index:02d}" for index in range(10, 15)]
        state = {
            "attempts": [
                self.attempt(phone, seconds_ago=600 - index * 60)
                for index, phone in enumerate(phones)
            ]
        }
        with self.assertRaisesRegex(ValueError, "maximum 5"):
            anon_message.enforce_rate_limits(state, "+12025550199", self.now, self.key)

    def test_old_attempts_are_pruned(self):
        state = {"attempts": [self.attempt("+12025550123", seconds_ago=90000)]}
        self.assertEqual(
            anon_message.enforce_rate_limits(state, "+12025550124", self.now, self.key),
            [],
        )

    def test_naive_clock_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            anon_message.enforce_rate_limits(
                {"attempts": []}, "+12025550124", datetime(2026, 1, 1), self.key
            )

    def test_corrupt_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Could not read rate-limit state"):
                anon_message.load_state(path)

    def test_lock_serializes_access_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "state.lock"
            with anon_message.state_file_lock(lock):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())

    def test_stale_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / "state.lock"
            lock.write_text("stale lock", encoding="ascii")
            old = time.time() - anon_message.STALE_LOCK_SECONDS - 5
            os.utime(lock, (old, old))
            with anon_message.state_file_lock(lock, timeout=0.2):
                self.assertTrue(lock.exists())
            self.assertFalse(lock.exists())


class ProviderTests(unittest.TestCase):
    def test_success_response_is_checked_and_request_is_bounded(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"success": True, "textId": "123"}
        with patch.object(anon_message.requests, "post", return_value=response) as post:
            result = anon_message.send_sms("+12025550123", "Hello", "key", "Kaan DGN")
        self.assertEqual(result["textId"], "123")
        post.assert_called_once_with(
            anon_message.API_URL,
            data={
                "phone": "+12025550123",
                "message": "Hello",
                "key": "key",
                "sender": "Kaan DGN",
            },
            timeout=(3, 10),
            allow_redirects=False,
        )
        response.raise_for_status.assert_called_once()

    def test_provider_failure_is_not_reported_as_success(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {"success": False, "error": "Out of quota"}
        with patch.object(anon_message.requests, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "Out of quota"):
                anon_message.send_sms("+12025550123", "Hello", "key", "Kaan DGN")

    def test_redirect_is_rejected(self):
        response = Mock()
        response.status_code = 302
        with patch.object(anon_message.requests, "post", return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, "refusing to forward credentials"):
                anon_message.send_sms("+12025550123", "Hello", "key", "Kaan DGN")
        self.assertFalse(response.json.called)
        self.assertFalse(response.raise_for_status.called)
        self.assertEqual(post.call_args.kwargs["allow_redirects"], False)

    def test_http_error_has_provider_context(self):
        response = Mock()
        response.status_code = 429
        response.json.return_value = {"success": False, "error": "Too many requests"}
        response.raise_for_status.side_effect = anon_message.requests.HTTPError("HTTP 429")
        with patch.object(anon_message.requests, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429: Too many requests"):
                anon_message.send_sms("+12025550123", "Hello", "key", "Kaan DGN")

    def test_dry_run_makes_no_network_request_or_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consent_file = root / "consented_recipients.txt"
            state_file = root / "state.json"
            key_file = root / "fingerprint-key"
            consent_file.write_text("+12025550123\n", encoding="utf-8")
            output = io.StringIO()
            with patch.object(anon_message.requests, "post") as post:
                with contextlib.redirect_stdout(output):
                    code = anon_message.main([
                        "--phone", "+12025550123",
                        "--message", "Testing only",
                        "--consent-file", str(consent_file),
                        "--state-file", str(state_file),
                        "--fingerprint-key-file", str(key_file),
                    ])
            self.assertEqual(code, 0)
            self.assertIn("no SMS was sent", output.getvalue())
            post.assert_not_called()
            self.assertFalse(state_file.exists())
            self.assertFalse(key_file.exists())

    def test_recipient_without_opt_in_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consent_file = root / "consented_recipients.txt"
            consent_file.write_text("# no opt-ins\n", encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                code = anon_message.main([
                    "--phone", "+12025550123",
                    "--message", "Testing only",
                    "--consent-file", str(consent_file),
                ])
            self.assertEqual(code, 1)
            self.assertIn("not in the consent registry", errors.getvalue())

    def test_successful_send_reserves_rate_limit_without_storing_phone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            phone = "+12025550123"
            consent_file = root / "consented_recipients.txt"
            state_file = root / "state.json"
            key_file = root / "fingerprint-key"
            consent_file.write_text(phone + "\n", encoding="utf-8")
            output = io.StringIO()
            with patch.dict(os.environ, {"TEXTBELT_API_KEY": "test-key"}):
                with patch.object(
                    anon_message,
                    "send_sms",
                    return_value={"success": True, "textId": "abc", "quotaRemaining": 3},
                ) as send:
                    with contextlib.redirect_stdout(output):
                        code = anon_message.main([
                            "--phone", phone,
                            "--message", "Opt-in test",
                            "--sender", "Kaan DGN",
                            "--consent-file", str(consent_file),
                            "--state-file", str(state_file),
                            "--fingerprint-key-file", str(key_file),
                            "--send",
                        ])
            self.assertEqual(code, 0)
            send.assert_called_once_with(phone, "Opt-in test", "test-key", "Kaan DGN")
            serialized_state = state_file.read_text(encoding="utf-8")
            self.assertNotIn(phone, serialized_state)
            self.assertIn("recipient_hash", serialized_state)
            self.assertIn("accepted the SMS request", output.getvalue())


if __name__ == "__main__":
    unittest.main()
