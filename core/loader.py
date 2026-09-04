# core/loader.py
import os
import sys
import glob
import inspect
import logging
import subprocess
import importlib.util
from telethon import events
from core.db import is_authorized

MODULES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "modules"))

def load_single_module(file_path: str, user, bot=None) -> bool:
    """Загрузка одного модуля в память"""
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    if module_name.startswith("_"):
        return False

    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # 1. Новый формат register(user, bot) или register(user)
        if hasattr(module, "register"):
            sig = inspect.signature(module.register)
            if len(sig.parameters) >= 2 and bot:
                module.register(user, bot)
            else:
                module.register(user)
            logging.info(f"🧩 Модуль [{module_name}] успешно подключен.")
            return True

        # 2. Совместимость с Media Studio и Quote Stickers
        elif hasattr(module, "register_media_studio"):
            module.register_media_studio(user, is_authorized_cb=is_authorized)
            logging.info(f"🎛️ Media Studio [{module_name}] подключена.")
            return True
        elif hasattr(module, "register_quote_stickers"):
            module.register_quote_stickers(user, is_authorized_cb=is_authorized)
            logging.info(f"✨ Quote Stickers [{module_name}] подключен.")
            return True
        else:
            logging.warning(f"⚠️ В модуле [{module_name}] нет функции register(user).")
            return False

    except Exception as e:
        logging.error(f"❌ Ошибка загрузки модуля [{module_name}]: {e}")
        return False

def load_all_modules(user, bot=None):
    """Сканирование всей папки modules/"""
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
        return

    files = glob.glob(os.path.join(MODULES_DIR, "*.py"))
    loaded = 0
    for f in files:
        if load_single_module(f, user, bot):
            loaded += 1
    logging.info(f"🚀 Всего загружено модулей: {loaded}")

def init_hot_reload(user, bot=None):
    """Регистрация команды sudo load для загрузки модулей на лету"""
    @user.on(events.NewMessage(pattern=r"^sudo\s+(load|загрузить)$"))
    async def load_module_handler(event):
        if not await is_authorized(event):
            return

        if not event.is_reply:
            return await event.reply("❌ Ответь командой `sudo load` на `.py` файл модуля!")

        target = await event.get_reply_message()
        if not target.document:
            return await event.reply("❌ Это не документ!")

        file_name = None
        for attr in target.document.attributes:
            if hasattr(attr, "file_name"):
                file_name = attr.file_name
                break

        if not file_name or not file_name.endswith(".py"):
            return await event.reply("❌ Файл должен заканчиваться на `.py`!")

        status = await event.reply(f"⏳ Скачиваю и внедряю `{file_name}` в ядро...")
        save_path = os.path.join(MODULES_DIR, file_name)

        try:
            # 1. Скачиваем прямо в modules/
            await user.download_media(target, file_name=save_path)

            # 2. Внедряем в рантайм
            success = load_single_module(save_path, user, bot)
            if not success:
                return await status.edit(f"⚠️ Модуль скачан, но не найден метод `register(user)`.")

            await status.edit(f"✅ Модуль `{file_name[:-3]}` **активен без перезагрузки**!")

            # 3. Авто-пуш в GitHub
            subprocess.run([
                "bash", "-c",
                f'git config user.name "github-actions[bot]" && '
                f'git config user.email "41898282+github-actions[bot]@users.noreply.github.com" && '
                f'git add {save_path} && '
                f'git commit -m "feat: auto-add module {file_name[:-3]} [skip ci]" && '
                f'git push'
            ])
            logging.info(f"Модуль {file_name} успешно сохранен в Git.")

        except Exception as e:
            logging.error(f"Ошибка Hot-Reload: {e}")
            await status.edit(f"❌ Ошибка внедрения модуля:\n`{e}`")
