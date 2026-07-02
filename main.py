import asyncio
import logging
import os
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

import config
import db

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("spybot")

os.makedirs(config.MEDIA_DIR, exist_ok=True)

client = TelegramClient(StringSession(config.SESSION_STRING), config.API_ID, config.API_HASH)


async def notify(text=None, file=None):
    await client.send_message(config.NOTIFY_CHAT, text, file=file, parse_mode="markdown", link_preview=False)


async def get_names(event):
    chat = await event.get_chat()
    sender = await event.get_sender()
    chat_title = getattr(chat, "title", None) or getattr(chat, "username", None) or getattr(chat, "first_name", "Unknown")
    sender_name = None
    if sender:
        sender_name = getattr(sender, "username", None) or " ".join(
            filter(None, [getattr(sender, "first_name", None), getattr(sender, "last_name", None)])
        )
    return chat_title, sender_name or "Unknown"


def media_kind(message):
    """Returns (kind, ttl_seconds) for a message's media, or (None, None)."""
    if not message.media:
        return None, None
    if isinstance(message.media, MessageMediaPhoto):
        return "photo", getattr(message.media, "ttl_seconds", None)
    if isinstance(message.media, MessageMediaDocument) and message.media.document:
        mime = getattr(message.media.document, "mime_type", "") or ""
        kind = "video" if mime.startswith("video") else "document"
        return kind, getattr(message.media, "ttl_seconds", None)
    return "media", None


@client.on(events.NewMessage(incoming=True))
async def on_new_message(event):
    message = event.message
    chat_id = event.chat_id
    chat_title, sender_name = await get_names(event)

    media_type, ttl = media_kind(message)
    media_path = None

    if media_type in ("photo", "video") and ttl:
        # Self-destructing photo/video: grab it the moment it arrives, before
        # anyone opens it in a real client and Telegram wipes it server-side.
        try:
            filename = f"{chat_id}_{message.id}_{int(datetime.now().timestamp())}"
            media_path = await client.download_media(message, file=os.path.join(config.MEDIA_DIR, filename))
            log.info("Saved self-destructing %s from %s -> %s", media_type, sender_name, media_path)
            await notify(
                f"**Self-destructing {media_type} saved**\n"
                f"From: **{sender_name}** in *{chat_title}*\n"
                f"TTL: {ttl}s",
                file=media_path,
            )
        except Exception:
            log.exception("Failed to auto-save self-destructing media")

    db.upsert_message(
        chat_id=chat_id,
        msg_id=message.id,
        sender_id=event.sender_id,
        chat_title=chat_title,
        sender_name=sender_name,
        text=message.raw_text,
        media_type=media_type,
        media_path=media_path,
        date=datetime.now(timezone.utc).isoformat(),
    )


@client.on(events.MessageEdited(incoming=True))
async def on_message_edited(event):
    message = event.message
    chat_id = event.chat_id
    old = db.get_message(chat_id, message.id)
    new_text = message.raw_text or ""
    old_text = old["text"] if old else None

    chat_title, sender_name = await get_names(event)

    if old_text is not None and old_text != new_text:
        db.add_edit_history(chat_id, message.id, old_text, new_text)
        await notify(
            f"**Message edited**\n"
            f"From: **{sender_name}** in *{chat_title}*\n\n"
            f"**Before:**\n{old_text or '_(empty)_'}\n\n"
            f"**After:**\n{new_text or '_(empty)_'}"
        )

    db.upsert_message(
        chat_id=chat_id,
        msg_id=message.id,
        sender_id=event.sender_id,
        chat_title=chat_title,
        sender_name=sender_name,
        text=new_text,
        media_type=None,
        media_path=None,
        date=datetime.now(timezone.utc).isoformat(),
    )


@client.on(events.MessageDeleted())
async def on_message_deleted(event):
    # Telegram's delete-message update doesn't carry the chat id for private
    # chats / small groups - only message id(s). We fall back to matching
    # against our local cache when the chat id isn't given to us.
    chat_id = event.chat_id
    for msg_id in event.deleted_ids:
        candidates = [db.get_message(chat_id, msg_id)] if chat_id else db.find_by_msgid_any_chat(msg_id)
        candidates = [c for c in candidates if c]
        for row in candidates:
            if row["deleted"]:
                continue
            db.mark_deleted(row["chat_id"], row["msg_id"])
            text = (
                f"**Message deleted**\n"
                f"From: **{row['sender_name']}** in *{row['chat_title']}*\n\n"
                f"{row['text'] or '_(no text)_'}"
            )
            file = row["media_path"] if row["media_path"] and os.path.exists(row["media_path"]) else None
            await notify(text, file=file)


async def main():
    db.init_db()
    await client.start()
    me = await client.get_me()
    log.info("Logged in as %s (id=%s)", me.username or me.first_name, me.id)
    log.info("Watching for edits/deletes and self-destructing media...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
