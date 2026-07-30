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

The bot is multi-tenant: anyone can connect it to their own Telegram
account via Business Chatbots, and each connection is tracked separately
(its own message cache, its own plan). The admin panel below is how you
see and manage all of them.

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

## Admin panel

A basic-auth protected dashboard at `/admin` (runs on the same process,
port `PORT`) showing every connected user, their status, when they
connected, usage counts (messages tracked, edits/deletes caught, media
saved), and their plan. You can set a user's plan (`free`/`pro`/
`unlimited`) from a dropdown right in the table - it's just a label stored
against their connection for now; no billing or enforcement is wired up
yet (see the monetization notes below for that).

Log in with `ADMIN_USER` / `ADMIN_PASSWORD` (set these in `.env` /
Railway variables - `ADMIN_PASSWORD` is required, the app won't start
without it).

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
   # paste your token into BOT_TOKEN, and set ADMIN_PASSWORD
   ```

4. **Run it**:
   ```bash
   python bot.py
   ```
   This starts both the Telegram bot (long polling - no public URL needed
   for that part) and the admin panel web server on `PORT`. Leave it
   running (pm2/systemd/screen/tmux, or any small always-on server).

5. **Connect it to your account**: in the Telegram app go to
   **Settings → Telegram Business → Chatbots**, add your bot by username,
   and turn on the permissions you want. The bot will message you to
   confirm it's connected, and it'll show up in the admin panel.

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | From @BotFather |
| `DB_PATH` | SQLite file used to remember message content for edit/delete diffing |
| `MEDIA_DIR` | Local folder downloaded photos/videos are saved to |
| `ADMIN_USER` | Admin panel login username (default `admin`) |
| `ADMIN_PASSWORD` | Admin panel login password - required |
| `PORT` | Port the admin panel listens on (Railway sets this automatically) |

## Files

- `bot.py` — the bot: handlers for business connection/new/edited/deleted messages
- `admin.py` — the admin panel (Flask): dashboard + plan editing
- `templates/admin.html` — admin dashboard page
- `db.py` — local SQLite cache of message content, edit history, connections/plans
- `config.py` — loads settings from `.env`
