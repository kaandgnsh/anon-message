# anon-message

A small Python command-line utility for **consent-based SMS notifications** through the [Textbelt API](https://docs.textbelt.com/).

This project is not anonymous. Phone numbers and message contents are sent to the SMS provider when a real send is requested. Use it only for recipients who have explicitly opted in and only in ways allowed by applicable law and the provider's policies.

## Features

- **Dry-run by default:** the script makes no network request unless `--send` is provided.
- **Explicit consent registry:** a recipient must be listed in a local file before previewing or sending.
- **Local safety limits:** one attempt per recipient in a rolling 24-hour period, at most five attempts per rolling 24 hours, and at least 60 seconds between attempts.
- **Safer secret handling:** reads the API key from `TEXTBELT_API_KEY`, not from source code.
- **Privacy-conscious rate-limit state:** stores keyed HMAC fingerprints instead of plain phone numbers; the random HMAC key is kept in a separate local file with restrictive permissions where supported.
- **Timeouts and response validation:** bounds connect/read time, rejects redirects to avoid forwarding API credentials, and treats provider errors as errors instead of printing a false success message.
- **Testable:** unit tests run without sending SMS messages or contacting Textbelt.

The local rate limits are best-effort guardrails, not a billing or abuse-prevention guarantee. A local file lock serializes concurrent invocations on the same host. Limits are not shared across computers and may not be reliable on network filesystems. Keep both `.anon-message-state.json` and `.anon-message-fingerprint-key` intact; if state exists but the key is missing, the tool fails closed. Anyone with local access can still alter or remove local files, so do not treat these limits as a production security boundary.

## Requirements

- Python 3.10+
- Git
- A Textbelt API key for real sends
- Explicit opt-in from every recipient

## Installation

Clone the repository:

```bash
git clone https://github.com/kaandgnsh/anon-message.git
cd anon-message
```

Create and activate a virtual environment (Linux/macOS):

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Record recipient consent

Copy the example registry and edit the local copy:

```bash
cp consented_recipients.example.txt consented_recipients.txt
```

Add one E.164 phone number per line, but only after that person has explicitly agreed to receive your messages. Example format: `+12025550123`. Replace the example with a real, consented number before running your own preview.

The real registry is excluded by `.gitignore` so phone numbers are not accidentally committed. Keep any evidence of consent separately and securely; the registry itself is only a technical allowlist.

## Preview first

Run from the repository root. A preview validates the number, message, and consent registry but sends nothing:

```bash
python anon-message/m-anon-message.py \
  --phone +12025550123 \
  --message "Test notification from Kaan DGN"
```

Replace the example number with a number listed in your local consent registry. The script prints a preview and exits without making a network request.

## Send a real SMS

Set your API key in the environment. Do not hard-code it or commit it to Git.

Linux/macOS Bash or Zsh:

```bash
export TEXTBELT_API_KEY="your_api_key"
```

Fish shell:

```fish
set -x TEXTBELT_API_KEY "your_api_key"
```

Windows PowerShell:

```powershell
$env:TEXTBELT_API_KEY = "your_api_key"
```

After confirming the recipient opted in, preview the message, and verify the number and content, explicitly add `--sender` and `--send`:

```bash
python anon-message/m-anon-message.py \
  --phone +12025550123 \
  --message "Test notification from Kaan DGN. Reply STOP to opt out." \
  --sender "Kaan DGN" \
  --send
```

Replace the sample phone number and sender with accurate values for your use. The `--send` flag contacts Textbelt and may consume quota. To test the API without consuming quota, Textbelt documents adding `_test` to your API key; confirm the current behavior in its documentation before use.

## Safety and provider compliance

- **Consent is mandatory.** Textbelt requires recipients to opt in; retain consent records and honor opt-out requests.
- Identify yourself in messages. For recurring messages, include clear opt-out wording such as “Reply STOP to opt out”; Textbelt documents automatic handling of STOP replies.
- Do not send unsolicited, deceptive, harassing, bulk, or otherwise prohibited messages.
- Phone numbers and message text leave your device when you use `--send` and are processed by Textbelt. Review its [Terms of Service](https://www.textbelt.com/tos/) and [documentation](https://docs.textbelt.com/) before use.
- A successful provider response means the provider accepted the request; it does not guarantee delivery.
- This is a lightweight educational project, not a production messaging platform. It does not verify consent with the carrier, synchronize limits across machines, or replace legal/compliance review.

## Tests

Run the unit test suite:

```bash
python -m unittest discover -s tests -v
```

The suite uses mocks and temporary files. It does not send SMS messages or require an API key.

## Project structure

```text
anon-message/
├── anon-message/
│   └── m-anon-message.py
├── tests/
│   └── test_anon_message.py
├── consented_recipients.example.txt
├── requirements.txt
└── README.md
```

## Author

**Kaan DOĞAN** · [GitHub](https://github.com/kaandgnsh) · [LinkedIn](https://www.linkedin.com/in/kaan-do%C4%9Fan-911663280/) · [Instagram](https://instagram.com/kaandgn.sh)
