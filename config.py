import os

from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ.get("SESSION_STRING", "")

NOTIFY_CHAT = os.environ.get("NOTIFY_CHAT", "me")
MEDIA_DIR = os.environ.get("MEDIA_DIR", "saved_media")
DB_PATH = os.environ.get("DB_PATH", "spy.db")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
