"""
Run this once, interactively, on your own machine to log in with your
Telegram account and generate a session string.

    python login.py

It will ask for your phone number, the login code Telegram sends you, and
your 2FA password if you have one set. It then prints a session string -
copy that into .env as SESSION_STRING.

WARNING: that string is equivalent to full access to your Telegram account
(like a password). Never share it or commit it to git.
"""
import os

from dotenv import load_dotenv
from telethon.sessions import StringSession
from telethon.sync import TelegramClient

load_dotenv()

api_id = int(os.environ["API_ID"])
api_hash = os.environ["API_HASH"]

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nLogin successful. Your session string (put this in .env as SESSION_STRING):\n")
    print(client.session.save())
    print("\nKeep it secret - anyone with this string can log in as you.\n")
