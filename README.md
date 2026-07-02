# B-Spy — Telegram secretary bot

A regular Telegram **bot** (bot-token, no login session, no "sitting on your
account") that connects to your account through Telegram's official
**Business Chatbots** feature (Settings → Telegram Business → Chatbots —
requires Telegram Business/Premium). Once connected, it:

- **Tracks edits** — notifies you when someone edits a message they sent
  you, showing the text *before* and *after*.
- **Tracks deletes** — notifies you when someone deletes a message they
  sent you, with the last known content (and any saved media).
- **Saves media on demand** — reply to any photo/video someone sent you
  (even a single emoji as the reply) and the bot downloads and sends it
  back to you. Nothing is saved automatically; only what you reply to.

All notifications arrive as normal messages from the bot in your own chat
with it — no separate channel needed.

## Why "reply to save" instead of automatic

The Bot API gives bots no way to tell a self-destructing/view-once photo
apart from a regular one — there's no field for it, by design (that's the
whole point of self-destruct). So automatically saving "only the expiring
ones" isn't something a bot can actually do. Auto-saving everything would
mean hoarding ordinary photos you never asked to keep. Replying to a
specific message sidesteps the problem entirely: you decide what's worth
keeping, and the bot fetches it right then - before you might otherwise
open/view it and lose it for good. If a reply fails to save something, the
bot tells you (most likely it already expired or was viewed).

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
