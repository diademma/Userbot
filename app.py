# app.py
import sys
import logging
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession, MemorySession

from core.config import API_ID, API_HASH, SESSION_STRING, BOT_TOKEN, USERBOT_NAME, VERSION
from core.db import init_db, mem_logs
from core.loader import load_all_modules, init_hot_reload
from core.inline import init_inline

# Логирование
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.addHandler(mem_logs)

# 1. Воркер (Юзербот)
user = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# 2. Симбиот (Инлайн-бот в памяти)
bot = TelegramClient(MemorySession(), API_ID, API_HASH) if BOT_TOKEN else None

async def main():
    if not all([API_ID, API_HASH, SESSION_STRING]):
        logging.critical("❌ ОШИБКА: Заполните API_ID, API_HASH, SESSION_STRING в Secrets!")
        return

    # Запуск базы
    init_db()

    # Запуск юзербота
    await user.start()
    me = await user.get_me()
    logging.info(f"🪐 {USERBOT_NAME} {VERSION} — Воркер: {me.first_name} (@{me.username})")

    # Запуск инлайн-бота
    if bot and BOT_TOKEN:
        await bot.start(bot_token=BOT_TOKEN)
        bot_me = await bot.get_me()
        logging.info(f"🤖 Инлайн-Симбиот: @{bot_me.username}")
    else:
        logging.warning("⚠️ BOT_TOKEN не задан. Инлайн-кнопки отключены.")

    # Подключаем ядро: инлайн-меню и хот-релоад
    init_inline(user, bot)
    init_hot_reload(user, bot)

    # Загружаем ТОЛЬКО пользовательские модули из папки modules/
    load_all_modules(user, bot)

    # Держим соединение
    tasks = [user.run_until_disconnected()]
    if bot and BOT_TOKEN:
        tasks.append(bot.run_until_disconnected())

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
