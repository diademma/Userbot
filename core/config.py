# core/config.py
import os

USERBOT_NAME = "𝗣𝗿𝗼𝘅𝗶𝗺𝗮 LLEHTABPA"
VERSION = "v5.0"

# Telegram API
API_ID = int(os.getenv("API_ID", 0)) if os.getenv("API_ID") else None
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

# Бот-симбиот для инлайна (пригодится на следующем этапе)
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ID создателя (Кукловода)
OWNER_ID = int(os.getenv("OWNER_ID", 5421909121))

# База данных
DB_NAME = "sniper_memory_v3.db"
