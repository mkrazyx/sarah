import os
import logging

env = os.environ.get

BOT_TOKEN = env("BOT_TOKEN", "8191103559:AAG-6navNOYM0pSAZqDYzzwsLBSXS-EsRHI")
APP_ID = int(env("APP_ID", "1736204"))
API_HASH = env("API_HASH", "890d40e0f91a4de32dec2965444b2cbe")
CHANNEL_ID = int(env("CHANNEL_ID", "-1001205507869"))
OWNER_ID = int(env("OWNER_ID", "1058015838"))
PORT = int(env("PORT", "8080"))
DB_URI = env("DATABASE_URL", "mongodb+srv://herostore:herostore@herostore.ywtvule.mongodb.net/?appName=herostore")
DB_NAME = env("DATABASE_NAME", "filestore")
SHORTLINK_URL = env("SHORTLINK_URL", "nowshort.com")
SHORTLINK_API = env("SHORTLINK_API", "c576b9cdd34ceb572a8df1f57eabf7c11efbda3a")
FORCE_SUB_CHANNEL = int(env("FORCE_SUB_CHANNEL", "0"))
WORKERS = int(env("TG_BOT_WORKERS", "4"))
CUSTOM_CAPTION = env("CUSTOM_CAPTION")
START_MSG = env("START_MESSAGE", "<b>🌟 Hello {first},\n\nI'm A File Store Bot 🤖 designed to provide files 📁 and links 🔗</b>")
FORCE_MSG = env("FORCE_SUB_MESSAGE", "<b>🌟 Join Our Main Channel &amp; Click Try Again ♻</b>")
ADMINS = list(dict.fromkeys(
    [int(x) for x in env("ADMINS", "1058015838").replace(",", " ").split()] + [OWNER_ID]))  # no duplicates

logging.basicConfig(level=logging.INFO, format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s")
logging.getLogger("pyrogram").setLevel(logging.WARNING)
