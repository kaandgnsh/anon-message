import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import contextlib
import io

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
        with self.assertRaises(ValueError):
            anon_message.validate_sender("")
        with self.assertRaises(ValueError):
            anon_message.validate_sender("bad\nname")


class ConsentTests(unittest.TestCase):
    def test_registry_ignores_comments_and_normalizes_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "consented_recipients.txt"
            path.write_text("# explicit opt-ins only\n+12025550123\n+1 (202) 555-0124\n", encoding="utf-8")
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


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def attempt(self, phone, seconds_ago=120):
        return {
            "at": (self.now - timedelta(seconds=seconds_ago)).isoformat(),
            "recipient_hash": anon_message.recipient_fingerprint(phone),
        }

    def test_one_send_per_recipient_per_24_hours(self):
        state = {"attempts": [self.attempt("+12025550123")]}
        with self.assertRaisesRegex(ValueError, "one send per recipient"):
            anon_message.enforce_rate_limits(state, "+12025550123", self.now)

    def test_global_cooldown(self):
        state = {"attempts": [self.attempt("+12025550123", seconds_ago=20)]}
        with self.assertRaisesRegex(ValueError, "wait at least"):
            anon_message.enforce_rate_limits(state, "+12025550124", self.now)

    def test_global_24_hour_cap(self):
        phones = [f"+120255501{index:02d}" for index in range(10, 15)]
        state = {
            "attempts": [
                self.attempt(phone, seconds_ago=600 - index * 60)
                for index, phone in enumerate(phones)
            ]
        }
        with self.assertRaisesRegex(ValueError, "maximum 5"):
            anon_message.enforce_rate_limits(state, "+12025550199", self.now)

    def test_old_attempts_are_pruned(self):
        state = {"attempts": [self.attempt("+12025550123", seconds_ago=90000)]}
        self.assertEqual(
            anon_message.enforce_rate_limits(state, "+12025550124", self.now),
            [],
        )

    def test_corrupt_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Could not read rate-limit state"):
                anon_message.load_state(path)


class ProviderTests(unittest.TestCase):
    def test_success_response_is_checked_and_request_is_bounded(self):
        response = Mock()
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
            timeout=10,
        )
        response.raise_for_status.assert_called_once()

    def test_provider_failure_is_not_reported_as_success(self):
        response = Mock()
        response.json.return_value = {"success": False, "error": "Out of quota"}
        with patch.object(anon_message.requests, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "Out of quota"):
                anon_message.send_sms("+12025550123", "Hello", "key", "Kaan DGN")

    def test_dry_run_makes_no_network_request_or_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consent_file = root / "consented_recipients.txt"
            state_file = root / "state.json"
            consent_file.write_text("+12025550123\n", encoding="utf-8")
            output = io.StringIO()
            with patch.object(anon_message.requests, "post") as post:
                with contextlib.redirect_stdout(output):
                    code = anon_message.main([
                        "--phone", "+12025550123",
                        "--message", "Testing only",
                        "--consent-file", str(consent_file),
                        "--state-file", str(state_file),
                    ])
            self.assertEqual(code, 0)
            self.assertIn("no SMS was sent", output.getvalue())
            post.assert_not_called()
            self.assertFalse(state_file.exists())

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


if __name__ == "__main__":
    unittest.main()
