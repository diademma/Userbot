# app.py
import os
import sys
import glob
import logging
import asyncio
import importlib.util
from telethon import TelegramClient
from telethon.sessions import StringSession

from core.config import API_ID, API_HASH, SESSION_STRING, USERBOT_NAME, VERSION
from core.db import init_db, mem_logs, is_authorized

# Настройка системного логгера
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(sys.stdout))
logger.addHandler(mem_logs)

# Инициализация Telethon
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

def load_modules():
    """Автоматическая загрузка всех модулей из папки modules/"""
    modules_dir = os.path.join(os.path.dirname(__file__), "modules")
    if not os.path.exists(modules_dir):
        os.makedirs(modules_dir)
        logging.warning("📁 Папка 'modules/' была создана. Добавьте туда модули.")
        return

    module_files = glob.glob(os.path.join(modules_dir, "*.py"))
    loaded_count = 0

    for file_path in module_files:
        module_name = os.path.splitext(os.path.basename(file_path))[0]
        if module_name.startswith("_"):
            continue  # Пропускаем служебные файлы вроде __init__.py

        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # 1. Новый стандарт: ищем функцию register(client)
            if hasattr(module, "register"):
                module.register(client)
                logging.info(f"🧩 Модуль [{module_name}] успешно зарегистрирован.")
                loaded_count += 1
            # 2. Обратная совместимость со старыми модулями media_studio / quote_stickers
            elif hasattr(module, "register_media_studio"):
                module.register_media_studio(client, is_authorized_cb=is_authorized)
                logging.info(f"🎛️ Media Studio [{module_name}] зарегистрирована.")
                loaded_count += 1
            elif hasattr(module, "register_quote_stickers"):
                module.register_quote_stickers(client, is_authorized_cb=is_authorized)
                logging.info(f"✨ Quote Stickers [{module_name}] зарегистрирован.")
                loaded_count += 1
            else:
                logging.warning(f"⚠️ В модуле [{module_name}] не найдена функция register(client).")

        except Exception as e:
            logging.error(f"❌ Ошибка загрузки модуля [{module_name}]: {e}")

    logging.info(f"🚀 Загружено модулей: {loaded_count}")

async def main():
    if not all([API_ID, API_HASH, SESSION_STRING]):
        logging.critical("❌ ОШИБКА: Заполните переменные API_ID, API_HASH, SESSION_STRING в Secrets!")
        return

    logging.info("Инициализация базы данных...")
    init_db()

    await client.start()
    
    # Подгружаем все модули
    load_modules()

    me = await client.get_me()
    logging.info(f"🪐 {USERBOT_NAME} {VERSION} запущен! Аккаунт-воркер: {me.first_name} (@{me.username})")
    await client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
