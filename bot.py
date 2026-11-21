import html
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, TypeHandler

import config
import db

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("secretary")

os.makedirs(config.MEDIA_DIR, exist_ok=True)

MEDIA_EMOJI = {
    "photo": "📸",
    "video": "🎥",
    "video note": "🎥",
    "voice": "🎤",
    "audio": "🎵",
    "document": "📄",
}


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        pass  # don't spam the bot's logs with health-check hits


class _HealthServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


def start_health_server():
    """Minimal stdlib-only HTTP server so Railway has something to see on
    $PORT. No Flask/waitress here on purpose - this must never be the thing
    that breaks."""
    server = _HealthServer(("0.0.0.0", config.PORT), _HealthHandler)
    server.serve_forever()


def chat_title_of(chat):
    return chat.title or chat.username or chat.first_name or "Unknown"


def sender_name_of(user):
    if not user:
        return "Unknown"
    return user.username or " ".join(filter(None, [user.first_name, user.last_name])) or "Unknown"


def sender_username_of(user):
    return user.username if user else None


def esc(text):
    return html.escape(text or "")


def build_user_link(user_id, username, display_name):
    """A clickable link to a Telegram account. Prefers the public
    https://t.me/<username> link (works for anyone, in-app or on the web).
    Falls back to a tg://user?id=... deep link when there's no username -
    that only resolves inside Telegram itself, and Telegram may still refuse
    to open it depending on the account's privacy settings; that's a
    Telegram-side limit, not something a bot can override."""
    name = esc(display_name or "Unknown")
    if username:
        url = f"https://t.me/{username}"
    elif user_id:
        url = f"tg://user?id={user_id}"
    else:
        return name
    return f'<a href="{url}">{name}</a>'


def user_link(user):
    if not user:
        return "Unknown"
    return build_user_link(user.id, user.username, sender_name_of(user))


def extract_file_id(message):
    """(media_type, file_id) for the richest media on a message, or (None, None)."""
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.video:
        return "video", message.video.file_id
    if message.video_note:
        return "video note", message.video_note.file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.document:
        return "document", message.document.file_id
    return None, None


def guess_ext(media_type, message):
    if media_type == "photo":
        return ".jpg"
    if media_type in ("video", "video note"):
        return ".mp4"
    if media_type == "voice":
        return ".ogg"
    if media_type == "audio":
        name = message.audio.file_name if message.audio else None
        return os.path.splitext(name)[1] if name else ".mp3"
    if media_type == "document":
        name = message.document.file_name if message.document else None
        return os.path.splitext(name)[1] if name else ".bin"
    return ""


async def try_download_media(context: ContextTypes.DEFAULT_TYPE, message, chat_id):
    """Best-effort silent download of any media on a message. Returns
    (media_type, media_path) - media_path is None if there was no media, or
    if the download failed (e.g. a self-destructing photo already viewed)."""
    media_type, file_id = extract_file_id(message)
    if not file_id:
        return media_type, None
    try:
        tg_file = await context.bot.get_file(file_id)
        ext = guess_ext(media_type, message)
        path = os.path.join(config.MEDIA_DIR, f"{chat_id}_{message.message_id}{ext}")
        await tg_file.download_to_drive(path)
        return media_type, path
    except Exception:
        log.warning("Could not download %s for message %s", media_type, message.message_id)
        return media_type, None


async def notify(context: ContextTypes.DEFAULT_TYPE, owner_chat_id, text, media_path=None, media_type=None):
    try:
        if media_path and os.path.exists(media_path):
            with open(media_path, "rb") as f:
                if media_type == "photo":
                    await context.bot.send_photo(owner_chat_id, photo=f, caption=text, parse_mode=ParseMode.HTML)
                elif media_type in ("video", "video note"):
                    await context.bot.send_video(owner_chat_id, video=f, caption=text, parse_mode=ParseMode.HTML)
                elif media_type == "voice":
                    await context.bot.send_voice(owner_chat_id, voice=f, caption=text, parse_mode=ParseMode.HTML)
                elif media_type == "audio":
                    await context.bot.send_audio(owner_chat_id, audio=f, caption=text, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_document(owner_chat_id, document=f, caption=text, parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(owner_chat_id, text, parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("Failed to notify owner")


async def active_connection(context: ContextTypes.DEFAULT_TYPE, conn_id):
    """The connection row if its owner isn't blocked, else None.

    If we've never seen this connection (e.g. the database was wiped by a
    redeploy), ask Telegram for it instead of silently ignoring the user
    until they reconnect the bot."""
    conn = db.get_connection(conn_id)
    if not conn:
        try:
            bc = await context.bot.get_business_connection(conn_id)
        except Exception:
            log.exception("Could not fetch business connection %s", conn_id)
            return None
        db.upsert_connection(
            bc.id,
            bc.user.id,
            bc.user_chat_id,
            bc.is_enabled,
            owner_username=bc.user.username,
            owner_first_name=bc.user.first_name,
        )
        log.info("Recovered unknown business connection for user %s", bc.user.id)
        conn = db.get_connection(conn_id)
    if not conn or conn["blocked"]:
        return None
    return conn


async def on_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    db.upsert_connection(
        conn.id,
        conn.user.id,
        conn.user_chat_id,
        conn.is_enabled,
        owner_username=conn.user.username,
        owner_first_name=conn.user.first_name,
    )
    if db.is_user_blocked(conn.user.id):
        return  # recorded so the admin can see them, but they get nothing

    if conn.is_enabled:
        text = (
            "🤝 <b>Secretary bot connected</b>\n\n"
            "Here's what I'll do from now on:\n"
            "✏️ Tell you when someone edits a message they sent you, with a link to their account\n"
            "🗑 Tell you when someone deletes a message they sent you, with a link to their account - "
            "including the photo/video/voice/audio if I managed to save it\n"
            "💾 Photos, videos and voice/audio messages are saved automatically as they arrive, "
            "so they're recoverable if deleted later"
        )
    else:
        text = "🔌 <b>Secretary bot disconnected.</b> I won't see your messages until you reconnect me."
    try:
        await context.bot.send_message(conn.user_chat_id, text, parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("Failed to send connection confirmation")


async def save_replied_media(context: ContextTypes.DEFAULT_TYPE, conn, message):
    """Manual fallback: reply to a message to (re)try saving its media,
    e.g. if the automatic save on arrival failed for some reason."""
    replied = message.reply_to_message
    if not replied or (replied.from_user and replied.from_user.id == conn["owner_user_id"]):
        return  # only save media other people sent, not the owner's own

    media_type, media_path = await try_download_media(context, replied, message.chat.id)
    if not media_type:
        return  # replied to something with no media, nothing to do

    link = user_link(replied.from_user)
    chat_title = chat_title_of(message.chat)

    if not media_path:
        await notify(
            context,
            conn["owner_chat_id"],
            f"⚠️ Couldn't save that {esc(media_type)} from {link} - "
            "it may have already expired or been opened.",
        )
        return

    db.upsert_message(
        connection_id=conn["connection_id"],
        chat_id=message.chat.id,
        msg_id=replied.message_id,
        sender_id=replied.from_user.id if replied.from_user else None,
        sender_name=sender_name_of(replied.from_user),
        sender_username=sender_username_of(replied.from_user),
        chat_title=chat_title,
        text=replied.caption or "",
        media_type=media_type,
        media_path=media_path,
        date=replied.date.isoformat(),
    )

    emoji = MEDIA_EMOJI.get(media_type, "💾")
    await notify(
        context,
        conn["owner_chat_id"],
        f"{emoji} <b>Saved</b> - {esc(media_type)} from {link} in <i>{esc(chat_title)}</i>",
        media_path=media_path,
        media_type=media_type,
    )


async def on_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    conn_id = message.business_connection_id
    conn = await active_connection(context, conn_id)
    if not conn:
        return  # unknown connection, or owner is blocked

    is_owner = message.from_user and message.from_user.id == conn["owner_user_id"]

    if is_owner:
        if message.reply_to_message:
            await save_replied_media(context, conn, message)
        return  # don't track the owner's own messages for edit/delete diffing

    text = message.text or message.caption or ""
    chat_title = chat_title_of(message.chat)

    # Grab photos/videos/voice/audio the moment they arrive, silently, so
    # they're recoverable later if the message gets deleted. Best-effort:
    # if it's already gone (e.g. a self-destructing photo) this just fails
    # quietly and the delete notification will say what it was, unsaved.
    media_type, media_path = await try_download_media(context, message, message.chat.id)

    db.upsert_message(
        connection_id=conn_id,
        chat_id=message.chat.id,
        msg_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_name_of(message.from_user),
        sender_username=sender_username_of(message.from_user),
        chat_title=chat_title,
        text=text,
        media_type=media_type,
        media_path=media_path,
        date=message.date.isoformat(),
    )


async def on_business_message_edited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.edited_business_message
    conn_id = message.business_connection_id
    conn = await active_connection(context, conn_id)
    if not conn:
        return
    if message.from_user and message.from_user.id == conn["owner_user_id"]:
        return

    old = db.get_message(conn_id, message.chat.id, message.message_id)
    new_text = message.text or message.caption or ""
    old_text = old["text"] if old else None
    chat_title = old["chat_title"] if old else chat_title_of(message.chat)
    link = user_link(message.from_user)

    if old_text is not None and old_text != new_text:
        db.add_edit_history(conn_id, message.chat.id, message.message_id, old_text, new_text)
        await notify(
            context,
            conn["owner_chat_id"],
            f"✏️ <b>Message edited</b> - {link} in <i>{esc(chat_title)}</i>\n\n"
            f"<b>Before:</b>\n{esc(old_text) or '<i>(empty)</i>'}\n\n"
            f"<b>After:</b>\n{esc(new_text) or '<i>(empty)</i>'}",
        )

    db.upsert_message(
        connection_id=conn_id,
        chat_id=message.chat.id,
        msg_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_name_of(message.from_user),
        sender_username=sender_username_of(message.from_user),
        chat_title=chat_title,
        text=new_text,
        media_type=old["media_type"] if old else None,
        media_path=old["media_path"] if old else None,
        date=message.date.isoformat(),
    )


async def on_business_messages_deleted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deleted = update.deleted_business_messages
    conn_id = deleted.business_connection_id
    conn = await active_connection(context, conn_id)
    if not conn:
        return

    for msg_id in deleted.message_ids:
        row = db.get_message(conn_id, deleted.chat.id, msg_id)
        if not row or row["deleted"]:
            continue
        db.mark_deleted(conn_id, deleted.chat.id, msg_id)
        link = build_user_link(row["sender_id"], row.get("sender_username"), row["sender_name"])

        media_path = row["media_path"] if row["media_path"] and os.path.exists(row["media_path"]) else None
        if row["text"]:
            body = esc(row["text"])
        elif row["media_type"]:
            saved_note = "" if media_path else " - couldn't save it in time"
            body = f"<i>[{esc(row['media_type'])}{saved_note}]</i>"
        else:
            body = "<i>(no text)</i>"

        text = f"🗑 <b>Message deleted</b> - {link} in <i>{esc(row['chat_title'])}</i>\n\n{body}"
        await notify(context, conn["owner_chat_id"], text, media_path=media_path, media_type=row["media_type"])


async def route_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.business_connection:
        await on_business_connection(update, context)
    elif update.business_message:
        await on_business_message(update, context)
    elif update.edited_business_message:
        await on_business_message_edited(update, context)
    elif update.deleted_business_messages:
        await on_business_messages_deleted(update, context)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 <b>Hi, I'm your secretary bot</b>\n\n"
        "1. Open Telegram Settings → Telegram Business → Chatbots "
        "(requires Telegram Business/Premium)\n"
        "2. Add this bot and turn on the permissions you want\n\n"
        "Then I'll message you here whenever someone edits or deletes a message they sent you, "
        "with a link to their account. Photos, videos and voice/audio messages are saved "
        "automatically as they arrive, so they're recoverable if deleted later.",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------- admin ----

def is_admin(user):
    return bool(user) and user.id in config.ADMIN_TELEGRAM_IDS


def admin_display_name(row):
    if row["owner_username"]:
        return f"@{row['owner_username']}"
    return row["owner_first_name"] or f"id {row['owner_user_id']}"


def admin_status(row):
    if row["blocked"]:
        return "🚫", "restricted"
    if row["is_enabled"]:
        return "✅", "active"
    return "⏸", "disconnected"


def admin_list_view():
    rows = db.list_connections_with_stats()
    totals = db.get_totals()
    text = (
        "👑 <b>Admin panel</b>\n\n"
        f"👥 Users: <b>{totals['total_users']}</b> (active {totals['active_users']})\n"
        f"💬 Messages tracked: {totals['total_messages']}\n"
        f"✏️ Edits: {totals['total_edits']} · 🗑 Deletes: {totals['total_deletes']} · "
        f"💾 Media: {totals['total_media']}\n\n"
    )
    if not rows:
        text += "No one has connected the bot yet."
        return text, InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="al")]])

    text += "Tap a user to manage them:"
    buttons = []
    for row in rows:
        emoji, _ = admin_status(row)
        label = f"{emoji} {admin_display_name(row)} · {row['message_count']} msgs"
        buttons.append([InlineKeyboardButton(label, callback_data=f"av:{row['rid']}")])
    buttons.append([InlineKeyboardButton("🔄 Refresh", callback_data="al")])
    return text, InlineKeyboardMarkup(buttons)


def admin_detail_view(row):
    emoji, status = admin_status(row)
    profile = build_user_link(row["owner_user_id"], row["owner_username"], admin_display_name(row))
    text = (
        f"👤 {profile}\n"
        f"🆔 <code>{row['owner_user_id']}</code>\n"
        f"Status: {emoji} {status}\n"
        f"Connected since: {esc((row['first_connected_at'] or '-')[:10])}\n\n"
        f"💬 Messages tracked: {row['message_count']}\n"
        f"✏️ Edits caught: {row['edit_count']}\n"
        f"🗑 Deletes caught: {row['delete_count']}\n"
        f"💾 Media saved: {row['media_count']}"
    )
    rid = row["rid"]
    toggle = (
        InlineKeyboardButton("✅ Unrestrict", callback_data=f"at:{rid}")
        if row["blocked"]
        else InlineKeyboardButton("🚫 Restrict", callback_data=f"at:{rid}")
    )
    keyboard = InlineKeyboardMarkup([
        [toggle],
        [InlineKeyboardButton("🗑 Remove", callback_data=f"ar:{rid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="al")],
    ])
    return text, keyboard


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user):
        return
    text, keyboard = admin_list_view()
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user):
        await query.answer()
        return

    data = query.data or ""
    action, _, arg = data.partition(":")

    if action == "al":
        text, keyboard = admin_list_view()
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await query.answer()
        return

    row = db.get_connection_with_stats(int(arg)) if arg.isdigit() else None
    if not row:
        await query.answer("That user no longer exists.", show_alert=True)
        text, keyboard = admin_list_view()
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        return

    if action == "av":
        text, keyboard = admin_detail_view(row)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await query.answer()

    elif action == "at":
        db.set_user_blocked(row["owner_user_id"], not row["blocked"])
        row = db.get_connection_with_stats(row["rid"])
        text, keyboard = admin_detail_view(row)
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await query.answer("Restricted" if row["blocked"] else "Unrestricted")

    elif action == "ar":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚠️ Yes, remove", callback_data=f"arc:{row['rid']}")],
            [InlineKeyboardButton("Cancel", callback_data=f"av:{row['rid']}")],
        ])
        await query.edit_message_text(
            f"Remove <b>{esc(admin_display_name(row))}</b> and delete all their stored messages and media?\n\n"
            "This can't be undone. If they reconnect the bot later they'll start fresh - "
            "use <b>Restrict</b> instead if you want to keep them out.",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        await query.answer()

    elif action == "arc":
        db.delete_connection(row["connection_id"])
        text, keyboard = admin_list_view()
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await query.answer("Removed")

    else:
        await query.answer()


def main():
    db.init_db()

    threading.Thread(target=start_health_server, daemon=True).start()
    log.info("Health endpoint listening on port %s", config.PORT)

    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CallbackQueryHandler(admin_callback))
    # Must stay last: it matches every update, and only the first matching
    # handler in a group runs.
    app.add_handler(TypeHandler(Update, route_update))
    log.info("Secretary bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
