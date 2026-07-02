# B-Spy — Telegram message tracker

A personal userbot for **your own** Telegram account (uses the Telegram
client API via [Telethon](https://docs.telethon.dev), not the Bot API —
same idea as tools like "NotifyMe"/"Chat Automation" trackers). It logs in
as you, in the background, and:

- **Edit tracking** — when someone edits a message they sent you, you get a
  notification showing the text *before* and *after* the edit.
- **Delete tracking** — when someone deletes a message they sent you, you
  get a notification with the last known content (text and/or media) of
  what they deleted.
- **Self-destructing media auto-save** — any "view once" / self-destructing
  photo or video someone sends you is downloaded automatically the instant
  it arrives (before it can be opened and wiped by Telegram's servers), and
  forwarded to you.

All notifications go to your **Saved Messages** by default.

## How it works / limits

- It's a second, always-running login session on your account (via a
  generated *session string*, not a bot token). Treat that string like a
  password — anyone who has it can read your whole account.
- Self-destructing media can only be captured **before it's opened**. This
  bot downloads it automatically the moment the message arrives, which is
  more reliable than trying to "catch it" by replying — once you or anyone
  opens/views it, Telegram deletes it server-side and *nothing* can recover
  it after that, bot or not.
- Telegram's delete-message update doesn't include which chat a deleted
  message came from for private chats / small groups (an API limitation,
  not a bug here) — only the message ID. The bot matches deletes against a
  local cache of recent messages to work around this; it's reliable in
  normal day-to-day use but can rarely misattribute if the same message ID
  briefly exists in two different chats.
- This only tracks **incoming** messages (things sent *to* you), which
  matches "notify me when someone edits/deletes their message to me."
- Using a self-managed session like this is against Telegram's Terms of
  Service in some interpretations (userbots/automation on a personal
  account). Use at your own risk/discretion.

## Setup

1. **Get API credentials**: go to <https://my.telegram.org> → *API
   Development Tools* → create an app → note the `api_id` and `api_hash`.

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure**:
   ```bash
   cp .env.example .env
   # fill in API_ID and API_HASH in .env
   ```

4. **Log in once** to generate a session string:
   ```bash
   python login.py
   ```
   Enter your phone number, the code Telegram sends you, and your 2FA
   password if you have one. Copy the printed session string into `.env`
   as `SESSION_STRING`.

5. **Run it**:
   ```bash
   python main.py
   ```
   Leave it running (e.g. under `pm2`, `systemd`, `screen`/`tmux`, or a
   small always-on VPS/Raspberry Pi) so it keeps watching in the
   background.

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `API_ID` / `API_HASH` | From my.telegram.org |
| `SESSION_STRING` | From `login.py` |
| `NOTIFY_CHAT` | Where notifications/saved media are sent. `me` = Saved Messages (default). Can also be a chat/channel id. |
| `MEDIA_DIR` | Local folder saved media is downloaded to |
| `DB_PATH` | SQLite file used to remember message content for edit/delete diffing |

## Files

- `main.py` — the bot: event handlers for new/edited/deleted messages
- `db.py` — local SQLite cache of message content + edit history
- `login.py` — one-time interactive login to produce a session string
- `config.py` — loads settings from `.env`
