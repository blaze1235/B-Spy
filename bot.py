import html
import logging
import os

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, TypeHandler

import config
import db

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("secretary")

os.makedirs(config.MEDIA_DIR, exist_ok=True)


def chat_title_of(chat):
    return chat.title or chat.username or chat.first_name or "Unknown"


def sender_name_of(user):
    if not user:
        return "Unknown"
    return user.username or " ".join(filter(None, [user.first_name, user.last_name])) or "Unknown"


def esc(text):
    return html.escape(text or "")


def media_kind_of(message):
    """Media type present on a message, without downloading anything."""
    if message.photo:
        return "photo"
    if message.video:
        return "video"
    if message.video_note:
        return "video"
    return None


def extract_file_id(message):
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.video:
        return "video", message.video.file_id
    if message.video_note:
        return "video", message.video_note.file_id
    return None, None


async def notify(context: ContextTypes.DEFAULT_TYPE, owner_chat_id, text, media_path=None):
    try:
        if media_path and os.path.exists(media_path):
            with open(media_path, "rb") as f:
                if media_path.lower().endswith((".mp4", ".mov", ".mkv")):
                    await context.bot.send_video(owner_chat_id, video=f, caption=text, parse_mode=ParseMode.HTML)
                else:
                    await context.bot.send_photo(owner_chat_id, photo=f, caption=text, parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(owner_chat_id, text, parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("Failed to notify owner")


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
    if conn.is_enabled:
        text = (
            "🤝 <b>Secretary bot connected</b>\n\n"
            "Here's what I'll do from now on:\n"
            "✏️ Tell you when someone edits a message they sent you\n"
            "🗑 Tell you when someone deletes a message they sent you\n"
            "💾 Save a photo/video if you <i>reply</i> to it - just reply with anything "
            "(even a single emoji) to the media you want kept"
        )
    else:
        text = "🔌 <b>Secretary bot disconnected.</b> I won't see your messages until you reconnect me."
    try:
        await context.bot.send_message(conn.user_chat_id, text, parse_mode=ParseMode.HTML)
    except Exception:
        log.exception("Failed to send connection confirmation")


async def save_replied_media(context: ContextTypes.DEFAULT_TYPE, conn, message):
    replied = message.reply_to_message
    if not replied or (replied.from_user and replied.from_user.id == conn["owner_user_id"]):
        return  # only save media other people sent, not the owner's own

    media_type, file_id = extract_file_id(replied)
    if not file_id:
        return  # replied to something with no photo/video, nothing to do

    sender_name = sender_name_of(replied.from_user)
    chat_title = chat_title_of(message.chat)

    try:
        tg_file = await context.bot.get_file(file_id)
        ext = ".mp4" if media_type == "video" else ".jpg"
        path = os.path.join(config.MEDIA_DIR, f"{message.chat.id}_{replied.message_id}{ext}")
        await tg_file.download_to_drive(path)
    except Exception:
        log.warning("Could not download replied-to media for message %s", replied.message_id)
        await notify(
            context,
            conn["owner_chat_id"],
            f"⚠️ Couldn't save that {esc(media_type)} from <b>{esc(sender_name)}</b> - "
            "it may have already expired or been opened.",
        )
        return

    db.upsert_message(
        connection_id=conn["connection_id"],
        chat_id=message.chat.id,
        msg_id=replied.message_id,
        sender_id=replied.from_user.id if replied.from_user else None,
        sender_name=sender_name,
        chat_title=chat_title,
        text=replied.caption or "",
        media_type=media_type,
        media_path=path,
        date=replied.date.isoformat(),
    )

    emoji = "📸" if media_type == "photo" else "🎥"
    await notify(
        context,
        conn["owner_chat_id"],
        f"{emoji} <b>Saved</b> - {esc(media_type)} from <b>{esc(sender_name)}</b> in <i>{esc(chat_title)}</i>",
        media_path=path,
    )


async def on_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    conn_id = message.business_connection_id
    conn = db.get_connection(conn_id)
    if not conn:
        return  # bot restarted and hasn't seen a business_connection update yet

    is_owner = message.from_user and message.from_user.id == conn["owner_user_id"]

    if is_owner:
        if message.reply_to_message:
            await save_replied_media(context, conn, message)
        return  # don't track the owner's own messages for edit/delete diffing

    text = message.text or message.caption or ""
    sender_name = sender_name_of(message.from_user)
    chat_title = chat_title_of(message.chat)

    db.upsert_message(
        connection_id=conn_id,
        chat_id=message.chat.id,
        msg_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_name,
        chat_title=chat_title,
        text=text,
        media_type=media_kind_of(message),
        media_path=None,
        date=message.date.isoformat(),
    )


async def on_business_message_edited(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.edited_business_message
    conn_id = message.business_connection_id
    conn = db.get_connection(conn_id)
    if not conn:
        return
    if message.from_user and message.from_user.id == conn["owner_user_id"]:
        return

    old = db.get_message(conn_id, message.chat.id, message.message_id)
    new_text = message.text or message.caption or ""
    old_text = old["text"] if old else None
    sender_name = old["sender_name"] if old else sender_name_of(message.from_user)
    chat_title = old["chat_title"] if old else chat_title_of(message.chat)

    if old_text is not None and old_text != new_text:
        db.add_edit_history(conn_id, message.chat.id, message.message_id, old_text, new_text)
        await notify(
            context,
            conn["owner_chat_id"],
            f"✏️ <b>Message edited</b> - <b>{esc(sender_name)}</b> in <i>{esc(chat_title)}</i>\n\n"
            f"<b>Before:</b>\n{esc(old_text) or '<i>(empty)</i>'}\n\n"
            f"<b>After:</b>\n{esc(new_text) or '<i>(empty)</i>'}",
        )

    db.upsert_message(
        connection_id=conn_id,
        chat_id=message.chat.id,
        msg_id=message.message_id,
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_name,
        chat_title=chat_title,
        text=new_text,
        media_type=None,
        media_path=None,
        date=message.date.isoformat(),
    )


async def on_business_messages_deleted(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deleted = update.deleted_business_messages
    conn_id = deleted.business_connection_id
    conn = db.get_connection(conn_id)
    if not conn:
        return

    for msg_id in deleted.message_ids:
        row = db.get_message(conn_id, deleted.chat.id, msg_id)
        if not row or row["deleted"]:
            continue
        db.mark_deleted(conn_id, deleted.chat.id, msg_id)
        text = (
            f"🗑 <b>Message deleted</b> - <b>{esc(row['sender_name'])}</b> in <i>{esc(row['chat_title'])}</i>\n\n"
            f"{esc(row['text']) or '<i>(no text)</i>'}"
        )
        media_path = row["media_path"] if row["media_path"] and os.path.exists(row["media_path"]) else None
        await notify(context, conn["owner_chat_id"], text, media_path=media_path)


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
        "and I'll save a photo/video whenever you reply to it.",
        parse_mode=ParseMode.HTML,
    )


def main():
    db.init_db()

    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(TypeHandler(Update, route_update))
    log.info("Secretary bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
