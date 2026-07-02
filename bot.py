import logging
import os

from telegram import Update
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


async def notify(context: ContextTypes.DEFAULT_TYPE, owner_chat_id, text, media_path=None):
    try:
        if media_path and os.path.exists(media_path):
            with open(media_path, "rb") as f:
                if media_path.lower().endswith((".mp4", ".mov", ".mkv")):
                    await context.bot.send_video(owner_chat_id, video=f, caption=text)
                else:
                    await context.bot.send_photo(owner_chat_id, photo=f, caption=text)
        else:
            await context.bot.send_message(owner_chat_id, text)
    except Exception:
        log.exception("Failed to notify owner")


async def on_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = update.business_connection
    db.upsert_connection(conn.id, conn.user.id, conn.user_chat_id, conn.is_enabled)
    status = "connected and active" if conn.is_enabled else "disconnected"
    try:
        await context.bot.send_message(
            conn.user_chat_id,
            f"Secretary bot {status}.\n"
            "I'll message you here when someone edits or deletes a message they sent you, "
            "and try to save photos/videos as they arrive.",
        )
    except Exception:
        log.exception("Failed to send connection confirmation")


async def download_media(context: ContextTypes.DEFAULT_TYPE, message):
    file_id = None
    media_type = None
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = "photo"
    elif message.video:
        file_id = message.video.file_id
        media_type = "video"
    elif message.video_note:
        file_id = message.video_note.file_id
        media_type = "video"

    if not file_id:
        return None, None

    try:
        tg_file = await context.bot.get_file(file_id)
        ext = ".mp4" if media_type == "video" else ".jpg"
        path = os.path.join(config.MEDIA_DIR, f"{message.chat.id}_{message.message_id}{ext}")
        await tg_file.download_to_drive(path)
        return media_type, path
    except Exception:
        # Telegram most likely refused this because it's a self-destructing /
        # view-once photo or video - bots (even connected Business bots)
        # generally can't fetch that content, by design.
        log.warning("Could not download media for message %s in chat %s", message.message_id, message.chat.id)
        return media_type, None


async def on_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.business_message
    conn_id = message.business_connection_id
    conn = db.get_connection(conn_id)
    if not conn:
        return  # bot restarted and hasn't seen a business_connection update yet

    if message.from_user and message.from_user.id == conn["owner_user_id"]:
        return  # the owner's own outgoing message, not something to track

    media_type, media_path = await download_media(context, message)
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
        media_type=media_type,
        media_path=media_path,
        date=message.date.isoformat(),
    )

    if media_type and media_path:
        await notify(context, conn["owner_chat_id"], f"Saved {media_type} from {sender_name}", media_path=media_path)
    elif media_type and not media_path:
        await notify(
            context,
            conn["owner_chat_id"],
            f"{sender_name} sent a {media_type} I couldn't download "
            "(likely self-destructing/view-once - Telegram tends to block bots from fetching those).",
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
            f"Message edited by {sender_name} in {chat_title}\n\n"
            f"Before:\n{old_text or '(empty)'}\n\n"
            f"After:\n{new_text or '(empty)'}",
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
        text = f"Message deleted by {row['sender_name']} in {row['chat_title']}\n\n{row['text'] or '(no text)'}"
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
        "Hi! To use me as your secretary bot:\n"
        "1. Open Telegram Settings -> Telegram Business -> Chatbots (requires Telegram Business/Premium)\n"
        "2. Add this bot and turn on the permissions you want\n"
        "3. I'll message you here whenever someone edits or deletes a message they sent you, "
        "and try to grab photos/videos as they arrive."
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
