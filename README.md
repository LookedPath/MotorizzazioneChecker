# Motorizzazione Verona slot checker

Small Python script that polls the Microsoft Bookings availability endpoint for Motorizzazione Verona and sends a Telegram message when free appointment slots appear or when the API response model changes.

It is designed to run from `cron` every minute on a Linux machine.

## Features

- No third-party Python packages
- Reads Telegram credentials from `.env`
- Supports one or many Telegram chat IDs
- Ignores `.env` in git
- Stores the last seen slot count in `.state/` so cron does not spam Telegram every minute
- Stores the last seen API response model fingerprint so model changes can trigger alerts
- Lets you override the request window and endpoint via environment variables

## Repository contents

- `check_slots.py`: monitor script
- `.env.example`: example configuration
- `.gitignore`: ignores `.env` and local state

## Requirements

- Linux machine with `python3`
- Internet access to:
  - `bookings.cloud.microsoft`
  - `api.telegram.org`
- A Telegram bot token
- One Telegram chat ID, or multiple chat IDs separated by commas in the same variable

## 1. Download the project

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/MotorizzazioneChecker.git
cd MotorizzazioneChecker
```

Or download the ZIP from GitHub and extract it.

## 2. Create the Telegram bot

1. Open Telegram and message `@BotFather`.
2. Run `/newbot`.
3. Choose a name and username.
4. Copy the bot token.

## 3. Get your chat ID

Send at least one message to your bot, then open this URL in the browser, replacing `<TOKEN>`:

```text
https://api.telegram.org/bot<TOKEN>/getUpdates
```

Look for `"chat":{"id": ...}` in the JSON response and copy that numeric ID.

If you want notifications in a group:

1. Add the bot to the group.
2. Send a message in the group.
3. Call `getUpdates` again and use the group chat ID.

## 4. Configure `.env`

Create the local config file from the example:

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```dotenv
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

If you want to notify multiple chats, keep the same variable name and separate IDs with commas:

```dotenv
TELEGRAM_CHAT_ID=first_chat_id,second_chat_id
```

Optional settings:

```dotenv
# Default request target
BOOKING_URL=https://bookings.cloud.microsoft/BookingsService/api/V1/bookingBusinessesc2/MotorizzazioneVerona@mitgov.onmicrosoft.com/GetStaffAvailability

# Captured identifiers from the website
SERVICE_ID=bf3d1cb6-95d3-4996-a1f7-3d9ba808c594
STAFF_IDS=ee78c5a6-5146-43a1-b8ac-3508836445f2

# Microsoft time zone name used by the request
REQUEST_TIME_ZONE=W. Europe Standard Time

# Local state path used to suppress duplicate alerts
STATE_FILE=.state/availability_state.json
```

## 5. Test it manually

Run:

```bash
python3 check_slots.py
```

To test Telegram notifications without waiting for a real slot change:

```bash
python3 check_slots.py --test-notification
```

Expected behavior:

- If no slots are available, it exits successfully and prints JSON like:

```json
{"checkedAt": "2026-05-28T12:34:56+02:00", "windowStart": "2026-05-01T00:00:00", "windowEnd": "2026-08-01T00:00:00", "modelChanged": false, "responseModelFingerprint": "...", "slotCount": 0, "notified": false}
```

- If slots are available and the previous run had zero slots, it sends a Telegram message.
- If slots are still available on the next run, it will not send another alert.
- If the API response model changes compared to the last saved response model, it sends a Telegram message even if no slots were extracted.
- With `--test-notification`, it sends a test Telegram message immediately and exits.

The local state is stored in `.state/availability_state.json`.

## 6. Install it on a Linux server

Example directory:

```bash
mkdir -p "$HOME/apps"
cd "$HOME/apps"
git clone https://github.com/YOUR_USERNAME/MotorizzazioneChecker.git
cd MotorizzazioneChecker
cp .env.example .env
chmod +x check_slots.py
```

Then edit `.env` with your Telegram values.

## 7. Add the cron job

Open your crontab:

```bash
crontab -e
```

Add this line, updating the path:

```cron
* * * * * cd /home/YOUR_USER/apps/MotorizzazioneChecker && /usr/bin/env python3 check_slots.py >> cron.log 2>&1
```

This runs the checker every minute and appends output to `cron.log`.

## 8. Check logs

Useful commands:

```bash
tail -f cron.log
tail -f /var/log/syslog
```

On some distros cron logs go to a different file or to `journalctl`.

Each cron log line now includes `checkedAt`, so you can see the date and time of each run directly in the JSON output.

The booking request always checks whole calendar months: the current month plus the next two full months. For example, any run in May checks from May 1 through August 1, covering all of May, June, and July.

## How duplicate alerts are avoided

Each run extracts the currently available slots from the Microsoft Bookings response and stores the latest slot count.

- If there are no slots, no slot-availability Telegram message is sent.
- If slots exist and the previous run had zero slots, a Telegram message is sent.
- If slots remain available, the script stays silent even if the exact times change.
- Once the count goes back to zero, the next nonzero availability will trigger a new alert.

The script also fingerprints the response model itself:

- The first saved response model becomes the local baseline.
- If the model changes on a later run, a Telegram message is sent.
- This helps catch cases where Microsoft changes the response shape, including possible availability responses that look different from the current "no availability" payload.

If you want to force a new alert while slots are still available, delete the state file:

```bash
rm -f .state/availability_state.json
```

## Notes

- The script uses the endpoint and IDs you captured from Chrome.
- The live endpoint currently returns `staffAvailabilityResponse[].availabilityItems[]` with statuses such as `BOOKINGSAVAILABILITYSTATUS_OUT_OF_OFFICE`. The checker only alerts on `AVAILABLE` or `SLOTS_AVAILABLE` style statuses.
- Microsoft could change the API response shape or add anti-bot protections in the future. If that happens, the script now alerts on the model change, but the extraction logic may still need a small update.
- The current detection is intentionally generic so it can still work if response field names vary slightly.
- Telegram alerts now include the official booking link so you can book faster after an alert.

## License

Add your preferred license before publishing the repository.
