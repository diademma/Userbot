# core/loader.py
import os
import sys
import glob
import asyncio
import inspect
import logging
import subprocess
import importlib
import importlib.util
from telethon import events
from core.db import is_authorized

MODULES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "modules"))

# Реестры состояния
LOADED_MODULES = {}
PENDING_MODULES = []
WAITING_NOTIFICATIONS = []  # Сообщения пользователю, которые ждут смены на "Все ресурсы загружены"

def get_loaded_modules():
    """Возвращает словарь загруженных модулей"""
    return LOADED_MODULES

def get_pending_modules():
    """Возвращает список имен модулей, которые еще ждут либы"""
    return [os.path.splitext(os.path.basename(p))[0] for p in PENDING_MODULES]

def load_single_module(file_path: str, user, bot=None, silent: bool = False) -> bool:
    """Загрузка модуля. Если silent=True — не спамит в логи при ожидании"""
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    if module_name.startswith("_"):
        return False

    try:
        if module_name in sys.modules:
            module = importlib.reload(sys.modules[module_name])
        else:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

        success = False
        if hasattr(module, "register"):
            sig = inspect.signature(module.register)
            if len(sig.parameters) >= 2 and bot:
                module.register(user, bot)
            else:
                module.register(user)
            success = True

        elif hasattr(module, "register_media_studio"):
            module.register_media_studio(user, is_authorized_cb=is_authorized)
            success = True

        elif hasattr(module, "register_quote_stickers"):
            module.register_quote_stickers(user, is_authorized_cb=is_authorized)
            success = True

        if success:
            LOADED_MODULES[module_name] = module
            logging.info(f"🧩 Модуль [{module_name}] успешно подключен.")
            return True

        return False

    except ModuleNotFoundError:
        # Если тихий режим выключен (первый запуск) — добавляем в очередь
        if file_path not in PENDING_MODULES:
            PENDING_MODULES.append(file_path)
            if not silent:
                logging.info(f"⏳ Модуль [{module_name}] поставлен в очередь (ждет фоновые либы)...")
        return False

    except Exception as e:
        logging.error(f"❌ Ошибка в модуле [{module_name}]: {e}")
        return False

async def background_modules_watcher(user, bot=None):
    """Тихий фоновый наблюдатель: ждет завершения pip без спама в логи"""
    await asyncio.sleep(4)
    retries = 35  # Проверяем в течение ~2.5 минут

    while PENDING_MODULES and retries > 0:
        await asyncio.sleep(4)
        retries -= 1

        for file_path in list(PENDING_MODULES):
            # Проверяем тихо (silent=True), чтобы не засорять консоль
            if load_single_module(file_path, user, bot, silent=True):
                PENDING_MODULES.remove(file_path)
                m_name = os.path.splitext(os.path.basename(file_path))[0]
                logging.info(f"🎉 Фоновый модуль [{m_name}] успешно подхвачен на лету!")

    # МАРКЕР: Срабатывает один раз, когда все фоновые ресурсы загрузились
    if not PENDING_MODULES:
        logging.info("✅ Все фоновые библиотеки и модули загружены!")

        # Редактируем все смс, где бот просил пользователя подождать
        while WAITING_NOTIFICATIONS:
            msg = WAITING_NOTIFICATIONS.pop(0)
            try:
                await msg.edit("✅ Все ресурсы загружены!")
            except Exception:
                pass

def load_all_modules(user, bot=None):
    """Стартовая загрузка при запуске ядра"""
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
        return

    files = glob.glob(os.path.join(MODULES_DIR, "*.py"))
    loaded = 0
    for f in files:
        if load_single_module(f, user, bot, silent=False):
            loaded += 1

    logging.info(f"🚀 Сходу запущено модулей: {loaded}")

    # Если есть модули в ожидании — запускаем тихий фоновый воркер
    if PENDING_MODULES:
        asyncio.create_task(background_modules_watcher(user, bot))

def init_hot_reload(user, bot=None):
    """Регистрация команд reload, load и перехватчика ранних запросов"""

    # 1. ПЕРЕХВАТЧИК РАННИХ КОМАНД
    @user.on(events.NewMessage(pattern=r"^sudo\s+(.+)"))
    async def early_command_interceptor(event):
        if not await is_authorized(event):
            return

        parts = event.raw_text.split()
        cmd = parts[1].lower() if len(parts) > 1 else ""

        # Список команд, которые уже умеет обрабатывать Sniper (не перехватываем их)
        base_cmds = [
            "спам", "ad", "реклама", "бан", "+искл", "-искл", "исклы", 
            "+бан", "-бан", "баны", "+рег", "-рег", "регексы", "+дов", 
            "-дов", "доверенные", "рп", "инфо", "лог", "logs", "load", 
            "reload", "релоад"
        ]

        if cmd in base_cmds:
            return  # Передаем управление дальше модулю Sniper

        # Если пользователь вызвал команду модуля, который еще в процессе фоновой установки
        if PENDING_MODULES:
            wait_msg = await event.reply("⏳ Подождите немного, загружаю ресурсы...")
            WAITING_NOTIFICATIONS.append(wait_msg)

    # 2. РЕЛОАД МОДУЛЕЙ
    @user.on(events.NewMessage(pattern=r"^sudo\s+(reload|релоад)(\s+.*)?$"))
    async def reload_handler(event):
        if not await is_authorized(event):
            return

        parts = event.raw_text.split()
        target = parts[1].lower() if len(parts) > 1 else ""

        if not target or target in ["all", "все"]:
            load_all_modules(user, bot)
            return await event.reply("🔄 Все модули из папки `modules/` перезагружены!")

        target_name = target.replace(".py", "")
        file_path = os.path.join(MODULES_DIR, f"{target_name}.py")

        if not os.path.exists(file_path):
            return await event.reply(f"❌ Файл `modules/{target_name}.py` не найден.")

        if load_single_module(file_path, user, bot, silent=False):
            await event.reply(f"✅ Модуль `{target_name}` успешно перезагружен в памяти!")
        else:
            await event.reply(f"⚠️ Ошибка перезагрузки `{target_name}`. Проверь логи: `sudo лог`")

    # 3. ЗАГРУЗКА НОВОГО МОДУЛЯ
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
            await user.download_media(target, file_name=save_path)
            success = load_single_module(save_path, user, bot, silent=False)
            if not success:
                return await status.edit(f"⚠️ Модуль скачан, но не найден метод регистрации.")

            await status.edit(f"✅ Модуль `{file_name[:-3]}` **активен без перезагрузки**!")

            subprocess.run([
                "bash", "-c",
                f'git config user.name "github-actions[bot]" && '
                f'git config user.email "41898282+github-actions[bot]@users.noreply.github.com" && '
                f'git add {save_path} && '
                f'git commit -m "feat: auto-add module {file_name[:-3]} [skip ci]" && '
                f'git push'
            ])
            logging.info(f"Модуль {file_name} сохранен в Git.")

        except Exception as e:
            logging.error(f"Ошибка Hot-Reload: {e}")
            await status.edit(f"❌ Ошибка внедрения модуля:\n`{e}`")
