# anon-message

<p align="center">
  <strong>A consent-first SMS notification CLI built with Python and Textbelt.</strong>
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="https://github.com/kaandgnsh/anon-message/actions/workflows/tests.yml"><img src="https://github.com/kaandgnsh/anon-message/actions/workflows/tests.yml/badge.svg?branch=main" alt="Tests"></a>
  <a href="https://docs.textbelt.com/"><img src="https://img.shields.io/badge/SMS-Textbelt-444444?style=flat-square" alt="Textbelt API"></a>
</p>

**This tool is not anonymous.** When you explicitly send an SMS, the recipient's phone number and message are sent to Textbelt. Use it only for recipients who have explicitly opted in, identify yourself accurately, honor opt-outs, and follow applicable laws and the provider's [Terms of Service](https://textbelt.com/tos/).

## Overview

`anon-message` is a small command-line learning project for sending individual, consent-based SMS notifications. It is designed to make the sending path explicit and easy to test without contacting the provider by default.

### Features

- **Dry-run by default:** no provider request is made unless `--send` is supplied.
- **Local consent allowlist:** a recipient must be listed in `consented_recipients.txt`.
- **Conservative local rate limits:** one attempt per recipient per rolling 24 hours, no more than five attempts per rolling 24 hours, and at least 60 seconds between attempts.
- **Environment-based API key:** reads `TEXTBELT_API_KEY`; no key is stored in source code.
- **Keyed recipient fingerprints:** rate-limit state stores HMAC fingerprints rather than plain phone numbers.
- **Local concurrency lock:** serializes rate-limit reservations between simultaneous invocations on the same host.
- **Bounded HTTP behavior:** connect/read timeouts, redirects disabled, and provider response validation.
- **Automated tests:** the test suite mocks provider calls and does not send SMS messages.

> [!IMPORTANT]
> Local consent lists and rate limits are guardrails, not proof of consent or a production abuse-prevention system. Limits are not shared across computers and may not be reliable on network filesystems. Keep both `.anon-message-state.json` and `.anon-message-fingerprint-key` intact. If the state exists but its key is missing, the tool fails closed. A person with access to the machine can still alter or remove local files.

## Requirements

- Python 3.10 or newer
- Git
- A Textbelt API key for real sends
- Explicit opt-in from every recipient

## Installation

Clone the repository and install the dependency in a virtual environment:

### Linux / macOS

```bash
git clone https://github.com/kaandgnsh/anon-message.git
cd anon-message

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
git clone https://github.com/kaandgnsh/anon-message.git
cd anon-message

py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configure recipient consent

Copy the example file:

```bash
cp consented_recipients.example.txt consented_recipients.txt
```

Add one international-format phone number per line, for example `+12025550123`, **only after that person has explicitly agreed to receive your messages**. Replace the example with a real, consented recipient before running the tool.

The local registry is an allowlist; it does not collect, verify, or replace evidence of consent. Keep records of consent and opt-outs separately and securely. The real registry is ignored by Git so actual phone numbers are not accidentally committed.

## Preview a message

Run the following from the repository root. Replace the example number with one in your local consent registry:

```bash
python anon-message/m-anon-message.py \
  --phone +12025550123 \
  --message "Test notification from Kaan DGN"
```

This validates the input and displays a preview. **It does not contact Textbelt, send a message, or create rate-limit state.**

## Send an SMS

Set your API key as an environment variable. Never hard-code a real API key or commit it to Git.

### Bash / Zsh

```bash
export TEXTBELT_API_KEY="your_api_key"
```

### Fish

```fish
set -x TEXTBELT_API_KEY "your_api_key"
```

### Windows PowerShell

```powershell
$env:TEXTBELT_API_KEY = "your_api_key"
```

After verifying the recipient's consent, phone number, and message, explicitly use `--sender` and `--send`:

```bash
python anon-message/m-anon-message.py \
  --phone +12025550123 \
  --message "Test notification from Kaan DGN. Reply STOP to opt out." \
  --sender "Kaan DGN" \
  --send
```

Use your real sender identity and a number listed in your local consent registry. Supplying `--send` contacts Textbelt and may consume quota. Textbelt documents appending `_test` to an API key to test an API request without consuming text quota; see its [current documentation](https://docs.textbelt.com/).

**A successful API response means Textbelt accepted the request; it does not guarantee delivery.** An attempt is recorded before the network request, so a timeout or provider error still counts toward the local rate limits. This prevents automatic retries from accidentally sending duplicate messages; review provider status before trying again.

## Safety and privacy

- Get explicit opt-in before sending. Do not send unsolicited, deceptive, harassing, bulk, or otherwise prohibited messages.
- Identify yourself accurately. For recurring messages, include clear opt-out wording such as `Reply STOP to opt out`. See Textbelt's [compliance guidance](https://docs.textbelt.com/compliance).
- Phone numbers and message text leave your device when a real send is requested.
- The HMAC key and rate-limit state are local files. Protect them and do not delete them to bypass limits.
- The tool does not verify consent with the recipient or carrier, guarantee delivery, or replace legal/compliance review.

## Tests

Run all unit tests from the repository root:

```bash
python -m unittest discover -s tests -v
```

Tests use mocks and temporary files. They do not send SMS messages or require a real API key. GitHub Actions runs the test suite on Python 3.10, 3.11, and 3.12.

## Project structure

```text
anon-message/
├── .github/
│   └── workflows/
│       └── tests.yml
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
