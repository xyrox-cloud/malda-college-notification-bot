# Malda College Bot — Notice Monitor + Routine + Calendar

A Python (aiogram v3) Telegram bot for Malda College that:

- **Scrapes** the college notice board (`maldacollege.ac.in/home.php`) and broadcasts new notices to **every subscriber** (not a single hardcoded chat).
- **Routine** lookup via `/r` — today, a weekday, the live class, or the next upcoming class, filtered by the user's registered semester + course + subject.
- **Academic calendar** awareness — holidays (HLD), university exams (UED), special days (SPD) are surfaced automatically.
- **Admin overrides** — `/setexception` lets an admin declare an unscheduled class-off day (weather, local event) that overrides the calendar and broadcasts immediately to all subscribers.
- **Notifications toggle** — each user can turn push notifications on/off independently via `/notify`.
- **Caching layer** — all three Google Sheets sources (Odd Sem Routine, Even Sem Routine, Academic Calendar) are cached on startup with on-disk fallback, refreshable via `/refreshdata` or auto-refreshed every 24h.

---

## Commands

| Command | Who | Behavior |
|---|---|---|
| `/start` | Everyone | Subscribe + 3-step registration (semester → course → subject) |
| `/myprofile` | Everyone | Show your registration + notification status |
| `/reregister` | Everyone | Redo semester/course/subject (keeps notification preference) |
| `/notify` | Everyone | Toggle notifications ON/OFF via inline buttons |
| `/r` or `/r today` | Everyone | Today's routine (holiday/exam/exception aware) |
| `/r <day>` | Everyone | Routine for next occurrence of that weekday (mon/tue/wed/thu/fri/sat) |
| `/r now` | Everyone | Currently ongoing class |
| `/r next` | Everyone | Next upcoming class (today or next college day) |
| `/status` | Everyone | Bot uptime, last check, subscriber count, notify-ON count |
| `/latest` | Everyone | Fetch & show the most recent notice right now |
| `/ping` | Everyone | Telegram API round-trip latency |
| `/help` | Everyone | Command list (admin commands shown only to admins) |
| `/refreshdata` | Admin | Re-fetch all three cached sheets from the network |
| `/setexception` | Admin | Set an ad-hoc class-off day + broadcast to subscribers |
| `/clearexception <date>` | Admin | Remove a wrongly-set exception |
| `/listexceptions` | Admin | View upcoming exceptions (past ones auto-pruned) |

---

## Architecture

```
malda-college-bot/
├── malda_bot.py          # aiogram entry — dispatcher, startup/shutdown, scrape loop
├── config.py             # env var loading + validation
├── storage.py            # JSON + filelock (users, exceptions, seen_notices)
├── sheets.py             # Apps Script fetcher (JSON/HTML auto-detect) + cache
├── scraper.py            # notice-board scraper (aiohttp + BeautifulSoup)
├── broadcast.py          # universal subscriber broadcast (rate-limited, self-cleaning)
├── routine.py            # /r lookup logic (exception → calendar → routine)
├── handlers/
│   ├── start.py          # /start + 3-step registration FSM
│   ├── profile.py        # /myprofile, /reregister
│   ├── notify.py         # /notify inline toggle
│   ├── routine_cmd.py    # /r family
│   ├── status.py         # /status
│   ├── admin.py          # /refreshdata, /setexception, /clearexception, /listexceptions
│   └── misc.py           # /help, /ping, /latest, unknown-command fallback
├── requirements.txt
├── .env.example          # copy to .env and fill in
├── .gitignore
├── malda-bot.service     # systemd unit
└── data/                 # runtime JSON + cache (gitignored)
    ├── users.json
    ├── exceptions.json
    ├── seen_notices.json
    └── cache/
        ├── odd_routine.json
        ├── even_routine.json
        └── calendar.json
```

### Key design choices

- **aiogram v3 (async)** — needed for clean inline keyboards + FSM callbacks, and for non-blocking broadcast to many subscribers.
- **JSON + filelock** — keeps the storage simple and dependency-light. Each file has its own `FileLock` so the async scrape loop and command handlers can mutate state safely.
- **Auto-detecting sheet format** — the Apps Script web apps may return JSON or HTML; `sheets.py` parses both and normalizes to the same row shape.
- **Self-cleaning subscribers** — when a broadcast hits a 403 ("bot was blocked by the user"), that user's `notificationsEnabled` is automatically set to `false` so we don't retry forever.
- **Rate-limited broadcast** — `BROADCAST_DELAY_SEC=0.04` (~25 msg/sec) keeps us safely under Telegram's ~30 msg/sec global limit. 429 RetryAfter responses are honored.

---

## Setup — Prerequisites

- Python 3.10+ (aiogram v3 requires 3.10+)
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram user ID (for admin commands) — get it from [@userinfobot](https://t.me/userinfobot)
- The 3 Google Apps Script exec URLs (Odd Sem Routine, Even Sem Routine, Academic Calendar)

---

## Setup — Deploy on AWS EC2

```bash
# SSH into your EC2 instance
ssh -i your-key.pem ubuntu@<EC2-PUBLIC-IP>

# Create the project directory
sudo mkdir -p /opt/malda-bot
sudo chown $USER:$USER /opt/malda-bot
cd /opt/malda-bot

# Upload the project files (scp, rsync, or git clone)
#   scp -i your-key.pem -r ./malda-college-bot/* ubuntu@<EC2-PUBLIC-IP>:/opt/malda-bot/

# Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Create the environment file with your secrets
cp .env.example .env
nano .env   # fill in BOT_TOKEN, ADMIN_CHAT_ID, 3 sheet URLs

chmod 600 .env   # protect your secrets
```

### Install & enable the systemd service

```bash
sudo cp malda-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable malda-bot
sudo systemctl start malda-bot

# Check status
sudo systemctl status malda-bot

# View live logs
sudo journalctl -u malda-bot -f
```

The bot will now run **24/7** and auto-restart on reboot or crash.

---

## Setup — Deploy on Oracle Cloud VPS (Always Free)

Oracle's Always Free tier gives you an AMD or ARM (Ampere A1) instance. The setup is nearly identical to EC2; the differences are called out below.

### 1. Create the instance

- Image: **Canonical Ubuntu 22.04** (or 24.04) — recommended, so the `ubuntu` user and `/opt` paths match the EC2 flow.
- Shape: **VM.Standard.A1.Flex** (ARM, 4 OCPU / 24GB RAM free tier) or **VM.Standard.E2.1.Micro** (AMD).
- If you used **Oracle Linux** instead, the default user is `opc` and you'll need to adjust `User=` in `malda-bot.service`.

### 2. Open the egress (Oracle VCN security list + iptables)

Oracle VPS instances are locked down by default. The bot only needs **outbound** HTTPS (to `api.telegram.org`, `maldacollege.ac.in`, `script.google.com`), so no ingress ports are required. But Oracle's default iptables rules may block outbound too — verify with:

```bash
# Test outbound HTTPS
curl -sI https://api.telegram.org/  | head -1   # expect HTTP/2 404 or 200
```

If it fails, Oracle's iptables is blocking. Fix:

```bash
sudo iptables -I OUTPUT -p tcp -m multiport --dports 80,443 -j ACCEPT
# Persist across reboots
sudo netfilter-persistent save
```

Also confirm your **VCN Security List** has an egress rule allowing `0.0.0.0/0` on TCP 443 (it does by default).

### 3. Install Python + clone the bot

```bash
ssh ubuntu@<your-oracle-instance-public-ip>

sudo apt update && sudo apt install -y python3 python3-venv python3-pip
sudo mkdir -p /opt/malda-bot
sudo chown $USER:$USER /opt/malda-bot
cd /opt/malda-bot

# Upload files (same as EC2)
#   scp -r ./malda-college-bot/* ubuntu@<your-oracle-ip>:/opt/malda-bot/

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env   # fill in BOT_TOKEN, ADMIN_CHAT_ID, 3 sheet URLs
chmod 600 .env
```

### 4. systemd service

```bash
sudo cp malda-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now malda-bot
sudo systemctl status malda-bot
sudo journalctl -u malda-bot -f
```

> **ARM (Ampere) note:** all dependencies (aiogram, aiohttp, bs4, filelock) ship pure-Python or pre-built ARM wheels, so `pip install -r requirements.txt` works as-is on `aarch64`.

> **Oracle Linux / `opc` user:** edit `malda-bot.service` and change `User=ubuntu` → `User=opc`, `Group=ubuntu` → `Group=opc`. Then `sudo chown -R opc:opc /opt/malda-bot`.

---

## Setup — Run on Termux (Android phone)

Termux is great for testing the bot locally before deploying to a VPS. The challenge is that `aiogram` v3 depends on `pydantic` v2, which depends on `pydantic-core` — a **Rust-compiled** package. Termux's default Rust toolchain doesn't ship the `aarch64-unknown-linux-android` target triple, so `pip install aiogram` fails with `Target triple not supported by rustup`.

There are **three** ways to fix this. Try them in order — Path A is fastest (pre-built binaries), Path B compiles from source (slower but always works), Path C ditches Rust entirely.

### Path A — `tur-repo` (pre-built wheels, ~2 minutes) ✅ recommended

The Termux User Repository (`tur-repo`) ships pre-built binary packages for many Rust-based Python wheels, including `pydantic-core`.

```bash
# 1. Install the tur-repo (adds a new package source)
pkg install tur-repo

# 2. Update package lists
pkg update

# 3. Install Python + Rust toolchain + build deps
pkg install python rust binutils patchelf make libffi openssl

# 4. Install the Python dependencies
cd ~/malda-college-bot
pip install -r requirements.txt
```

If step 4 still fails on `pydantic-core`, force pip to use a pre-built wheel from the tur-repo:

```bash
pkg install pydantic-core
pip install --no-build-isolation pydantic
pip install aiogram aiohttp beautifulsoup4 filelock
```

### Path B — compile `pydantic-core` from source (~10–20 minutes)

If `tur-repo` is unavailable or outdated, install Rust and let pip compile `pydantic-core` natively. This is slow but reliable.

```bash
# 1. Install Rust + build tools
pkg install rust binutils patchelf make libffi openssl

# 2. Tell Rust to use the Android target triple (Termux default)
export CARGO_BUILD_TARGET="aarch64-linux-android"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="cc"

# 3. Install Python deps — pydantic-core will compile (~10 min on a phone)
cd ~/malda-college-bot
pip install -r requirements.txt
```

> **Tip:** Plug your phone in and keep Termux in the foreground during the compile — Android may kill background processes and abort the build.

### Path C — use aiogram 2.x (no Rust, pure Python) — fallback only

If neither A nor B works, you can downgrade to `aiogram==2.25.1`, which uses `pydantic` v1 (pure Python, no Rust). **However**, this requires code changes — aiogram v2 has a different API (no `DefaultBotProperties`, different middleware, different FSM). This is a significant rewrite and is **not** included in this package. If you really need it, ask and I'll port the handlers.

```bash
# Only the install command is shown — code changes are NOT included
pip install "aiogram==2.25.1" aiohttp beautifulsoup4 filelock
```

### Termux-specific notes

- **No systemd on Termux** — just run `python3 malda_bot.py` directly. To keep it alive after closing Termux, use `nohup python3 malda_bot.py &` or install `termux-services` (`pkg install termux-services`).
- **Background limits** — Android kills background processes aggressively. Use `termux-wake-lock` to prevent the bot from being killed while the screen is off:
  ```bash
  pkg install termux-api
  termux-wake-lock    # acquire
  # ... run the bot ...
  termux-wake-unlock  # release when done
  ```
- **Battery** — the bot polls the college site every `INTERVAL` seconds (default 300). On a phone, consider raising this to 600–900 to save battery.
- **Production** — Termux is fine for testing, but for 24/7 operation use the Oracle VPS (Always Free tier) setup above.

---

## Configuration (Environment Variables)

| Variable | Required | Default | Description |
|---|---|---|---|
| `BOT_TOKEN` | Yes | — | Telegram bot token from @BotFather |
| `ADMIN_CHAT_ID` | Yes | — | Numeric Telegram user ID; comma-separated for multiple admins |
| `ODD_ROUTINE_URL` | Yes | — | Apps Script exec URL for Odd Sem Routine sheet |
| `EVEN_ROUTINE_URL` | Yes | — | Apps Script exec URL for Even Sem Routine sheet |
| `CALENDAR_URL` | Yes | — | Apps Script exec URL for Academic Calendar sheet |
| `INTERVAL` | No | `300` | Notice-board poll interval (seconds; min 30) |
| `SLOT_DURATION_MIN` | No | `60` | Class slot duration for `/r now` & `/r next` |
| `SHEET_REFRESH_HOURS` | No | `24` | Auto-refresh sheet cache every N hours (0 = off) |
| `BROADCAST_DELAY_SEC` | No | `0.04` | Delay between broadcast sends (~25 msg/sec) |
| `TZ` | No | `Asia/Kolkata` | Timezone for "today"/"now" |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

---

## Migration from the old single-user bot

The original `malda_bot.py` sent notices to a single `CHAT_ID`. This version:

1. **Keeps your existing `seen_notices.json`** — copy it to `data/seen_notices.json` and the bot will skip already-seen notices.
2. **Auto-migrates `users.json`** — if you have an existing `users.json` with semester-only records, every record gets `notificationsEnabled: true` added on first load.
3. **`CHAT_ID` is no longer used** for routing notices — it's replaced by `ADMIN_CHAT_ID` for admin-only commands. Update your `.env` accordingly.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| **Termux: `Target triple not supported by rustup: aarch64-unknown-linux-android`** | `pydantic-core` can't compile. Run `pkg install tur-repo && pkg update && pkg install rust binutils patchelf` then retry `pip install -r requirements.txt`. See the "Setup — Run on Termux" section above for the full 3-path fix. |
| **Termux: `ModuleNotFoundError: No module named 'aiogram'`** | The pip install failed silently (see above). Fix the Rust/pydantic-core issue first, then `pip install -r requirements.txt` again. |
| Bot starts but `/r today` says "no data" | Run `/refreshdata` (admin). Check that `ODD_ROUTINE_URL` / `EVEN_ROUTINE_URL` are correct in `.env`. |
| `/setexception` doesn't broadcast | No subscribers have notifications ON yet. Use `/notify` to turn it on for at least one user. |
| Broadcast logs "sent to 0/N" | All subscribers have blocked the bot — they've been auto-set to `notificationsEnabled: false`. Have them `/start` the bot again. |
| `aiogram.exceptions.TelegramRetryAfter` | Normal — Telegram asked us to slow down. `BROADCAST_DELAY_SEC` will auto-increase if needed; the retry is automatic. |
| Notices not arriving | Check `sudo journalctl -u malda-bot -f` (or the Termux console) for scrape errors. The college site may have changed its HTML structure — verify `div.notice_matter` still exists. |
| Sheet fetch returns empty | Open the Apps Script URL in a browser — does it return JSON or an HTML table? `sheets.py` auto-detects both, but if the HTML structure differs from the reference PDFs the parser may miss columns. Check the log line `Sheet refresh complete: odd=N even=N calendar=N`. |
| Permission denied on `/opt/malda-bot/data/` | `sudo chown -R $USER:$USER /opt/malda-bot` and restart the service. |

---

## Running locally (for testing)

```bash
cd malda-college-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export BOT_TOKEN="your-token"
export ADMIN_CHAT_ID="your-chat-id"
export ODD_ROUTINE_URL="..."
export EVEN_ROUTINE_URL="..."
export CALENDAR_URL="..."
export INTERVAL=60   # shorter for testing

python3 malda_bot.py
```

Press `Ctrl+C` to stop.

---

## File summary

| File | Purpose |
|---|---|
| `malda_bot.py` | Entry point — aiogram dispatcher, startup, notice-scrape loop |
| `config.py` | Env var loading + validation + admin check |
| `storage.py` | JSON + filelock persistence (users, exceptions, seen) |
| `sheets.py` | Apps Script fetcher + parser + in-memory/disk cache |
| `scraper.py` | College notice-board scraper (async) |
| `broadcast.py` | Universal subscriber broadcast (rate-limited, self-cleaning) |
| `routine.py` | `/r` lookup logic (exception → calendar → routine) |
| `handlers/*.py` | One router per command group |
| `requirements.txt` | Python dependencies |
| `.env.example` | Template — copy to `.env` and fill in |
| `malda-bot.service` | systemd unit (Ubuntu user; adjustable for Oracle Linux `opc`) |
# malda-college-notification-bot

## Google Apps Script Links
Agar aapko routine/calendar ke liye Google Apps Script ka deployment link chahiye, to Telegram par mujhe DM karo: @xynqr
