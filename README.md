# B-Spy — Telegram secretary bot

A regular Telegram **bot** (bot-token, no login session, no "sitting on your
account") that connects to your account through Telegram's official
**Business Chatbots** feature (Settings → Telegram Business → Chatbots —
requires Telegram Business/Premium). Once connected, it:

- **Tracks edits** — notifies you when someone edits a message they sent
  you, showing the text *before* and *after*.
- **Tracks deletes** — notifies you when someone deletes a message they
  sent you, with the last known content (and any saved media).
- **Tries to save incoming photos/videos** as they arrive, including
  "view once" ones, and tells you when it worked.

All notifications arrive as normal messages from the bot in your own chat
with it — no separate channel needed.

## Important limitation: self-destructing/view-once media

Telegram's self-destruct feature is designed so the media is only ever
readable by the client that opens it — that's the whole point of it. In
practice this Business Bot mechanism is very likely to be blocked by
Telegram from downloading that content at all (unlike edits/deletes, which
are officially exposed to bots). The bot **attempts** the download every
time and tells you plainly if it failed, so you can verify this yourself
rather than take my word for it. If reliable self-destruct capture turns
out to matter to you and this doesn't work, the only way to get it is a
real logged-in user session (a "userbot," e.g. via Telethon) — happy to
build that alongside this if you want it, just say so. Regular (non
self-destructing) photos/videos are saved reliably.

## Setup

1. **Create the bot**: message [@BotFather](https://t.me/BotFather),
   `/newbot`, follow the prompts, copy the token it gives you.

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure**:
   ```bash
   cp .env.example .env
   # paste your token into BOT_TOKEN
   ```

4. **Run it**:
   ```bash
   python bot.py
   ```
   Leave it running (pm2/systemd/screen/tmux, or any small always-on
   server). No public URL or webhook needed — it uses long polling.

5. **Connect it to your account**: in the Telegram app go to
   **Settings → Telegram Business → Chatbots**, add your bot by username,
   and turn on the permissions you want. The bot will message you to
   confirm it's connected.

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | From @BotFather |
| `DB_PATH` | SQLite file used to remember message content for edit/delete diffing |
| `MEDIA_DIR` | Local folder downloaded photos/videos are saved to |

## Files

- `bot.py` — the bot: handlers for business connection/new/edited/deleted messages
- `db.py` — local SQLite cache of message content + edit history
- `config.py` — loads settings from `.env`
