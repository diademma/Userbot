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

# Гарантируем, что папка modules всегда есть в системных путях Python
if MODULES_DIR not in sys.path:
    sys.path.insert(0, MODULES_DIR)

LOADED_MODULES = {}
PENDING_MODULES = []
WAITING_NOTIFICATIONS = []
LAST_LOAD_ERRORS = {}

REQUIRED_METADATA = ["TITLE", "BANNER", "COMMANDS"]

def get_loaded_modules():
    return LOADED_MODULES

def get_pending_modules():
    return [os.path.splitext(os.path.basename(p))[0] for p in PENDING_MODULES]

def validate_module_api(module, module_name: str) -> tuple[bool, str]:
    """Проверка наличия всех обязательных метаданных и точки входа"""
    missing = []
    
    # 1. Проверяем строковые поля манифеста
    for field in REQUIRED_METADATA:
        val = getattr(module, field, None)
        if not val or not isinstance(val, str) or not val.strip():
            # Допускаем альтернативное имя DESCRIPTION вместо COMMANDS
            if field == "COMMANDS" and getattr(module, "DESCRIPTION", None):
                continue
            missing.append(field)

    if missing:
        return False, f"Отсутствуют обязательные метаданные манифеста: {', '.join(missing)}"

    # 2. Проверяем наличие точки входа register
    if not hasattr(module, "register") or not callable(getattr(module, "register")):
        return False, "Отсутствует обязательная функция точки входа `register(user)`"

    return True, ""

def load_single_module(file_path: str, user, bot=None, silent: bool = False) -> bool:
    """Загрузка и горячая замена модуля в ОЗУ со строгой валидацией API"""
    if not os.path.isfile(file_path):
        LAST_LOAD_ERRORS[os.path.basename(file_path)] = "Файл не найден на диске"
        return False

    module_name = os.path.splitext(os.path.basename(file_path))[0]
    if module_name.startswith("_"):
        return False

    importlib.invalidate_caches()

    # Очищаем старые ссылки перед сборкой нового контекста
    sys.modules.pop(module_name, None)

    try:
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            LAST_LOAD_ERRORS[module_name] = "Не удалось скомпилировать спецификацию модуля"
            return False

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        # Валидация манифеста API ядра
        is_valid, error_reason = validate_module_api(module, module_name)
        if not is_valid:
            sys.modules.pop(module_name, None)
            LAST_LOAD_ERRORS[module_name] = error_reason
            if not silent:
                logging.warning(f"⚠️ Модуль [{module_name}] отклонен ядром: {error_reason}")
            return False

        # Регистрация хэндлеров события
        sig = inspect.signature(module.register)
        params_count = len(sig.parameters)

        if params_count >= 2 and bot:
            module.register(user, bot)
        else:
            module.register(user)

        LOADED_MODULES[module_name] = module
        LAST_LOAD_ERRORS.pop(module_name, None)
        logging.info(f"🧩 Модуль [{module_name}] успешно подключен к ядру.")
        return True

    except ModuleNotFoundError as e:
        sys.modules.pop(module_name, None)
        LAST_LOAD_ERRORS[module_name] = f"Не найдена библиотека: `{e.name}`"
        if file_path not in PENDING_MODULES:
            PENDING_MODULES.append(file_path)
            if not silent:
                logging.info(f"⏳ Модуль [{module_name}] ожидает либу: {e.name}")
        return False

    except SyntaxError as e:
        sys.modules.pop(module_name, None)
        LAST_LOAD_ERRORS[module_name] = f"Синтаксическая ошибка в строке {e.lineno}: {e.msg}"
        logging.error(f"❌ Ошибка синтаксиса в [{module_name}]: {e}")
        return False

    except Exception as e:
        sys.modules.pop(module_name, None)
        LAST_LOAD_ERRORS[module_name] = f"{type(e).__name__}: {str(e)}"
        logging.error(f"❌ Ошибка инициализации [{module_name}]: {e}")
        return False

async def background_modules_watcher(user, bot=None):
    """Фоновый воркер ожидания внешних зависимостей"""
    await asyncio.sleep(4)
    retries = 35

    while PENDING_MODULES and retries > 0:
        await asyncio.sleep(4)
        retries -= 1

        for file_path in list(PENDING_MODULES):
            if load_single_module(file_path, user, bot, silent=True):
                PENDING_MODULES.remove(file_path)
                m_name = os.path.splitext(os.path.basename(file_path))[0]
                logging.info(f"🎉 Модуль [{m_name}] дождался ресурсов и подключен!")

    if not PENDING_MODULES:
        logging.info("✅ Все фоновые модули успешно загружены!")
        while WAITING_NOTIFICATIONS:
            msg = WAITING_NOTIFICATIONS.pop(0)
            try:
                await msg.edit("✅ Все ресурсы загружены! Команды готовы к работе.")
            except Exception:
                pass
    else:
        failed = [os.path.splitext(os.path.basename(p))[0] for p in PENDING_MODULES]
        logging.error(f"⚠️ Не удалось запустить модули: {failed}")
        while WAITING_NOTIFICATIONS:
            msg = WAITING_NOTIFICATIONS.pop(0)
            try:
                await msg.edit(f"❌ Не удалось загрузить модули: `{', '.join(failed)}`")
            except Exception:
                pass

def load_all_modules(user, bot=None):
    """Инициализация модулей со склада при старте бота"""
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR, exist_ok=True)
        return

    files = glob.glob(os.path.join(MODULES_DIR, "*.py"))
    loaded = 0
    for f in files:
        if load_single_module(f, user, bot, silent=False):
            loaded += 1

    logging.info(f"🚀 Сходу запущено валидных модулей: {loaded}")

    if PENDING_MODULES:
        asyncio.create_task(background_modules_watcher(user, bot))

def init_hot_reload(user, bot=None):
    """Слушатели команд управления модулями (Hot Reload)"""

    @user.on(events.NewMessage(pattern=r"^sudo\s+(.+)"))
    async def early_command_interceptor(event):
        if not await is_authorized(event):
            return

        parts = event.raw_text.split()
        cmd = parts[1].lower() if len(parts) > 1 else ""

        base_cmds = [
            "спам", "ad", "реклама", "бан", "+искл", "-искл", "исклы", 
            "+бан", "-бан", "баны", "+рег", "-рег", "регексы", "+дов", 
            "-дов", "доверенные", "рп", "инфо", "лог", "logs", "load", 
            "reload", "релоад", "spy", "кроко", "croco"
        ]

        if cmd in base_cmds:
            return

        if PENDING_MODULES:
            wait_msg = await event.reply("⏳ Подождите немного, подтягиваю зависимости...")
            WAITING_NOTIFICATIONS.append(wait_msg)

    @user.on(events.NewMessage(pattern=r"^sudo\s+(reload|релоад)(\s+.*)?$"))
    async def reload_handler(event):
        if not await is_authorized(event):
            return

        parts = event.raw_text.split()
        target = parts[2].lower() if len(parts) > 2 else (parts[1].lower() if len(parts) > 1 and parts[1].lower() not in ["reload", "релоад"] else "")

        if not target or target in ["all", "все"]:
            load_all_modules(user, bot)
            return await event.reply("🔄 Все модули склада повторно проверены и обновлены в ОЗУ!")

        target_name = target.replace(".py", "")
        file_path = os.path.join(MODULES_DIR, f"{target_name}.py")

        if not os.path.exists(file_path):
            return await event.reply(f"❌ Файл `modules/{target_name}.py` не найден на диске.")

        if load_single_module(file_path, user, bot, silent=False):
            await event.reply(f"✅ Модуль `{target_name}` успешно перезагружен!")
        else:
            err = LAST_LOAD_ERRORS.get(target_name, "Неизвестная ошибка")
            await event.reply(f"⚠️ Ошибка перезагрузки `{target_name}`:\n`{err}`")

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
            return await event.reply("❌ Файл должен иметь расширение `.py`!")

        status = await event.reply(f"⏳ Анализирую и интегрирую модуль `{file_name}`...")
        
        # Гарантируем чистое имя файла без мусора
        clean_file_name = os.path.basename(file_name)
        save_path = os.path.join(MODULES_DIR, clean_file_name)
        module_name = clean_file_name[:-3]

        try:
            # Скачиваем файл в папку modules/
            await user.download_media(target, file=save_path)

            # Пробуем инициализировать модуль
            success = load_single_module(save_path, user, bot, silent=False)
            if not success:
                err = LAST_LOAD_ERRORS.get(module_name, "Не удалось верифицировать манифест")
                if os.path.exists(save_path):
                    os.remove(save_path)
                return await status.edit(f"❌ **Модуль отклонен ядром:**\n`{err}`")

            await status.edit(f"✅ Модуль `{module_name}` успешно верифицирован и **активен в памяти**!")

            # Автосохранение в репозиторий Git
            try:
                subprocess.run([
                    "bash", "-c",
                    f'git config user.name "github-actions[bot]" && '
                    f'git config user.email "41898282+github-actions[bot]@users.noreply.github.com" && '
                    f'git add {save_path} && '
                    f'git commit -m "feat: add module {module_name} [skip ci]" && '
                    f'git push'
                ], timeout=15)
                logging.info(f"Модуль {clean_file_name} успешно сохранен в Git.")
            except Exception as git_err:
                logging.warning(f"Git-синхронизация пропущена: {git_err}")

        except Exception as e:
            logging.error(f"Критическая ошибка загрузчика: {e}")
            await status.edit(f"❌ Критическая ошибка:\n`{e}`")
