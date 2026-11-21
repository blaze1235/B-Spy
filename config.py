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

# Telegram user IDs allowed to use /admin. Comma-separated to allow more than one.
ADMIN_TELEGRAM_IDS = {
    int(x) for x in os.environ.get("ADMIN_TELEGRAM_IDS", "1398614118").split(",") if x.strip()
}
