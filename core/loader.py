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
    
    # 1. Проверяем обязательные строковые поля
    for field in REQUIRED_METADATA:
        val = getattr(module, field, None)
        if not val or not isinstance(val, str) or not val.strip():
            # Допускаем альтернативное имя DESCRIPTION для поля COMMANDS
            if field == "COMMANDS" and getattr(module, "DESCRIPTION", None):
                continue
            missing.append(field)

    if missing:
        return False, f"Отсутствуют обязательные метаданные: {', '.join(missing)}"

    # 2. Проверяем наличие вызываемой функции register
    if not hasattr(module, "register") or not callable(getattr(module, "register")):
        return False, "Отсутствует обязательная функция `register(user)`"

    return True, ""

def load_single_module(file_path: str, user, bot=None, silent: bool = False) -> bool:
    """Загрузка модуля со строгой валидацией API"""
    module_name = os.path.splitext(os.path.basename(file_path))[0]
    if module_name.startswith("_"):
        return False

    importlib.invalidate_caches()

    # Очищаем поврежденный кэш перед импортом
    if module_name in sys.modules:
        mod = sys.modules[module_name]
        if not hasattr(mod, "register") or not hasattr(mod, "TITLE"):
            sys.modules.pop(module_name, None)

    try:
        if module_name in sys.modules:
            module = importlib.reload(sys.modules[module_name])
        else:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

        # СТРОГАЯ ВАЛИДАЦИЯ API
        is_valid, error_reason = validate_module_api(module, module_name)
        if not is_valid:
            sys.modules.pop(module_name, None)
            LAST_LOAD_ERRORS[module_name] = error_reason
            if not silent:
                logging.warning(f"⚠️ Модуль [{module_name}] отклонен ядром: {error_reason}")
            return False

        # РЕГИСТРАЦИЯ ХЭНДЛЕРОВ
        sig = inspect.signature(module.register)
        if len(sig.parameters) >= 2 and bot:
            module.register(user, bot)
        else:
            module.register(user)

        LOADED_MODULES[module_name] = module
        LAST_LOAD_ERRORS.pop(module_name, None)
        logging.info(f"🧩 Модуль [{module_name}] успешно прошел валидацию и подключен.")
        return True

    except ModuleNotFoundError as e:
        sys.modules.pop(module_name, None)
        if file_path not in PENDING_MODULES:
            PENDING_MODULES.append(file_path)
            if not silent:
                logging.info(f"⏳ Модуль [{module_name}] ожидает либу: {e.name}")
        return False

    except Exception as e:
        sys.modules.pop(module_name, None)
        LAST_LOAD_ERRORS[module_name] = str(e)
        logging.error(f"❌ Ошибка выполнения кода в [{module_name}]: {e}")
        return False

async def background_modules_watcher(user, bot=None):
    """Фоновый воркер ожидания библиотек"""
    await asyncio.sleep(4)
    retries = 35

    while PENDING_MODULES and retries > 0:
        await asyncio.sleep(4)
        retries -= 1

        for file_path in list(PENDING_MODULES):
            if load_single_module(file_path, user, bot, silent=True):
                PENDING_MODULES.remove(file_path)
                m_name = os.path.splitext(os.path.basename(file_path))[0]
                logging.info(f"🎉 Фоновый модуль [{m_name}] успешно подключен!")

    if not PENDING_MODULES:
        logging.info("✅ Все фоновые библиотеки и модули успешно загружены!")
        while WAITING_NOTIFICATIONS:
            msg = WAITING_NOTIFICATIONS.pop(0)
            try:
                await msg.edit("✅ Все ресурсы загружены! Можете использовать команду.")
            except Exception:
                pass
    else:
        failed = [os.path.splitext(os.path.basename(p))[0] for p in PENDING_MODULES]
        logging.error(f"⚠️ Не удалось запустить модули: {failed}")
        while WAITING_NOTIFICATIONS:
            msg = WAITING_NOTIFICATIONS.pop(0)
            try:
                await msg.edit(f"❌ Не удалось загрузить ресурсы для модулей: `{', '.join(failed)}`")
            except Exception:
                pass

def load_all_modules(user, bot=None):
    if not os.path.exists(MODULES_DIR):
        os.makedirs(MODULES_DIR)
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
            "reload", "релоад", "spy"
        ]

        if cmd in base_cmds:
            return

        if PENDING_MODULES:
            wait_msg = await event.reply("⏳ Подождите немного, загружаю ресурсы...")
            WAITING_NOTIFICATIONS.append(wait_msg)

    @user.on(events.NewMessage(pattern=r"^sudo\s+(reload|релоад)(\s+.*)?$"))
    async def reload_handler(event):
        if not await is_authorized(event):
            return

        parts = event.raw_text.split()
        target = parts[1].lower() if len(parts) > 1 else ""

        if not target or target in ["all", "все"]:
            load_all_modules(user, bot)
            return await event.reply("🔄 Все модули склада повторно провалидированы и перезагружены!")

        target_name = target.replace(".py", "")
        file_path = os.path.join(MODULES_DIR, f"{target_name}.py")

        if not os.path.exists(file_path):
            return await event.reply(f"❌ Файл `modules/{target_name}.py` не найден на складе.")

        if load_single_module(file_path, user, bot, silent=False):
            await event.reply(f"✅ Модуль `{target_name}` успешно прошел проверку API и перезагружен!")
        else:
            err = LAST_LOAD_ERRORS.get(target_name, "Ошибка валидации API")
            await event.reply(f"⚠️ Ошибка загрузки `{target_name}`:\n`{err}`")

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

        status = await event.reply(f"⏳ Скачиваю и проверяю API модуля `{file_name}`...")
        save_path = os.path.join(MODULES_DIR, file_name)
        module_name = file_name[:-3]

        try:
            # Скачиваем через file= (Telethon)
            await user.download_media(target, file=save_path)
            
            # Проверяем соответствие API
            success = load_single_module(save_path, user, bot, silent=False)
            if not success:
                err = LAST_LOAD_ERRORS.get(module_name, "Неизвестная ошибка проверки API")
                # Удаляем с диска бракованный файл, чтобы не засорять склад
                if os.path.exists(save_path):
                    os.remove(save_path)
                return await status.edit(f"❌ **Модуль отклонен ядром:**\n`{err}`")

            await status.edit(f"✅ Модуль `{module_name}` соответствует API и **активен в памяти**!")

            # Авто-пуш только валидных файлов
            subprocess.run([
                "bash", "-c",
                f'git config user.name "github-actions[bot]" && '
                f'git config user.email "41898282+github-actions[bot]@users.noreply.github.com" && '
                f'git add {save_path} && '
                f'git commit -m "feat: add validated module {module_name} [skip ci]" && '
                f'git push'
            ])
            logging.info(f"Валидный модуль {file_name} сохранен в Git.")

        except Exception as e:
            logging.error(f"Ошибка Hot-Reload: {e}")
            await status.edit(f"❌ Критическая ошибка:\n`{e}`")
