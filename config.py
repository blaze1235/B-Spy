import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "spy.db")
MEDIA_DIR = os.environ.get("MEDIA_DIR", "saved_media")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PORT = int(os.environ.get("PORT", "8080"))
