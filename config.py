import os

env = os.environ.get

BOT_TOKEN = env("BOT_TOKEN", "8191103559:AAG-6navNOYM0pSAZqDYzzwsLBSXS-EsRHI")
DB_URI = env("DATABASE_URL", "mongodb+srv://herostore:herostore@herostore.ywtvule.mongodb.net/?appName=herostore")
DB_NAME = env("DATABASE_NAME", "jisoo")
SHORTLINK_URL = env("SHORTLINK_URL", "nowshort.com")
SHORTLINK_API = env("SHORTLINK_API", "c576b9cdd34ceb572a8df1f57eabf7c11efbda3a")
OWNER_ID = int(env("OWNER_ID", "1058015838"))
ADMINS = list(dict.fromkeys([int(x) for x in env("ADMINS", "1058015838").replace(",", " ").split()] + [OWNER_ID]))
FORCE_SUB_CHANNEL = int(env("FORCE_SUB_CHANNEL", "0"))

START_MSG = env("START_MESSAGE", "<b>🌟 Hello {first},\n\nI'm A File Store Bot 🤖 designed to provide files 📁 and links 🔗</b>")
FORCE_MSG = env("FORCE_SUB_MESSAGE", "<b>🌟 Join Our Main Channel &amp; Click Try Again ♻</b>")

APP_ID = int(env("APP_ID", "1736204"))
API_HASH = env("API_HASH", "890d40e0f91a4de32dec2965444b2cbe")
PORT = int(env("PORT", "8080"))
WORKERS = int(env("TG_BOT_WORKERS", "4"))
